import io
import json
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import app
import chat_service as chat

class ChatTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.temp.name)/'demo.db'
        app.init_db();chat.init_tables()
        with app.connect() as c:
            c.execute('INSERT INTO history_sources VALUES (1,?,?,?,?,?)',('测试历史.xlsx','hash','purchase',chat.now(),1))
            month=chat.today().replace(day=1)
            for i in range(6):
                month=(month-timedelta(days=1)).replace(day=1)
                c.execute('INSERT INTO demand_history VALUES (?,?,?,?,?,?,?,?,?)',(1,'M-10003',month.strftime('%Y-%m'),7000,'无',95.45,304,'销售数据',2+i))
        self.date=str(chat.today()+timedelta(days=14))
        self.full=f'M-10003需要500件，预算5万元，用于下月补货，申请人张三，生产部，送到一号仓，{self.date}到货，当前库存100件，按时在途50件'
        self.env=patch.dict('os.environ',{'OPENAI_API_KEY':'','OPENAI_MODEL':''});self.env.start()
        self.pool=patch.object(chat.POOL,'submit',side_effect=lambda fn,*args:fn(*args));self.pool.start()
    def tearDown(self):
        self.pool.stop();self.env.stop();app.DB=self.old;self.temp.cleanup()
    def send(self,text,rid=None,token=None):
        response=chat.start(text,rid,token or uuid.uuid4().hex)
        result=chat.job_status(response['job_id'])
        self.assertEqual(result['status'],'done',result)
        return result['result'],response
    def test_code_without_spaces(self):
        for text in ['我需要M-10003一千件','M-10003需要500件','需要 M-10003 共500件']:
            self.assertEqual(chat.local_extract(text)[0]['material_id'],'M-10003')
    def test_chinese_number_and_money(self):
        f,e=chat.local_extract('需要M-10003一千件，预算十万元，用于补货')
        self.assertEqual(f['quantity'],1000);self.assertEqual(f['budget'],100000)
    def test_stock_not_demand(self):
        f,e=chat.local_extract('当前库存100件，按时在途50件')
        self.assertNotIn('quantity',f);self.assertEqual(f['on_time_incoming'],50)
    def test_unconfirmed_incoming_not_deducted(self):
        f,e=chat.local_extract('在途100件')
        self.assertNotIn('on_time_incoming',f);self.assertTrue(e)
    def test_history_does_not_supply_current_data(self):
        result,_=self.send('需要M-10003共500件，预算5万元，用于补货')
        self.assertEqual(result['state'],'needs_info');self.assertIn('当前可用库存',result['message']);self.assertIn('在途量',result['message']);self.assertIsNone(result['pdf_url'])
    def test_auto_process_and_snapshot(self):
        result,r=self.send(self.full)
        self.assertEqual(result['state'],'passed');self.assertIn('33,407.50',result['message'])
        with app.connect() as c:
            snapshot=json.loads(c.execute('SELECT snapshot FROM applications').fetchone()[0])
        self.assertEqual(snapshot['purchase_quantity'],350);self.assertEqual(snapshot['unit_price'],95.45)
        self.assertEqual(snapshot['history_snapshot'][-1]['historical_incoming'],304)
        self.assertFalse(snapshot['department_budget_checked'])
        self.assertNotIn('history_snapshot',result);self.assertNotIn('fields',result)
    def test_followup_preserves_request(self):
        result,r=self.send('需要M-10003共500件，预算5万元，用于补货')
        result,r2=self.send(f'申请人张三，生产部，送到一号仓，{self.date}到货，当前库存100件，按时在途50件',r['request_id'])
        self.assertEqual(r2['request_id'],r['request_id']);self.assertEqual(result['state'],'passed')
        self.assertEqual(len(chat.conversation(r['request_id'])['messages']),4)
    def test_overbudget_no_application(self):
        result,_=self.send(self.full.replace('5万元','100元'))
        self.assertEqual(result['state'],'blocked');self.assertIn('超过本次预算',result['message']);self.assertIsNone(result['pdf_url'])
        with app.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],0)
    def test_fix_budget(self):
        result,r=self.send(self.full.replace('5万元','100元'))
        result,_=self.send('预算改为5万元',r['request_id'])
        self.assertEqual(result['state'],'passed')
    def test_duplicate_request(self):
        first,_=self.send(self.full);second,_=self.send(self.full)
        self.assertEqual(second['state'],'blocked');self.assertIn(first['request_id'],second['message'])
    def test_idempotent_retries(self):
        token=uuid.uuid4().hex
        first,a=self.send(self.full,token=token);second,b=self.send(self.full,token=token)
        self.assertEqual(a,b)
        with app.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],1)
    def test_reused_token_changed_content(self):
        _,a=self.send(self.full,token='same-token-123')
        with self.assertRaises(ValueError):chat.start('不同内容',None,'same-token-123')
    def test_invalid_correction_remains_blocked(self):
        result,r=self.send(self.full.replace('5万元','100元'))
        result,_=self.send('数量改为-2件',r['request_id']);self.assertEqual(result['state'],'needs_clarification')
        result,_=self.send('预算改为5万元',r['request_id']);self.assertEqual(result['state'],'needs_clarification');self.assertIn('数量不合法',result['message'])
        result,_=self.send('数量改为500件',r['request_id']);self.assertEqual(result['state'],'passed')
    def test_ambiguous_incoming_can_be_clarified(self):
        result,r=self.send(self.full.replace('按时在途','在途'));self.assertEqual(result['state'],'needs_clarification')
        result,_=self.send('按时在途50件',r['request_id']);self.assertEqual(result['state'],'passed')
    def test_unknown_sentence_requests_clarification(self):
        result,r=self.send('按之前说的来吧')
        self.assertEqual(result['state'],'needs_clarification')
        self.assertIn('未能从这句话',result['message'])
        self.assertIsNone(result['pdf_url'])
        result,_=self.send(self.full,r['request_id'])
        self.assertEqual(result['state'],'passed')
    def test_quantity_ambiguity_persists_until_answered(self):
        result,r=self.send(self.full.replace('500件','500件或600件'))
        self.assertEqual(result['state'],'needs_clarification')
        result,_=self.send('预算改为6万元',r['request_id'])
        self.assertEqual(result['state'],'needs_clarification')
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],0)
        result,_=self.send('需求数量500件',r['request_id'])
        self.assertEqual(result['state'],'passed')
    def test_invalid_date_is_clarification(self):
        result,r=self.send(self.full.replace(self.date,'2026-02-30'))
        self.assertEqual(result['state'],'needs_clarification')
        self.assertIn('日期无效',result['message'])
        self.assertIsNone(result['pdf_url'])
    def test_uncertainty_precedes_budget_decision(self):
        result,_=self.send(self.full.replace('500件','500盒').replace('5万元','100元'))
        self.assertEqual(result['state'],'needs_clarification')
        self.assertIn('换算',result['message'])
        self.assertIsNone(result['pdf_url'])
    def test_unknown_material_no_demo_match(self):
        result,_=self.send(self.full.replace('M-10003','PKG-001'))
        self.assertEqual(result['state'],'review');self.assertIn('新物料',result['message'])
        with app.connect() as c:
            snapshot=json.loads(c.execute('SELECT snapshot FROM human_reviews').fetchone()[0])
            self.assertIsNone(snapshot['unit_price'])
            self.assertEqual(snapshot['history_snapshot'],[])
    def test_new_material_supplement_routes_to_review(self):
        result,r=self.send(self.full.replace('M-10003','M-99999'))
        result,_=self.send('规格304不锈钢件，采购单价90元',r['request_id'])
        self.assertEqual(result['state'],'review');self.assertIn('人工审核',result['message']);self.assertIsNone(result['pdf_url'])
    def glove_text(self):
        return f'我是生产部张三，需要20盒M码丁腈手套，每盒100只，用于4名操作人员下月领用，每人5盒。预算600元，要求{self.date}前送到一号仓。'
    def box_text(self):
        return f'我是生产部李明，需要500个五层瓦楞包装箱，规格400×300×250mm，用于500件产品出货，每件一个。预算2000元，要求{self.date}前送到一号仓。'
    def test_product_output_is_not_request_quantity(self):
        fields,issues=chat.local_extract(self.box_text())
        self.assertEqual(issues,[])
        self.assertEqual(fields['quantity'],500)
        self.assertEqual(fields['unit'],'个')
        self.assertEqual(fields['material_name'],'五层瓦楞包装箱')
        self.assertEqual(fields['spec'],'400×300×250mm')
        self.assertEqual(fields['purpose'],'500件产品出货，每件一个')
        self.assertEqual(fields['applicant'],'李明')
        self.assertEqual(fields['warehouse'],'一号仓')
        result,_=self.send(self.box_text())
        self.assertEqual(result['state'],'review')
        self.assertNotIn('多个需求数量',result['message'])
    def test_product_basis_mismatch_is_clarifiable(self):
        result,r=self.send(self.box_text().replace('每件一个','每件两个'))
        self.assertEqual(result['state'],'needs_clarification')
        self.assertIn('1000个',result['message'])
        result,_=self.send('需求数量1000个',r['request_id'])
        self.assertEqual(result['state'],'review')
    def test_product_basis_does_not_hide_another_request(self):
        result,_=self.send(self.box_text().replace('规格400','另需20卷胶带，规格400'))
        self.assertEqual(result['state'],'needs_clarification')
        self.assertIn('多个需求数量',result['message'])
    def test_gloves_register_and_queue(self):
        f,issues=chat.local_extract(self.glove_text())
        self.assertEqual(issues,[]);self.assertEqual(f['quantity'],20);self.assertEqual(f['applicant'],'张三')
        self.assertEqual(f['spec'],'M码 / 100只/盒')
        result,r=self.send(self.glove_text())
        self.assertEqual(result['state'],'review');self.assertIn('数量依据一致',result['message'])
        with app.connect() as c:
            row=c.execute('SELECT * FROM new_materials').fetchone()
            self.assertEqual(row['name'],'丁腈手套');self.assertEqual(row['status'],'pending_review')
        queued=chat.human_queue()
        self.assertEqual(len(queued),1);self.assertEqual(queued[0]['request_id'],r['request_id'])
        self.assertIsNone(queued[0]['estimated_total']);self.assertIsNone(queued[0]['purchase_quantity'])
    def test_new_material_reused_not_duplicated(self):
        first,r=self.send(self.glove_text())
        result,_=self.send('采购单价28元',r['request_id'])
        self.assertEqual(result['state'],'review')
        with app.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM new_materials').fetchone()[0],1)
        duplicate,_=self.send(self.glove_text())
        self.assertEqual(duplicate['state'],'blocked');self.assertIn(r['request_id'],duplicate['message'])
        self.assertEqual(len(chat.human_queue()),1)
    def test_real_quantity_conflict_blocks(self):
        result,_=self.send(self.glove_text().replace('每人5盒','每人6盒'))
        self.assertEqual(result['state'],'needs_clarification');self.assertIn('24盒',result['message'])
        self.assertEqual(chat.human_queue(),[])
    def test_new_material_known_overbudget_blocks(self):
        text=self.glove_text()+'采购单价40元，当前可用库存0盒，按时在途0盒。'
        result,_=self.send(text)
        self.assertEqual(result['state'],'blocked');self.assertIn('800.00',result['message'])
        self.assertEqual(chat.human_queue(),[])
    def test_incomplete_new_material_still_needs_applicant_input(self):
        result,_=self.send('需要20盒M码丁腈手套，每盒100只')
        self.assertEqual(result['state'],'needs_info');self.assertIn('用途',result['message'])
        self.assertEqual(chat.human_queue(),[])
    def test_oversize_demand_is_review_not_rejection(self):
        result,_=self.send(self.full.replace('500件','50000件').replace('5万元','500万元'))
        self.assertEqual(result['state'],'review');self.assertIn('计划部门',result['message'])
    def test_no_purchase(self):
        result,_=self.send(self.full.replace('500件','100件'))
        self.assertEqual(result['state'],'no_purchase');self.assertIsNone(result['pdf_url'])
    def test_wrong_unit(self):
        result,_=self.send(self.full.replace('500件','500盒'))
        self.assertEqual(result['state'],'needs_clarification');self.assertIn('换算',result['message'])
    def test_date_change_invalidates_supply(self):
        result,r=self.send(self.full.replace('5万元','100元'))
        newdate=str(chat.today()+timedelta(days=20))
        result,_=self.send(f'{newdate}到货，预算5万元',r['request_id'])
        self.assertEqual(result['state'],'needs_info');self.assertIn('当前可用库存',result['message'])
    def test_model_failure_does_not_approve(self):
        with patch('chat_service.extract',side_effect=ValueError('模型不可用')):
            r=chat.start(self.full,token=uuid.uuid4().hex)
        self.assertEqual(chat.job_status(r['job_id'])['status'],'failed')
        with app.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],0)
    def test_restart_recovers_jobs(self):
        with patch.object(chat.POOL,'submit'):
            r=chat.start(self.full,token=uuid.uuid4().hex)
        chat.init_tables(recover=True)
        self.assertEqual(chat.job_status(r['job_id'])['status'],'failed')
    def test_pdf_has_real_source_and_disclosure(self):
        from pypdf import PdfReader
        result,_=self.send(self.full)
        with app.connect() as c:row=c.execute('SELECT * FROM applications').fetchone()
        content=app.make_pdf(row['id'],json.loads(row['snapshot']))
        text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        self.assertIn('M-10003',text);self.assertIn('申请人声明',text);self.assertNotIn('模拟数据演示',text)

if __name__=='__main__':unittest.main()
