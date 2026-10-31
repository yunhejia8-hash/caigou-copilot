import io
import json
import math
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import app

class ProcurementTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=app.DB
        app.DB=Path(self.tmp.name)/'test.db'
        app.init_db()
        self.p={'applicant':'张测试','department':'生产部','purpose':'生产线月度领用，每人5盒，共20盒','material_id':'PPE-001','quantity':20,'budget':600,'warehouse':'一号仓','needed_by':str(app.today()+timedelta(days=14))}
    def tearDown(self):
        app.DB=self.old;self.tmp.cleanup()
    def evaluate(self,**kwargs):
        with app.connect() as c:return app.evaluate(c,{**self.p,**kwargs})
    def test_stock_satisfies(self):
        r=self.evaluate();self.assertEqual(r['available'],80);self.assertEqual(r['purchase_quantity'],0);self.assertEqual(r['status'],'无需新增采购')
    def test_reserved_and_incoming_and_cross_warehouse(self):
        r=self.evaluate(material_id='PKG-001',quantity=500,budget=2000)
        self.assertEqual((r['available'],r['incoming'],r['purchase_quantity'],r['estimated_total']),(120,100,280,980))
        self.assertEqual(r['other_warehouses'][0]['available'],80)
        self.assertEqual(r['status'],'需部门复核')
    def test_late_orders_excluded(self):
        r=self.evaluate(material_id='SPR-001',quantity=10)
        self.assertEqual(r['incoming'],0);self.assertEqual(r['purchase_quantity'],8)
    def test_overdue_orders_excluded(self):
        with app.connect() as c:c.execute('UPDATE orders SET due=?',(str(app.today()-timedelta(days=1)),))
        self.assertEqual(self.evaluate(material_id='PKG-001')['incoming'],0)
    def test_stale_stock_excluded(self):
        r=self.evaluate(material_id='RAW-001',warehouse='二号仓',quantity=50,budget=20000)
        self.assertEqual(r['available'],0);self.assertEqual(r['purchase_quantity'],20)
        self.assertTrue(any(c['dimension']=='库存时效' for c in r['checks']))
    def test_new_material_pending_review(self):
        r=self.evaluate(material_id='',material_name='检测相机',spec='500万像素',unit='台',quantity=2,estimated_price=4200,budget=10000)
        self.assertEqual(r['estimated_total'],8400);self.assertEqual(r['status'],'需部门复核')
    def test_missing_blocks_confirmation(self):
        r=app.assess({**self.p,'purpose':''})
        self.assertEqual(r['status'],'待补充资料')
        with self.assertRaises(ValueError):app.confirm(r['id'])
    def test_missing_zero_budget_distinction(self):
        self.assertIn('本次预算上限',self.evaluate(budget='')['missing'])
        self.assertNotIn('本次预算上限',self.evaluate(budget=0)['missing'])
    def test_invalid_numbers(self):
        for value in [-1,0,1.5,'abc',float('nan'),float('inf'),True]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.evaluate(quantity=value)
    def test_invalid_date(self):
        for value in ['2026-02-30',str(app.today()-timedelta(days=1))]:
            with self.assertRaises(ValueError):self.evaluate(needed_by=value)
    def test_unknown_material(self):
        with self.assertRaises(ValueError):self.evaluate(material_id='bad')
    def test_expired_price(self):
        r=self.evaluate(material_id='EQP-001',warehouse='设备仓',quantity=5,budget=2000)
        self.assertTrue(any(c['dimension']=='价格时效' for c in r['checks']))
    def test_budget_and_short_lead(self):
        r=self.evaluate(quantity=200,budget=1,needed_by=str(app.today()))
        self.assertTrue(any(c['dimension']=='预算检查' and c['level']=='review' for c in r['checks']))
        self.assertTrue(any(c['dimension']=='采购周期' for c in r['checks']))
    def test_usage_and_inactive(self):
        self.assertTrue(any(c['dimension']=='用量合理性' for c in self.evaluate(quantity=1000)['checks']))
        self.assertEqual(self.evaluate(material_id='OLD-001')['status'],'需部门复核')
    def test_confirm_idempotent_duplicate_and_snapshot(self):
        a=app.assess(self.p);first=app.confirm(a['id']);second=app.confirm(a['id'])
        self.assertEqual(first['id'],second['id']);self.assertTrue(second['existing'])
        self.assertTrue(any(c['dimension']=='重复申请' for c in self.evaluate()['checks']))
        with app.connect() as c:
            snapshot=json.loads(c.execute('SELECT snapshot FROM applications').fetchone()[0])
            self.assertEqual(len(snapshot['rules']),4)
    def test_confirm_rechecks_stock(self):
        a=app.assess(self.p)
        with app.connect() as c:c.execute('UPDATE stock SET on_hand=0 WHERE material_id=?',('PPE-001',))
        first=app.confirm(a['id'])
        with app.connect() as c:r=json.loads(c.execute('SELECT snapshot FROM applications WHERE id=?',(first['id'],)).fetchone()[0])
        self.assertEqual(r['purchase_quantity'],20)
    def test_seed_idempotent(self):
        app.init_db()
        with app.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM materials').fetchone()[0],8)
    def test_rule_change(self):
        with app.connect() as c:c.execute("UPDATE rules SET threshold=0.1,version=2 WHERE id='usage_months'")
        self.assertTrue(any(c['dimension']=='用量合理性' for c in self.evaluate()['checks']))
    def test_local_extraction(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'','OPENAI_MODEL':''}):
            r=app.extract('生产部需要500个包装箱，预算2000元，送到一号仓')
        self.assertEqual(r['fields']['quantity'],500);self.assertEqual(r['fields']['budget'],2000)
        self.assertNotIn('purpose',r['fields'])
    def test_model_extraction_mock(self):
        fields={k:None for k in app.EXTRACT_KEYS};fields['material_name']='包装箱'
        response=io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(fields)}]}]}).encode())
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test','OPENAI_MODEL':'configured-model'}),patch('app.urlopen',return_value=response):
            self.assertEqual(app.extract('包装箱')['fields']['material_name'],'包装箱')
    def test_model_error(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test','OPENAI_MODEL':'configured-model'}),patch('app.urlopen',side_effect=TimeoutError):
            with self.assertRaisesRegex(ValueError,'模型服务'):app.extract('包装箱')
    def test_pdf_chinese_and_long_text(self):
        from pypdf import PdfReader
        r=self.evaluate(purpose='<script>\n'+('长文本的用途及数量依据。'*35))
        pdf=app.make_pdf('PR-TEST',r)
        reader=PdfReader(io.BytesIO(pdf));text=''.join(p.extract_text() for p in reader.pages)
        self.assertIn('采购需求申请表',text);self.assertIn('丁腈手套',text);self.assertIn('长文本',text)

if __name__=='__main__':unittest.main()
