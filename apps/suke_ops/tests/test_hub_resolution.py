"""HTTP boundary tests with isolated turn storage and transactional action fixtures."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import suke_ops


class Invalid(Exception):
    pass


class Denied(Invalid):
    pass


class HubResolutionTests(unittest.TestCase):
    def setUp(self):
        self.turn = NS(name='TURN', owner='buyer', state='done', question='供应商延期，先保重点客户',
                       answer='模型文字不能充当批准', agent_module='control', result='{}')
        self.docs = {'TURN': self.turn}
        self.writes, self.locks, self.snapshots = [], [], {}
        self.f = ModuleType('frappe')
        self.f.session = NS(user='buyer')
        self.f.PermissionError = Denied
        self.f.whitelist = lambda *a, **kw: lambda fn: fn
        self.f.throw = lambda message, exception=Invalid: self.fail_with(message, exception)
        self.f.get_cached_value = lambda dt, name, field: {'user_type': 'System User', 'enabled': 1}[field]
        self.f.get_doc = lambda dt, name: self.docs[name]
        self.f.db = NS(sql=self.lock, set_value=self.save, savepoint=self.savepoint, rollback=self.rollback)
        utils = NS(cint=int, flt=float, now_datetime=lambda: None, nowdate=lambda: '')
        path = Path(__file__).resolve().parents[1] / 'suke_ops' / 'hub.py'
        spec = importlib.util.spec_from_file_location('_hub_resolution', path)
        self.hub = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'frappe': self.f, 'frappe.utils': utils,
                                      'suke_ops.demand': ModuleType('suke_ops.demand')}):
            spec.loader.exec_module(self.hub)
        self.decisions = NS(
            ACTIONS={'choose': {'plan_id', 'fingerprint', 'reason'}, 'execute': {'reason'}},
            build=Mock(return_value={'kind': 'erp', 'state': 'pending_approval'}),
            act=Mock(side_effect=self.act))
        self.module_patch = patch.object(suke_ops, 'decision_result', self.decisions, create=True)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    @staticmethod
    def fail_with(message, exception):
        raise exception(message)

    def lock(self, query, name):
        self.locks.append(name)

    def save(self, doctype, name, field, value):
        self.assertEqual(doctype, 'Agent Turn')
        setattr(self.docs[name], field, value)

    def savepoint(self, key):
        self.snapshots[key] = (deepcopy(self.docs), list(self.writes))

    def rollback(self, save_point):
        self.docs, self.writes = self.snapshots[save_point]

    def act(self, turn, result, action, **kwargs):
        self.writes.append({'by': self.f.session.user, 'action': action})
        return {**result, 'resolution': {'kind': 'erp', 'state': 'pending_approval'}}

    def test_cross_user_and_disabled_internal_account_cannot_read_or_mutate(self):
        self.f.session.user = 'other'
        with self.assertRaises(Denied):
            self.hub.resolution_refresh('TURN')
        with self.assertRaises(Denied):
            self.hub.resolution_action('TURN', 'execute')
        self.assertEqual(self.locks, [])
        self.assertEqual(self.writes, [])
        self.f.session.user = 'buyer'
        self.f.get_cached_value = lambda *a: 0
        with self.assertRaises(Denied):
            self.hub.resolution_refresh('TURN')

    def test_queued_and_running_discussions_cannot_execute(self):
        for state in ('queued', 'running'):
            self.turn.state = state
            with self.assertRaises(Invalid):
                self.hub.resolution_action('TURN', 'execute')
        self.decisions.act.assert_not_called()

    def test_state_is_rechecked_after_row_lock(self):
        def lock_and_change(*args):
            self.docs['TURN'] = NS(**{**vars(self.turn), 'state': 'running'})
        self.f.db.sql = lock_and_change
        with self.assertRaises(Invalid):
            self.hub.resolution_action('TURN', 'execute')
        self.decisions.act.assert_not_called()

    def test_action_and_receipt_are_atomic_when_persistence_fails(self):
        def fail_save(*args):
            raise Invalid('storage failed after ERP action')
        self.f.db.set_value = fail_save
        with self.assertRaises(Invalid):
            self.hub.resolution_action('TURN', 'execute', reason='确认分配')
        self.assertEqual(self.writes, [])
        self.assertEqual(self.docs['TURN'].result, '{}')

    def test_failed_domain_action_rolls_back_partial_effects(self):
        def fail_action(*args, **kwargs):
            self.writes.append('partial')
            raise Invalid('stock changed')
        self.decisions.act.side_effect = fail_action
        with self.assertRaises(Invalid):
            self.hub.resolution_action('TURN', 'execute')
        self.assertEqual(self.writes, [])

    def test_action_whitelist_and_server_owned_actor(self):
        response = self.hub.resolution_action('TURN', 'choose', plan_id='sales_first',
            fingerprint='observed-version', reason='客户验收窗口不可延后', order_name='unrelated')
        self.assertEqual(response['name'], 'TURN')
        self.assertEqual(response['answer'], self.turn.answer)
        self.assertEqual(json.loads(self.turn.result)['resolution']['state'], 'pending_approval')
        self.assertEqual(self.decisions.act.call_args.kwargs,
            {'plan_id': 'sales_first', 'fingerprint': 'observed-version', 'reason': '客户验收窗口不可延后'})
        self.assertEqual(self.writes, [{'by': 'buyer', 'action': 'choose'}])
        with self.assertRaises(Invalid):
            self.hub.resolution_action('TURN', 'model_says_approved')

    def test_refresh_reads_authority_without_model_or_business_write(self):
        self.turn.state = 'failed'  # A failed model summary need not hide the result.
        self.turn.result = json.dumps({'resolution_context': {'explicit_case': 'CASE'},
                                       'resolution': {'state': 'needs_choice'}})
        original = self.turn.result
        response = self.hub.resolution_refresh('TURN')
        self.assertEqual(response['resolution']['state'], 'pending_approval')
        self.assertEqual(response['state'], 'failed')
        self.assertEqual(self.turn.result, original)
        self.assertEqual(self.writes, [])
        self.decisions.act.assert_not_called()
        self.assertEqual(self.decisions.build.call_args.args[1]['resolution_context']['explicit_case'], 'CASE')

    def run_bound_turn(self, respond):
        """Execute the orchestration, with only ERP reads/model transport replaced."""
        self.turn.state = 'queued'
        self.turn.conversation = 'CONVERSATION'
        self.turn.creation = '2026-11-01 09:00:00'
        self.turn.result = json.dumps({'resolution_context': {'explicit_case': 'CASE-A', 'case_names': []}})
        self.f.set_user = lambda user: setattr(self.f.session, 'user', user)
        self.f.get_all = Mock(return_value=[])
        self.f.db.commit = Mock()
        self.f.db.rollback = Mock()
        self.f.db.exists = Mock(return_value=False)
        self.f.db.get_value = lambda dt, name, field: getattr(self.docs[name], field)
        def save(dt, name, field, value=None):
            self.assertEqual(dt, 'Agent Turn')
            for key, val in (field.items() if isinstance(field, dict) else [(field, value)]):
                setattr(self.docs[name], key, val)
        self.f.db.set_value = save
        self.decisions.wants_resolution = Mock(return_value=True)
        api = ModuleType('suke_ops.api')
        api.get_analysis = Mock(side_effect=lambda name, scope: {
            'context': {'case_name': name, 'scope': scope}, 'plans': [], 'agent': {'old_prose': True}})
        finance = ModuleType('suke_ops.finance_sync.api')
        finance.agent_tools = lambda runtime: []
        with patch.dict(sys.modules, {'suke_ops.decision_result': self.decisions,
                                      'suke_ops.api': api, 'suke_ops.finance_sync.api': finance}), \
                patch.object(self.hub, 'module_board', return_value={}), \
                patch.object(self.hub, 'latest_reports', return_value=[]), \
                patch.object(self.hub.runtime, 'respond', side_effect=respond):
            self.hub.run_turn('TURN')
        self.decisions.act.assert_not_called()
        self.assertEqual(self.writes, [])
        return api.get_analysis, json.loads(self.turn.result)

    def test_bound_case_reaches_initial_evidence_and_only_current_stock_is_compared(self):
        seen = {}
        def respond(module, question, history, specs, dispatch, trace, **kwargs):
            seen.update(deepcopy(kwargs['initial_evidence']))
            self.assertEqual(kwargs['tool_budget'][0], 46)  # Board and bound-case pre-read.
            # A repeated authorized tool read must not duplicate the binding.
            dispatch('buying', 'compare_supply_case', {'name': 'CASE-A'})
            dispatch('control', 'report_action', {'status': 'analysis_only',
                     'reason': '两种现存分配顺序已核实', 'next_step': '等待选择后送审'})
            return '等待用户选择方案。'
        reads, result = self.run_bound_turn(respond)
        self.assertEqual(self.turn.state, 'done')
        self.assertEqual(seen['bound_supply_case'], 'CASE-A')
        self.assertEqual(seen['supply_resolution']['context'],
                         {'case_name': 'CASE-A', 'scope': 'on_hand_only'})
        self.assertNotIn('agent', seen['supply_resolution'])
        self.assertEqual(reads.call_count, 2)
        for call in reads.call_args_list:
            self.assertEqual(call.args, ('CASE-A',))
            self.assertEqual(call.kwargs, {'scope': 'on_hand_only'})
        self.assertEqual(result['resolution_context'], {'explicit_case': 'CASE-A', 'case_names': ['CASE-A']})
        self.assertEqual(self.decisions.build.call_args.args[1]['resolution_context'], result['resolution_context'])

    def test_bound_case_rejects_other_case_and_failure_keeps_original_context(self):
        def respond(module, question, history, specs, dispatch, trace, **kwargs):
            self.assertEqual(kwargs['initial_evidence']['bound_supply_case'], 'CASE-A')
            dispatch('buying', 'compare_supply_case', {'name': 'CASE-B'})
            self.fail('A comparison outside the explicit case must not succeed')
        reads, result = self.run_bound_turn(respond)
        self.assertEqual(self.turn.state, 'failed')
        reads.assert_called_once_with('CASE-A', scope='on_hand_only')
        self.assertEqual(result['resolution_context'], {'explicit_case': 'CASE-A', 'case_names': ['CASE-A']})
        self.assertEqual(result['error_type'], 'Invalid')
        self.assertTrue(any(row['kind'] == 'failed' and row['tool'] == 'compare_supply_case'
                            for row in result['events']))
        self.assertIn('resolution', result)  # Model failure does not hide the verified case.


if __name__ == '__main__':
    unittest.main()
