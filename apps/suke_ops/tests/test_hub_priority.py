"""Exercise the public priority API without a database, queue or model call."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from suke_ops import agent_runtime, trace_scenario


class ApiError(Exception):
    pass


class AccessError(ApiError):
    pass


class FakeFrappe(ModuleType):
    """Only Agent Turn storage is available; business writes fail this fixture."""

    def __init__(self):
        super().__init__('frappe')
        self.session = SimpleNamespace(user='buyer@example.test')
        self.PermissionError = AccessError
        self.documents = {}
        self.jobs = []
        self.db = SimpleNamespace(sql=lambda *a, **k: [], get_value=self.get_value)

    @staticmethod
    def whitelist(*args, **kwargs):
        return lambda function: function

    @staticmethod
    def throw(message, exception=ApiError):
        raise exception(message)

    @staticmethod
    def get_cached_value(doctype, name, field):
        return {'user_type': 'System User', 'enabled': 1}[field]

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, str):
            assert doctype == 'Agent Turn'
            return self.documents[name]
        assert doctype['doctype'] == 'Agent Turn', 'No business documents may be written.'
        doc = SimpleNamespace(**deepcopy(doctype))
        doc.name = 'turn-' + str(len(self.documents) + 1)
        doc.owner = self.session.user
        doc.answer = ''

        def insert(**kwargs):
            self.documents[doc.name] = doc
            return doc

        doc.insert = insert
        return doc

    def get_value(self, doctype, filters, field):
        assert doctype == 'Agent Turn'
        return next((doc.name for doc in self.documents.values()
                     if getattr(doc, 'request_key', None) == filters['request_key']), None)

    def get_all(self, doctype, filters, fields):
        assert doctype == 'Agent Turn'
        return [doc for doc in self.documents.values()
                if doc.owner == filters['owner'] and doc.state in filters['state'][1]]

    def enqueue(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


class HubPriorityTests(unittest.TestCase):
    def setUp(self):
        self.frappe = FakeFrappe()
        utils = ModuleType('frappe.utils')
        utils.cint, utils.flt = int, float
        utils.now_datetime = lambda: datetime(2026, 11, 2, 11, 0)
        utils.nowdate = lambda: '2026-11-02'
        path = Path(__file__).resolve().parents[1] / 'suke_ops' / 'hub.py'
        spec = importlib.util.spec_from_file_location('_hub_priority_test', path)
        self.hub = importlib.util.module_from_spec(spec)
        # demand is unrelated to this API; isolate its Frappe import as well.
        with patch.dict(sys.modules, {'frappe': self.frappe, 'frappe.utils': utils,
                                      'suke_ops.demand': ModuleType('suke_ops.demand')}):
            spec.loader.exec_module(self.hub)
        self.configured = patch.object(agent_runtime, 'configured', return_value=True)
        self.configured.start()
        self.addCleanup(self.configured.stop)
        self.model = patch.object(agent_runtime, 'request_model',
                                  side_effect=AssertionError('An API request must only queue analysis.'))
        self.model.start()
        self.addCleanup(self.model.stop)

    def start(self, **kwargs):
        return self.hub.start(question='按当前顺序评估方案', **kwargs)

    def completed(self, manual=False):
        options = {'priority_order': ['WO-B', 'WO-A'], 'priority_reason': '客户窗口已确认，优先复核 B'} if manual else {}
        result = self.start(scenario_id=6, **options)
        doc = self.frappe.documents[result['name']]
        doc.state = 'done'
        doc.result = json.dumps({key: value for key, value in result.items()
                                 if key in ('verified_evidence', 'priority_policy', 'priority_assessment')}, ensure_ascii=False)
        return result, doc

    def test_default_preview_is_read_only_and_marked_as_preview(self):
        result = self.hub.scenario_preview()
        self.assertTrue(result['priority_policy']['preview'])
        self.assertEqual(result['priority_policy']['mode'], 'due_time')
        self.assertEqual(result['priority_policy']['order'], ['WO-A', 'WO-B'])
        self.assertEqual(result['priority_assessment']['status'], 'assessed')
        self.assertEqual(self.frappe.documents, {})
        self.assertEqual(self.frappe.jobs, [])

    def test_manual_preview_has_authenticated_attribution_and_preserves_evidence(self):
        canonical = trace_scenario.evidence(False)
        result = self.hub.scenario_preview('["WO-B", "WO-A"]', 'B 的客户收货窗口不能调整')
        policy = result['priority_policy']
        self.assertEqual(policy['recorded_by'], self.frappe.session.user)
        self.assertEqual(policy['recorded_at'], '2026-11-02 11:00:00')
        self.assertEqual(policy['order'], ['WO-B', 'WO-A'])
        for key, value in canonical.items():
            self.assertEqual(result[key], value)

    def test_start_persists_policy_outside_canonical_evidence(self):
        result = self.start(scenario_id=6, priority_order='["WO-B", "WO-A"]',
                            priority_reason='先评估 B 的客户窗口')
        self.assertNotIn('preview', result['priority_policy'])
        self.assertEqual(result['verified_evidence'], trace_scenario.evidence(False))
        saved = json.loads(self.frappe.documents[result['name']].result)
        self.assertEqual(saved['scenario'], result['verified_evidence'])
        self.assertEqual(saved['priority_policy'], result['priority_policy'])
        self.assertEqual(saved['priority_assessment'], result['priority_assessment'])
        self.assertEqual(len(self.frappe.jobs), 1)
        self.assertFalse(saved['priority_assessment']['execution_authorized'])

    def test_invalid_manual_input_cannot_queue_work(self):
        invalid = [
            ('not json', '原因'), ('{}', '原因'), ('null', '原因'),
            ('["WO-A"]', '原因'), ('["WO-A", "WO-A"]', '原因'),
            ('["WO-A", "OTHER"]', '原因'), ('["WO-A", 1]', '原因'),
            (['WO-A', 'WO-B'], ''), (['WO-A', 'WO-B'], '   '),
            (['WO-A', 'WO-B'], '理' * 301), (None, '只有理由'),
            (['WO-A', 'WO-B'], None), (' ' * 2001, '原因'),
        ]
        for order, reason in invalid:
            with self.subTest(order=order, reason=reason):
                with self.assertRaises(ApiError):
                    self.start(scenario_id=6, priority_order=order, priority_reason=reason)
        self.assertEqual(self.frappe.documents, {})
        self.assertEqual(self.frappe.jobs, [])

    def test_priority_is_rejected_for_real_erp_and_other_scenarios(self):
        for options in ({}, {'scenario_id': 0}, {'module': 'buying'}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ApiError, '连接器演示'):
                    self.start(**options, priority_order=['WO-B', 'WO-A'], priority_reason='测试顺序')
        self.assertEqual(self.frappe.jobs, [])

    def test_other_synthetic_scenario_still_queues_without_policy(self):
        result = self.start(scenario_id=0)
        self.assertTrue(result['synthetic'])
        self.assertNotIn('priority_policy', result)
        self.assertNotIn('priority_assessment', result)
        self.assertEqual(len(self.frappe.jobs), 1)

    def test_real_erp_analysis_still_queues_without_synthetic_state(self):
        result = self.start(module='buying-order')
        self.assertFalse(result['synthetic'])
        self.assertNotIn('priority_policy', result)
        self.assertIsNone(self.frappe.documents[result['name']].result)

    def test_priority_only_review_preserves_canonical_and_records_new_author_time(self):
        prior, prior_doc = self.completed()
        previous_json = prior_doc.result
        self.hub.now_datetime = lambda: datetime(2026, 11, 2, 11, 15)
        result = self.start(previous_turn=prior['name'], priority_order=['WO-B', 'WO-A'],
                            priority_reason='客户确认不能移动收货窗口')
        self.assertEqual(result['verified_evidence'], prior['verified_evidence'])
        self.assertEqual(prior_doc.result, previous_json)
        self.assertEqual(result['previous_turn'], prior['name'])
        self.assertEqual(json.loads(self.frappe.documents[result['name']].result)['previous_turn'], prior['name'])
        policy = result['priority_policy']
        self.assertEqual(policy['recorded_by'], self.frappe.session.user)
        self.assertEqual(policy['recorded_at'], '2026-11-02 11:15:00')
        self.assertNotEqual(policy['version'], prior['priority_policy']['version'])
        self.assertEqual(policy['order'], ['WO-B', 'WO-A'])

    def test_supplier_update_inherits_manual_policy_without_rewriting_attribution(self):
        prior, _ = self.completed(manual=True)
        self.hub.now_datetime = lambda: datetime(2026, 11, 2, 12, 0)
        result = self.start(previous_turn=prior['name'], scenario_update='supplier_120')
        self.assertEqual(result['priority_policy'], prior['priority_policy'])
        self.assertTrue(result['verified_evidence']['updated'])
        self.assertEqual(result['verified_evidence']['previous_turn'], prior['name'])
        assessment = result['priority_assessment']
        self.assertEqual(assessment['totals']['confirmed_gap'], 360)
        self.assertEqual(assessment['totals']['conditional_gap'], 80)
        allocation = {row['work_order']: row for row in assessment['allocations']}
        self.assertEqual(allocation['WO-A']['conditional_gap'], 80)
        self.assertEqual(allocation['WO-B']['conditional_gap'], 0)

    def test_human_note_preserves_priority_but_pauses_calculation(self):
        prior, prior_doc = self.completed(manual=True)
        previous_json = prior_doc.result
        result = self.start(previous_turn=prior['name'], human_note='仓管口头说盘点少了 20 件，待复核。')
        self.assertEqual(result['priority_policy'], prior['priority_policy'])
        self.assertEqual(prior_doc.result, previous_json)
        self.assertEqual(result['priority_assessment']['status'], 'needs_verification')
        self.assertEqual(result['priority_assessment']['allocations'], [])
        self.assertTrue(all(value is None for value in result['priority_assessment']['totals'].values()))
        note = result['verified_evidence']['human_sources'][-1]
        self.assertEqual(note['reported_by'], self.frappe.session.user)
        self.assertEqual(note['source_path'], prior['name'])

    def test_priority_change_after_unverified_note_does_not_resume_calculation(self):
        prior, doc = self.completed(manual=True)
        result = self.start(previous_turn=prior['name'], human_note='实际库存待复核')
        next_doc = self.frappe.documents[result['name']]
        next_doc.state = 'done'
        next_doc.result = json.dumps(result, ensure_ascii=False)
        changed = self.start(previous_turn=next_doc.name, priority_order=['WO-A', 'WO-B'],
                             priority_reason='恢复按开工时点复核')
        self.assertEqual(changed['priority_policy']['order'], ['WO-A', 'WO-B'])
        self.assertEqual(changed['priority_assessment']['status'], 'needs_verification')
        self.assertEqual(changed['verified_evidence'], result['verified_evidence'])

    def test_changed_priority_does_not_prevent_later_supplier_update(self):
        prior, _ = self.completed()
        changed = self.start(previous_turn=prior['name'], priority_order=['WO-B', 'WO-A'],
                             priority_reason='先评估 B')
        doc = self.frappe.documents[changed['name']]
        doc.state = 'done'
        doc.result = json.dumps(changed, ensure_ascii=False)
        updated = self.start(previous_turn=doc.name, scenario_update='supplier_120')
        self.assertTrue(updated['verified_evidence']['updated'])
        self.assertEqual(updated['priority_policy'], changed['priority_policy'])

    def test_cross_account_prior_turn_is_rejected(self):
        prior, doc = self.completed(manual=True)
        doc.owner = 'other@example.test'
        with self.assertRaisesRegex(AccessError, '自己的对话'):
            self.start(previous_turn=prior['name'], priority_order=['WO-A', 'WO-B'], priority_reason='调整')
        self.assertEqual(len(self.frappe.jobs), 1)

    def test_priority_and_evidence_change_cannot_share_a_request(self):
        prior, _ = self.completed()
        for addition in ({'human_note': '库存变化'}, {'scenario_update': 'supplier_120'}):
            with self.subTest(addition=addition):
                with self.assertRaisesRegex(ApiError, '分开复核'):
                    self.start(previous_turn=prior['name'], priority_order=['WO-B', 'WO-A'],
                               priority_reason='调整', **addition)
        self.assertEqual(len(self.frappe.jobs), 1)

    def test_legacy_completed_turn_gets_default_policy_on_update(self):
        prior, doc = self.completed()
        doc.result = json.dumps({'verified_evidence': prior['verified_evidence']}, ensure_ascii=False)
        result = self.start(previous_turn=doc.name, scenario_update='supplier_120')
        self.assertEqual(result['priority_policy']['mode'], 'due_time')
        self.assertEqual(result['priority_policy']['order'], ['WO-A', 'WO-B'])

    def test_guest_cannot_preview_or_change_priority(self):
        self.frappe.session.user = 'Guest'
        with self.assertRaises(AccessError):
            self.hub.scenario_preview()
        with self.assertRaises(AccessError):
            self.start(scenario_id=6)
        self.assertEqual(self.frappe.jobs, [])


if __name__ == '__main__':
    unittest.main()
