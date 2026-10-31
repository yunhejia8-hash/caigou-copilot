import importlib
import json
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

fake = MagicMock()
fake.whitelist = lambda *a, **k: lambda fn: fn
with patch.dict(sys.modules, {'frappe':fake,'frappe.utils':SimpleNamespace(now_datetime=lambda:'2026-10-31 12:00:00')}):
    workflow = importlib.import_module('suke_ops.procurement_workflow')

FIELDS = {'material_name':'五层瓦楞包装箱','spec':'400×300×250mm','quantity':500,'unit':'个',
          'purpose':'500件产品出货，每件一个','budget':2000,'needed_by':'2026-11-15','warehouse':'一号仓',
          'applicant':'李明','department':'生产部'}
TEXT = '申请人李明，生产部，需要500个五层瓦楞包装箱，规格400×300×250mm，用于500件产品出货，每件一个。预算2000元，2026年11月15日前送到一号仓。'
QUOTE='供应商：A厂，物料：五层瓦楞包装箱，规格：400×300×250mm\n数量：500个，单价：3.5元/个，含税，运费100元\n到货：2026-11-15，有效期：2026-11-14，付款：验收后30天'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.patches=[patch.object(workflow,'duplicate_candidates',return_value=[]),
                      patch.object(workflow,'create_pdf',side_effect=lambda wf,name:{'file':'PDF-'+name,'turn':name}),
                      patch.object(workflow.intake,'today',return_value=date(2026,10,31))]
        for p in self.patches:p.start()
        self.n=0
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
    def turn(self,text):
        self.n+=1
        return SimpleNamespace(question=text,name='TEST'+str(self.n),conversation='test',creation='2026-10-31')
    def process(self,text,prior=None):
        with patch.object(workflow,'previous_workflow',return_value=prior):
            return workflow.process(self.turn(text),prior)
    def ready(self):
        return self.process(TEXT)['workflow']
    def confirm(self,wf):
        return self.process(f"确认申请 {wf['id']} 第{wf['revision']}版",wf)['workflow']
    def test_original_preflight_missing_identity(self):
        f={k:v for k,v in FIELDS.items() if k not in ('applicant','department')}
        r=workflow.preflight(f)
        self.assertEqual(r['state'],'needs_info');self.assertIn('申请人',r['missing']);self.assertIn('申请部门',r['missing'])
    def test_missing_material_is_not_replaced_by_scratch_identity(self):
        r=workflow.preflight({k:v for k,v in FIELDS.items() if k != 'material_name'})
        self.assertEqual(r['state'],'needs_info');self.assertIn('物料名称或编码',r['missing'])
    def test_original_stock_budget_and_quantity_checks(self):
        r=workflow.preflight({**FIELDS,'available_stock':0,'on_time_incoming':0,'unit_price':5})
        self.assertEqual(r['state'],'blocked');self.assertTrue(r['blockers'])
        r=workflow.preflight({**FIELDS,'available_stock':500,'on_time_incoming':0})
        self.assertEqual(r['purchase_quantity'],0)
        r=workflow.preflight({**FIELDS,'people_count':4,'per_person':5,'basis_unit':'个'})
        self.assertEqual(r['state'],'needs_clarification')
    def test_named_material_duplicate_detected_without_master_code(self):
        r=workflow.preflight(FIELDS,duplicates=[{'id':'OLD','fields':FIELDS,'state':'review'}])
        self.assertEqual(r['state'],'blocked');self.assertIsNone(r['fields']['material_id'])
    def test_unknown_price_never_budget_divided_by_quantity(self):
        r=workflow.preflight(FIELDS)
        self.assertEqual(r['state'],'review');self.assertIsNone(r['unit_price']);self.assertIsNone(r['estimated_total'])
    def test_original_duplicate_check_preserved(self):
        f={**FIELDS,'material_id':'TEST-BOX'}
        r=workflow.preflight(f,duplicates=[{'id':'OLD','fields':f,'state':'review'}])
        self.assertEqual(r['state'],'blocked');self.assertIn('重复',r['blockers'][0])
    def test_combined_example_stops_at_application(self):
        with patch.object(workflow.procurement_bridge,'run') as compare:
            result=self.process(TEXT+'\n\n'+QUOTE)
        self.assertEqual(result['workflow']['phase'],'awaiting_confirmation');self.assertTrue(result['application']['preview_url']);compare.assert_not_called()
    def test_confirmation_does_not_replay_old_quotes(self):
        ready=self.ready()
        with patch.object(workflow.procurement_bridge,'run') as compare:
            wf=self.confirm(ready)
        self.assertEqual(wf['phase'],'inquiry');compare.assert_not_called()
    def test_quote_before_confirmation_never_calls_tools(self):
        with patch.object(workflow.procurement_bridge,'run') as compare:
            r=self.process(QUOTE,self.ready())
        self.assertEqual(r['workflow']['phase'],'awaiting_confirmation');compare.assert_not_called()
    def test_quote_after_confirmation_uses_frozen_application(self):
        wf=self.confirm(self.ready())
        with patch.object(workflow.procurement_bridge,'run',return_value={'quotes':[{},{}],'comparable_count':2,'lowest_total':1850}) as compare:
            saved=self.process(QUOTE,wf)
            compare.assert_not_called()
            self.assertEqual(saved['workflow']['phase'],'quotations_ready')
            r=self.process('开始比价',saved['workflow'])
        self.assertEqual(r['workflow']['phase'],'compared');self.assertEqual(compare.call_args.kwargs['fields']['quantity'],500)
    def test_change_invalidates_confirmation_and_creates_revision(self):
        wf=self.confirm(self.ready());old=wf['revision']
        r=self.process('预算改为3000元',wf)
        self.assertEqual(r['workflow']['phase'],'awaiting_confirmation');self.assertEqual(r['workflow']['revision'],old+1);self.assertNotIn('confirmed_at',r['workflow'])
        stale=self.process(f"确认申请 {wf['id']} 第{old}版",r['workflow'])
        self.assertEqual(stale['workflow']['phase'],'awaiting_confirmation')
    def test_guard_stops_model_from_skipping_sequence(self):
        for wf in [None,{},self.ready()]:
            for tool in ('procurement_compare','procurement_search'):
                with self.assertRaises(ValueError):workflow.guard(wf,tool)
        workflow.guard(self.confirm(self.ready()),'procurement_search')
        with self.assertRaises(ValueError):workflow.guard(self.confirm(self.ready()),'procurement_compare')
    def test_html_escapes_user_data_and_preserves_unit(self):
        wf=self.ready();wf['fields']['purpose']='<script>alert(1)</script>'
        html=workflow.application_html(wf)
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html);self.assertIn('500.0个',html)
    def test_confirmation_not_inferred_from_generic_agreement(self):
        wf=self.ready();r=self.process('行，继续',wf)
        self.assertEqual(r['workflow']['phase'],'awaiting_confirmation')
    def test_application_metadata_endpoint_has_no_secret_file_path(self):
        r=workflow.document(self.ready());self.assertNotIn('file',r);self.assertNotIn('/private/',json.dumps(r))
