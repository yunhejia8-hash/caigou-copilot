"""质检的有效状态核算；全部为内存合成单据。"""
import importlib
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


class Row(dict):
    def __getattribute__(self,name):
        if name in self:return self[name]
        return super().__getattribute__(name)
    __getattr__=dict.get


class SnapshotTests(unittest.TestCase):
    def test_real_synthetic_material_name_is_not_demo_data(self):
        fake=MagicMock();fake.whitelist=lambda *a,**kw:lambda fn:fn
        utils=Row(now_datetime=lambda:datetime(2026,11,1,1),nowdate=lambda:'2026-11-01',flt=lambda x:float(x or 0))
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':utils}):
            api=importlib.import_module('suke_ops.procurement_api')
        for name in ('合成树脂','合成橡胶','合成纤维'):
            mr=Row(company='生产公司',items=[Row(item_code='MAT-RESIN',item_name=name)])
            self.assertFalse(api._synthetic_source(mr))
        self.assertTrue(api._synthetic_source(Row(company='生产公司',items=[Row(item_code='SUKE-TEST',item_name='演示')])) )

    def test_only_submitted_rejection_blocks_completed_receipt(self):
        fake=MagicMock();fake.whitelist=lambda *a,**kw:lambda fn:fn
        utils=Row(now_datetime=lambda:datetime(2026,11,1,1),nowdate=lambda:'2026-11-01',flt=lambda x:float(x or 0))
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':utils}):
            api=importlib.import_module('suke_ops.procurement_api')
        common=dict(modified='2026-11-01 01:00:00',items=[])
        item=Row(item_code='TEST-ITEM',stock_uom='Nos',stock_qty=10,schedule_date='2026-11-15',received_qty=10,qty=10)
        order=Row(**common,doctype='Purchase Order',name='PO-TEST',docstatus=1,status='Completed',per_billed=100,supplier='TEST-SUPPLIER')
        order['items']=[item]
        receipt=Row(**common,doctype='Purchase Receipt',name='PR-TEST',docstatus=1,status='Completed');receipt['items']=[item]
        process=Row(name='PROCESS-TEST',title='合成流程',company='DEMO',synthetic=1,stock_review=None,review_json=None)
        for status,expected in [(0,'done'),(1,'partial'),(2,'done')]:
            rejected=Row(**common,doctype='Quality Inspection',name='QI-TEST',docstatus=status,status='Rejected')
            with self.subTest(docstatus=status),patch.object(api,'frappe',fake),patch.object(api,'_documents',return_value=[order,receipt,rejected]):
                snapshot=api._snapshot(process)
            self.assertEqual(snapshot['context']['quality_rejected'],status==1)
            self.assertEqual(next(s['state'] for s in snapshot['stages'] if s['id']=='receipt'),expected)
