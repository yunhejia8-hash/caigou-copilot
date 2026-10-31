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
        self.f.ValidationError = Invalid
        self.f.DoesNotExistError = type('Missing', (Invalid,), {})
        self.f.whitelist = lambda *a, **kw: lambda fn: fn
        self.f.throw = lambda message, exception=Invalid: self.fail_with(message, exception)
        self.f.get_cached_value = lambda dt, name, field: {'user_type': 'System User', 'enabled': 1}[field]
        self.f.get_doc = lambda dt, name, **kw: self.docs[name]
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

    def prepare_practice_endpoint(self):
        """Only Agent Turn insert is supported; any ERP write/model request fails."""
        self.inserted = []
        self.practice_case = NS(name='PRACTICE-CASE', company='独立虚拟公司', owner='seed-admin',
                                creation='2026-11-01 08:00:00', modified_by='warehouse-user',
                                modified='2026-11-01 08:30:00')
        self.practice_summary = {'registered': True, 'dataset_id': 'registered-local-1',
            'scope': 'isolated_erp', 'label': '本地连接器测试',
            'metrics': [{'label': '自由库存', 'value': 192, 'unit': '件'}],
            'sources': [{'doctype': 'Supply Case', 'name': self.practice_case.name, 'label': '供应异常'}]}
        self.practice = NS(current_case=Mock(return_value=self.practice_case.name),
                           summary=Mock(side_effect=lambda case: deepcopy(self.practice_summary)),
                           seed=Mock(side_effect=AssertionError('Seeding is not an endpoint action')),
                           install=Mock(side_effect=AssertionError('Installation is not an endpoint action')))
        self.practice_api = ModuleType('suke_ops.api')
        self.practice_api.read_case = Mock(return_value=self.practice_case)
        self.practice_api.execute = Mock(side_effect=AssertionError('Opening cannot execute a plan'))
        for patcher in [patch.object(suke_ops, 'supply_practice', self.practice, create=True),
                        patch.dict(sys.modules, {'suke_ops.api': self.practice_api}),
                        patch.object(self.hub.runtime, 'configured', return_value=False),
                        patch.object(self.hub.runtime, 'respond', side_effect=AssertionError('Model must not run'))]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.decisions.build.return_value = {'kind': 'sandbox', 'state': 'needs_choice',
            'case': {'name': self.practice_case.name, 'is_simulation': True},
            'sandbox': {key: self.practice_summary[key] for key in ('registered', 'dataset_id', 'scope', 'label')},
            'can': {'choose': True, 'approve': False, 'execute': False}}
        self.f.get_all = Mock(return_value=[])
        self.f.db.get_value = lambda doctype, filters, field: next((doc.name for doc in self.docs.values()
            if all(getattr(doc, key, None) == value for key, value in filters.items())), None)
        self.hub.now_datetime = lambda: '2026-11-01 09:00:00'

        def get_doc(doctype, name=None, **kwargs):
            if not isinstance(doctype, dict):
                self.assertEqual(doctype, 'Agent Turn')
                return self.docs[name]
            self.assertEqual(doctype['doctype'], 'Agent Turn', 'The endpoint must not create ERP documents')
            doc = NS(**doctype)

            def insert(ignore_permissions=False):
                self.assertTrue(ignore_permissions)
                doc.name = 'PRACTICE-TURN-' + str(len(self.inserted) + 1)
                self.docs[doc.name] = doc
                self.inserted.append(doc)
                return doc
            doc.insert = insert
            return doc
        self.f.get_doc = get_doc

    def open_practice(self, **overrides):
        return self.hub.open_supply_practice(**{
            'conversation': 'b3ecf00f-1b31-4e90-9b1f-72ed3b8eaa01',
            'request_id': '7d848b7c-8cf9-46bd-9e13-7b2b3605c101', **overrides})

    def test_advertised_bear_client_version_passes_the_http_gate(self):
        self.f.local = NS(request=NS(method='POST'))
        with patch.object(self.hub.runtime, 'configured', side_effect=[True, False]), \
                patch.object(self.hub, 'practice_available', return_value=False):
            status = self.hub.chat_status()
            self.assertTrue(status['model_ready'])
            # 状态读取后配置消失时，应到达模型配置校验，而不是拒绝刚声明的协议。
            with self.assertRaisesRegex(Invalid, '尚未配置模型服务'):
                self.hub.start('查询采购订单', client_version=status['client_version'])
        self.assertEqual(self.writes, [])

    def test_invalid_or_missing_optional_practice_does_not_break_chat_status(self):
        practice = NS(current_case=Mock())
        with patch.object(suke_ops, 'supply_practice', practice, create=True), \
                patch.object(self.hub.runtime, 'configured', return_value=True):
            for error in (Invalid('注册已失效'), self.f.DoesNotExistError('来源已删除'), Denied('不可读取')):
                with self.subTest(error=type(error).__name__):
                    practice.current_case.side_effect = error
                    status = self.hub.chat_status()
                    self.assertTrue(status['model_ready'])
                    self.assertFalse(status['practice_available'])

    def test_registered_practice_returns_completed_owned_turn_and_actual_read_provenance_without_model(self):
        self.prepare_practice_endpoint()
        status = self.hub.chat_status()
        self.assertFalse(status['model_ready'])
        self.assertTrue(status['practice_available'])
        response = self.open_practice()
        self.assertEqual(response['state'], 'done')
        self.assertEqual(response['agent_module'], 'control')
        self.assertEqual(response['mode'], '程序核算 · 本地虚拟数据')
        self.assertEqual(response['resolution']['kind'], 'sandbox')
        self.assertEqual(response['practice']['case_name'], self.practice_case.name)
        self.assertEqual(response['practice']['company'], self.practice_case.company)
        self.assertEqual(response['practice']['metrics'][0]['value'], 192)  # Live value, not seeded 200.
        self.assertEqual(len(self.inserted), 1)
        saved = self.inserted[0]
        self.assertEqual(saved.owner, 'buyer')
        self.assertEqual(saved.conversation, 'b3ecf00f-1b31-4e90-9b1f-72ed3b8eaa01')
        self.assertEqual(json.loads(saved.result)['resolution_context']['explicit_case'], self.practice_case.name)
        event = response['events'][0]
        self.assertEqual(event['query'], {'doctype': 'Supply Case', 'name': self.practice_case.name,
                                        'scope': 'on_hand_only'})
        source = json.loads(event['result_excerpt'])['_provenance']
        self.assertEqual(source['created_by'], 'seed-admin')
        self.assertEqual(source['modified_by'], 'warehouse-user')
        self.assertEqual(source['read_at'], '2026-11-01 09:00:00')
        self.assertEqual(self.writes, [])
        self.hub.runtime.respond.assert_not_called()
        self.decisions.act.assert_not_called()
        self.practice_api.execute.assert_not_called()
        self.practice.seed.assert_not_called()
        self.practice.install.assert_not_called()

    def test_practice_request_is_idempotent_per_user_and_never_returns_another_owners_turn(self):
        self.prepare_practice_endpoint()
        first = self.open_practice()
        first_key = self.inserted[0].request_key
        repeated = self.open_practice()
        self.assertEqual(first['name'], repeated['name'])
        self.assertEqual(len(self.inserted), 1)
        self.assertEqual(self.locks, ['buyer', 'buyer'])
        self.f.session.user = 'other'
        other = self.open_practice()
        self.assertNotEqual(other['name'], first['name'])
        self.assertEqual(self.inserted[-1].owner, 'other')
        self.assertNotEqual(self.inserted[-1].request_key, first_key)
        self.f.db.get_value = lambda *args: first['name']  # A corrupt/forged lookup must still fail ownership.
        with self.assertRaises(Denied):
            self.open_practice()
        self.assertEqual(len(self.inserted), 2)

    def test_active_analysis_prevents_new_practice_but_a_retry_can_return_its_existing_turn(self):
        self.prepare_practice_endpoint()
        existing = self.open_practice()
        self.f.get_all.return_value = [{'name': 'RUNNING-TURN'}]
        retry = self.open_practice()
        self.assertEqual(retry['name'], existing['name'])
        with self.assertRaisesRegex(Invalid, '尚未完成'):
            self.open_practice(request_id='6cfffb86-e3b2-4f0b-a1ec-169b61475139')
        self.assertEqual(len(self.inserted), 1)
        self.assertEqual(self.f.get_all.call_args.kwargs['filters'],
                         {'owner': 'buyer', 'state': ['in', ['queued', 'running']]})

    def test_practice_without_registration_or_read_permission_cannot_create_an_entry(self):
        self.prepare_practice_endpoint()
        self.practice.current_case.return_value = None
        self.assertFalse(self.hub.chat_status()['practice_available'])
        with self.assertRaisesRegex(Invalid, '尚未初始化'):
            self.open_practice()
        self.assertEqual(self.inserted, [])
        self.practice_api.read_case.assert_not_called()
        self.practice.current_case.return_value = self.practice_case.name
        self.practice_api.read_case.side_effect = Denied('case not readable')
        with self.assertRaises(Denied):
            self.open_practice()
        self.assertEqual(self.inserted, [])
        self.practice.current_case.side_effect = Denied('registry not readable')
        self.assertFalse(self.hub.chat_status()['practice_available'])

    def test_malformed_practice_uuids_fail_before_locking_reading_or_saving(self):
        self.prepare_practice_endpoint()
        for field in ('conversation', 'request_id'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.open_practice(**{field: 'not-a-uuid'})
        self.assertEqual(self.locks, [])
        self.assertEqual(self.inserted, [])
        self.practice.current_case.assert_not_called()
        self.practice_api.read_case.assert_not_called()

    def test_practice_entry_rejects_non_sandbox_and_missing_registered_summary(self):
        self.prepare_practice_endpoint()
        for kind in ('erp', 'synthetic', 'unbound'):
            with self.subTest(kind=kind):
                self.decisions.build.return_value['kind'] = kind
                with self.assertRaisesRegex(Invalid, '注册或读取验证未通过'):
                    self.open_practice()
        self.decisions.build.return_value['kind'] = 'sandbox'
        self.practice.summary.side_effect = None
        self.practice.summary.return_value = None
        with self.assertRaisesRegex(Invalid, '注册或读取验证未通过'):
            self.open_practice()
        self.assertEqual(self.inserted, [])

    def test_practice_entry_requires_exact_registered_isolated_dataset_contract(self):
        self.prepare_practice_endpoint()
        original = deepcopy(self.decisions.build.return_value)
        for field, value in [('registered', False), ('registered', 1), ('scope', 'ordinary_erp'), ('dataset_id', '')]:
            with self.subTest(field=field, value=value):
                self.decisions.build.return_value = deepcopy(original)
                self.decisions.build.return_value['sandbox'][field] = value
                with self.assertRaises(Invalid):
                    self.open_practice()
        self.assertEqual(self.inserted, [])

    def test_practice_metadata_must_match_the_registered_dataset_and_case(self):
        self.prepare_practice_endpoint()
        original = deepcopy(self.practice_summary)
        for field, value in [('registered', False), ('registered', 1), ('scope', 'ordinary_erp'),
                             ('dataset_id', ''), ('dataset_id', 'different-registered-dataset')]:
            with self.subTest(field=field, value=value):
                self.practice_summary = {**original, field: value}
                with self.assertRaises(Invalid):
                    self.open_practice()
        self.practice_summary = original
        wrong_case = self.hub.with_resolution(self.turn, {})
        wrong_case['practice']['case_name'] = 'OTHER-CASE'
        with patch.object(self.hub, 'with_resolution', return_value=wrong_case), self.assertRaises(Invalid):
            self.open_practice()
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.writes, [])


if __name__ == '__main__':
    unittest.main()
