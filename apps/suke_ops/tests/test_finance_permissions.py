"""财务页面读取与模型使用分别授权；仅内存替身。"""
import importlib
import sys
import unittest
import json
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,patch


class FinancePermissionTests(unittest.TestCase):
    def test_older_subsecond_snapshot_never_overwrites_newer_balance(self):
        from suke_ops.finance_sync.core import normalize,timestamp
        from suke_ops.finance_sync.connectors import MockConnector
        fake=MagicMock();fake.db.exists.return_value=True
        current=NS(source_modified=timestamp('2026-10-30T00:00:00.900+00:00'))
        fake.get_doc.return_value=current
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':NS(cint=int,now_datetime=datetime.now)}):
            service=importlib.import_module('suke_ops.finance_sync.service')
        raw=MockConnector('100').fetch('payable',None,'2026-10-31T00:00:00+00:00',0,1)[0]
        row=normalize('payable',dict(raw,modified_at='2026-10-30T00:00:00.100+00:00',outstanding='100'),'100','+08:00')
        with patch.object(service,'frappe',fake),patch.object(service,'save_internal',side_effect=AssertionError('不得写旧余额')):
            self.assertEqual(service.upsert(NS(name='TEST',account_id='TEST',source_org='100'),row),'older')

    def test_query_summary_preserves_maximum_allowed_decimal_precision(self):
        now=datetime(2026,11,1)
        fake=MagicMock();fake.whitelist=lambda *a,**kw:lambda fn:fn
        utils=NS(cint=int,get_datetime=lambda x:x,now_datetime=lambda:now)
        value='1234567890123456789.1234567891'
        records=[NS(name=str(n),state='active',payload=json.dumps({'currency':'CNY','amount':value,'outstanding':value}),synced_at=now) for n in range(2)]
        fake.get_all.side_effect=lambda *a,**kw:[NS(total=2)] if isinstance(kw['fields'][0],dict) else records
        binding=NS(entity='payable',last_success=now,cursor='',last_error='')
        connection=NS(name='TEST',title='合成',company='TEST',source_org='100',source_timezone='+08:00',mode='mock',enabled=1,stale_minutes=60,bindings=[binding])
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':utils}):
            api=importlib.import_module('suke_ops.finance_sync.api')
        with patch.object(api,'frappe',fake),patch.object(api.service,'authorize',return_value=connection):
            summary=api.query('TEST')['summary_by_currency']['CNY']
        self.assertEqual(summary['amount'],'2469135780246913578.2469135782')
        self.assertEqual(summary['outstanding'],summary['amount'])

    def test_manager_requires_explicit_personal_model_permission(self):
        fake=MagicMock();fake.session.user='Manager'
        class Denied(RuntimeError):pass
        fake.PermissionError=Denied
        def throw(message,*args):raise Denied(message)
        fake.throw.side_effect=throw
        connection=NS(company='TEST',allow_ai=1,access_users=[])
        fake.get_doc.side_effect=lambda kind,name:connection if kind=='ERP Connection' else NS(check_permission=lambda *a:None)
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':NS(cint=int,now_datetime=datetime.now),'suke_ops.business_search':NS(require_user=lambda:None)}):
            service=importlib.import_module('suke_ops.finance_sync.service')
            with patch.object(service,'frappe',fake),patch.object(service,'manager',return_value=True):
                self.assertIs(service.authorize('TEST'),connection)
                with self.assertRaises(Denied):service.authorize('TEST',ai=True)
                connection.access_users=[NS(user='Manager',allow_ai=0)]
                with self.assertRaises(Denied):service.authorize('TEST',ai=True)
                connection.access_users[0].allow_ai=1
                self.assertIs(service.authorize('TEST',ai=True),connection)
                connection.allow_ai=0
                with self.assertRaises(Denied):service.authorize('TEST',ai=True)
