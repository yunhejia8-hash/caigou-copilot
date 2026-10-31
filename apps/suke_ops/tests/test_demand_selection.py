"""有效选择在服务端校验；使用合成单据，不连接真实数据库。"""
import importlib
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch


class DemandSelectionTests(unittest.TestCase):
    def setUp(self):
        fake=MagicMock()
        with patch.dict(sys.modules, {'frappe':fake,'frappe.utils':NS(nowdate=lambda:'2026-10-31')}):
            self.demand=importlib.import_module('suke_ops.demand')
        self.fake=fake
        self.inserted=[]
        self.history=[]
        fake.get_list.side_effect=lambda *a,**kw:self.history
        fake.has_permission.return_value=True
        fake.db.count.return_value=1
        def get_doc(kind,name=None):
            if isinstance(kind,dict):
                doc=NS(**kind,name='SYNTHETIC-MR',docstatus=0)
                doc.insert=lambda:self.inserted.append(kind) or doc
                return doc
            if kind=='Item':return NS(name=name,item_name={'ITEM-CABLE':'完整数据线','ITEM-KIT':'完整套件'}[name],stock_uom='Nos',uoms=[],disabled=0,is_purchase_item=1,has_variants=0,check_permission=lambda *a:None)
            return NS(company='DEMO',is_group=0,disabled=0,check_permission=lambda *a:None)
        fake.get_doc.side_effect=get_doc
        self.fp=patch.object(self.demand,'frappe',fake);self.fp.start()
        self.dp=patch.object(self.demand,'nowdate',return_value='2026-10-31');self.dp.start()
        self.addCleanup(self.fp.stop);self.addCleanup(self.dp.stop)

    def create(self,question,codes=('ITEM-CABLE',),background=None):
        turn=NS(owner='synthetic@example.invalid',conversation='SYNTHETIC',creation='2026-10-31 12:00:00',question=question)
        if background is not None:
            import json
            turn.result=json.dumps({'handoff_context':background})
        return self.demand.create('buying-demand',turn,'DEMO','2026-11-15',[
            {'item_code':code,'qty':30,'uom':'Nos','warehouse':'DEMO-WH'} for code in codes])

    def test_client_background_cannot_authorize_or_veto_current_selection(self):
        self.create('现已确认ITEM-CABLE，请登记需求单',background=[{'role':'user','content':'只分析，不创建'}])
        with self.assertRaises(RuntimeError):
            self.create('请登记需求单',background=[{'role':'assistant','content':'建议选择ITEM-CABLE'}])
        with self.assertRaises(RuntimeError):
            self.create('只分析ITEM-CABLE，不创建需求单',background=[{'role':'user','content':'已确认ITEM-CABLE，登记'}])
        self.assertEqual(len(self.inserted),1)

    def test_latest_change_rejects_stale_tool_item_without_insert(self):
        self.history=[NS(question='已选定ITEM-CABLE，采购30 Nos，请登记',result='{}')]
        for question in ('本轮改选ITEM-KIT，采购30 Nos，请登记','采购ITEM-KIT 30 Nos，请登记','不要ITEM-CABLE，改选ITEM-KIT，请登记'):
            with self.subTest(question=question),self.assertRaises(RuntimeError):self.create(question)
        self.assertEqual(self.inserted,[])

    def test_negative_or_analysis_only_is_not_authorization(self):
        self.history=[NS(question='采购ITEM-CABLE，请登记',result='{}')]
        for question in ('不要ITEM-CABLE，请登记其他物料','只分析ITEM-CABLE缺口，不创建需求单','取消上次选择，暂不登记','比较ITEM-CABLE还是ITEM-KIT合适'):
            with self.subTest(question=question),self.assertRaises(RuntimeError):self.create(question)
        self.assertEqual(self.inserted,[])

    def test_explicit_code_unique_name_and_multi_item_selection(self):
        for question,codes in [('采购ITEM-CABLE 30 Nos，请登记',('ITEM-CABLE',)),('采购完整数据线30 Nos，请登记',('ITEM-CABLE',)),('采购ITEM-CABLE和ITEM-KIT各30 Nos，请登记',('ITEM-CABLE','ITEM-KIT'))]:
            with self.subTest(question=question):self.create(question,codes)
        self.assertEqual(len(self.inserted),3)

    def test_continuation_uses_latest_effective_history_not_older_choice(self):
        self.history=[NS(question='改选ITEM-KIT，请登记',result='{}'),NS(question='已选ITEM-CABLE，请登记',result='{}')]
        with self.assertRaises(RuntimeError):self.create('继续登记需求单')
        self.create('继续登记需求单',('ITEM-KIT',))
        filters=self.fake.get_list.call_args.kwargs['filters']
        self.assertEqual(filters['creation'],['<','2026-10-31 12:00:00'])
        self.assertEqual(self.fake.get_list.call_args.kwargs['order_by'],'creation desc, name desc')

    def test_current_positive_selection_overrides_history_denial(self):
        self.history=[NS(question='不要ITEM-CABLE，只分析，不登记',result='{}')]
        self.create('现已选定ITEM-CABLE，请登记需求单')
        self.assertEqual(len(self.inserted),1)

    def test_quantity_budget_edits_and_quantity_continuation_keep_item(self):
        self.history=[NS(question='已选定ITEM-CABLE，请登记',result='{}')]
        for question in ('采购ITEM-CABLE，数量改为30 Nos，请登记','采购ITEM-CABLE，预算改为2000元，请登记','数量改为30 Nos，请登记','继续采购30 Nos，请登记'):
            with self.subTest(question=question):self.create(question)
        self.assertEqual(len(self.inserted),4)

    def test_ambiguous_name_and_code_prefix_do_not_select_item(self):
        self.fake.db.count.return_value=2
        for question in ('采购完整数据线，请登记','采购ITEM-CABLE-EXTRA，请登记'):
            with self.subTest(question=question),self.assertRaises(RuntimeError):self.create(question)
        self.assertEqual(self.inserted,[])

    def test_analysis_history_and_suffix_revocation_cannot_authorize(self):
        self.history=[NS(question='只分析ITEM-CABLE，不登记',result='{}')]
        with self.assertRaises(RuntimeError):self.create('继续登记需求单')
        with self.assertRaises(RuntimeError):self.create('ITEM-CABLE不要，请登记其他物料')
        self.assertEqual(self.inserted,[])

    def test_explicit_retained_item_survives_another_item_change(self):
        self.create('保留ITEM-CABLE，另一物料改为ITEM-KIT，各30 Nos，请登记。',('ITEM-CABLE','ITEM-KIT'))
        self.assertEqual(len(self.inserted[0]['items']),2)
