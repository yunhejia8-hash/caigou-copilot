"""Decision boundary regression tests: in-memory documents, no model or ERP."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

from suke_ops.engine import compare, digest
import suke_ops


class Invalid(Exception):
    pass


class Denied(Invalid):
    pass


class Missing(Invalid):
    pass


class Doc(NS):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.meta = NS(fields=[], get_table_fields=lambda: [])
        self.denied = set()
        self.submissions = 0
        self.flags = NS()

    def get(self, key, default=None):
        return getattr(self, key, default)

    def check_permission(self, kind):
        if kind in self.denied:
            raise Denied('denied')

    def submit(self):
        self.check_permission('submit')
        self.submissions += 1
        self.docstatus = 1


class FakeFrappe(ModuleType):
    def __init__(self):
        super().__init__('frappe')
        self.session = NS(user='buyer')
        self.PermissionError, self.DoesNotExistError, self.ValidationError = Denied, Missing, Invalid
        self.docs, self.roles, self.locks = {}, ['Supply Planner', 'Supply Approver'], []
        self.permissions = {}
        self.db = NS(sql=lambda *a, **k: self.locks.append(a), exists=lambda *a, **k: None)

    @staticmethod
    def whitelist(*a, **k):
        return lambda f: f

    @staticmethod
    def throw(message, exception=Invalid):
        raise exception(message)

    def get_roles(self):
        return self.roles

    def has_permission(self, doctype, action, doc=None):
        return self.permissions.get((doctype, action), True) and (not doc or action not in doc.denied)

    def get_doc(self, kind, name=None):
        try:
            return self.docs[kind, name]
        except KeyError:
            raise Missing(name)

    def get_list(self, kind, filters, fields, **kwargs):
        return [doc for (dt, _), doc in self.docs.items() if dt == kind and
                all(doc.get(key) == value for key, value in filters.items())]


def load(name, filename):
    path = Path(__file__).resolve().parents[1] / 'suke_ops' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DecisionResultTests(unittest.TestCase):
    def setUp(self):
        self.f = FakeFrappe()
        self.utils = NS(now_datetime=lambda: datetime(2026, 11, 1, 9), flt=float)
        self.case = Doc(name='CASE', item_code='CONNECTOR', warehouse='WH', company='Company',
                        is_simulation=0, purchase_order='PO')
        self.ctx = {'scope': 'on_hand_only', 'stock_uom': '米', 'as_of': '2026-11-01T09:00:00+08:00',
            'actual_qty': 320, 'blocked_qty': 0,
            'demands': [{'id': 'SO-LINE', 'document': 'SO', 'document_type': 'Sales Order',
                         'kind': 'sales', 'qty': 300, 'need_by': '2026-11-02T17:00:00+08:00'},
                        {'id': 'WO-LINE', 'document': 'WO', 'document_type': 'Work Order',
                         'kind': 'production', 'qty': 300, 'min_batch_qty': 100,
                         'need_by': '2026-11-02T08:00:00+08:00'}],
            'reservations': [{'demand_id': 'OTHER', 'qty': 120}], 'supplies': []}
        self.turn = NS(owner='buyer', question='供应商600件连接器延期，可能先发200，销售生产争料')
        self.result = {'resolution_context': {'case_names': ['CASE']}}
        self.po = Doc(name='PO', doctype='Purchase Order', company='Company', docstatus=0, status='Draft',
                      supplier='Supplier', currency='CNY', grand_total=1000, terms='original',
                      items=[Doc(item_code='CONNECTOR', warehouse='WH', qty=50, uom='件', rate=20,
                                 amount=1000, schedule_date='2026-11-02')])
        self.f.docs['Supply Case', 'CASE'] = self.case
        self.f.docs['Purchase Order', 'PO'] = self.po
        self.approval = NS(document_hash=lambda po: digest([po.name, po.grand_total, po.terms]))
        self.api = NS(PLANNERS={'Supply Planner', 'Supply Approver', 'System Manager'},
                      loads=lambda value, default=None: json.loads(value) if value else default,
                      read_case=self.read_case, get_analysis=self.analysis,
                      propose=self.propose, transition=self.transition, execute=self.execute)
        self.proposals, self.executions = 0, 0
        with patch.dict(sys.modules, {'frappe': self.f, 'frappe.utils': self.utils}), \
                patch.object(suke_ops, 'api', self.api, create=True), \
                patch.object(suke_ops, 'approval', self.approval, create=True):
            self.mod = load('_decision_result_test', 'decision_result.py')

    def read_case(self, name):
        case = self.f.get_doc('Supply Case', name)
        case.check_permission('read')
        return case

    def analysis(self, name, scope):
        self.assertEqual(scope, 'on_hand_only')
        result = compare(self.ctx)
        for plan in result['plans']:
            plan['scope'] = scope
        return {'context': self.ctx, **result}

    def propose(self, name, plan, fingerprint, reason, scope):
        self.assertEqual(scope, 'on_hand_only')
        self.proposals += 1
        name = 'DEC-' + str(self.proposals)
        selected = next(p for p in self.analysis('CASE', scope)['plans'] if p['id'] == plan)
        self.f.docs['Supply Decision', name] = Doc(name=name, supply_case='CASE', selected_plan=plan,
            snapshot_hash=fingerprint, plan_json=json.dumps(selected), workflow_state='草稿',
            execution_result='{}', reason=reason)
        return name

    def transition(self, name, action, reason=None):
        self.f.get_doc('Supply Decision', name).workflow_state = {
            '提交审批': '待审批', '修正重提': '待审批', '批准': '已批准', '驳回': '已驳回'}[action]

    def execute(self, name):
        self.executions += 1
        self.f.get_doc('Supply Decision', name).workflow_state = '已执行'
        # Native reservation changes the live inventory fingerprint.
        self.ctx['reservations'].append({'demand_id': 'SO-LINE', 'qty': 200})

    def choose(self):
        card = self.mod.build(self.turn, self.result)
        self.result = self.mod.act(self.turn, self.result, 'choose', plan_id='sales_first',
                                  fingerprint=card['fingerprint'], reason='优先客户交付，确认本次库存边界')
        return self.result

    def approve_po(self):
        doc = Doc(name='APPROVAL', source_type='Purchase Order', source_name='PO', workflow_state='已批准',
                  source_hash=self.approval.document_hash(self.po))
        self.f.docs['Operations Approval', doc.name] = doc
        return doc

    def test_regular_chat_no_card_and_shortage_without_case_never_guesses(self):
        self.turn.question = '你好，介绍采购流程'
        self.assertIsNone(self.mod.build(self.turn, {}))
        self.turn.question = '供应商说600件晚三天，可能先发200'
        card = self.mod.build(self.turn, {})
        self.assertEqual(card['kind'], 'unbound')
        self.assertFalse(any(card['can'].values()))

    def test_two_options_count_only_free_stock_and_keep_unknown_cost(self):
        card = self.mod.build(self.turn, self.result)
        self.assertEqual(len(card['choices']), 2)
        for row in card['choices']:
            self.assertEqual(row['current_free_qty'], 200)
            self.assertEqual(sum(r['current_new_qty'] for r in row['allocations']), 200)
            self.assertIsNone(row['cost'])
        self.assertEqual(card['approved_orders'], [])
        self.assertEqual(card['case']['stock_uom'], '米')
        self.assertIn('200 米', card['choices'][0]['summary'])
        self.assertEqual(card['choices'][0]['allocations'][0]['stock_uom'], '米')

    def test_choice_approval_execution_is_idempotent_and_keeps_trace(self):
        self.choose()
        self.assertEqual(self.result['resolution']['state'], 'pending_approval')
        selected = deepcopy(self.result)
        self.choose()
        self.assertEqual(self.proposals, 1)
        self.result = self.mod.act(self.turn, self.result, 'approve')
        self.assertEqual(self.result['resolution']['state'], 'approved')
        self.result = self.mod.act(self.turn, self.result, 'execute')
        self.assertEqual(self.result['resolution']['state'], 'executed')
        self.mod.act(self.turn, self.result, 'execute')
        self.assertEqual(self.executions, 1)
        self.assertEqual(self.result['resolution']['selected_by'], 'buyer')
        self.assertIn('discussion', {**selected, 'discussion': []})

    def test_stale_choice_or_approved_decision_cannot_execute(self):
        old = self.mod.build(self.turn, self.result)['fingerprint']
        self.ctx['actual_qty'] += 1
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'choose', plan_id='sales_first', fingerprint=old, reason='先保客户')
        self.choose()
        self.result = self.mod.act(self.turn, self.result, 'approve')
        self.ctx['actual_qty'] += 1
        self.assertEqual(self.mod.build(self.turn, self.result)['state'], 'stale')
        with self.assertRaises(Denied):
            self.mod.act(self.turn, self.result, 'execute')
        self.assertEqual(self.executions, 0)

    def test_followup_only_receipt_does_not_claim_stock_was_reserved(self):
        self.choose()
        name = self.result['resolution']['decision']['name']
        decision = self.f.docs['Supply Decision', name]
        decision.workflow_state = '已执行'
        decision.execution_result = json.dumps({'reservations': [], 'tasks': ['FOLLOWUP']})
        card = self.mod.build(self.turn, self.result)
        self.assertIn('没有新增库存预留', card['summary'])
        self.assertEqual(card['execution']['tasks'], ['FOLLOWUP'])
        decision.execution_result = json.dumps({'reservations': ['SRE'], 'tasks': []})
        self.assertIn('已创建获批的现存库存预留', self.mod.build(self.turn, self.result)['summary'])

    def test_owner_and_approver_are_both_required(self):
        self.turn.owner = 'other'
        with self.assertRaises(Denied):
            self.mod.build(self.turn, self.result)
        self.turn.owner = 'buyer'
        self.choose()
        self.f.roles = ['Supply Planner']
        with self.assertRaises(Denied):
            self.mod.act(self.turn, self.result, 'approve')

    def test_revoked_case_permission_disables_every_action(self):
        self.choose()
        self.case.denied.add('read')
        card = self.mod.build(self.turn, self.result)
        self.assertIsNone(card['case'])
        self.assertFalse(any(card['can'].values()))

    def test_synthetic_case_cannot_submit_real_po(self):
        self.approve_po()
        self.case.is_simulation = 1
        card = self.mod.build(self.turn, self.result)
        self.assertEqual(card['kind'], 'synthetic')
        self.assertEqual(card['approved_orders'], [])
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='PO',
                         source_hash=self.approval.document_hash(self.po))
        self.assertEqual(self.po.submissions, 0)

    def test_synthetic_trace_builds_options_without_reading_real_documents(self):
        from suke_ops.trace_scenario import evidence
        result = {'synthetic': True, 'verified_evidence': evidence(False)}
        with patch.object(self.api, 'read_case', side_effect=AssertionError('No ERP access for a synthetic trace')):
            card = self.mod.build(self.turn, result)
        self.assertEqual(card['kind'], 'synthetic')
        self.assertEqual(len(card['choices']), 2)
        self.assertFalse(any(card['can'].values()))
        self.assertEqual(card['source_as_of'], result['verified_evidence']['as_of'])
        self.assertEqual(card['choices'][0]['current_free_qty'], 200)
        self.assertEqual(card['choices'][0]['shortage_qty'], 360)
        self.assertEqual(card['choices'][0]['conditional_supply_qty'], 560)
        self.assertEqual(card['choices'][0]['conditional_shortage_qty'], 0)
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, result, 'bind_case', case_name='CASE')

    def test_synthetic_missing_time_or_requirements_blocks_without_fabricating_record(self):
        from suke_ops.trace_scenario import evidence
        for field in ('as_of', 'requirements'):
            snapshot = evidence(False)
            snapshot.pop(field)
            card = self.mod.build(self.turn, {'synthetic': True, 'verified_evidence': snapshot})
            self.assertEqual(card['state'], 'blocked')
            self.assertEqual(card['choices'], [])
            self.assertNotIn('source_as_of', card)
            self.assertTrue(card['requirements'])

    def test_unverified_human_supplement_never_keeps_old_numeric_options(self):
        from suke_ops.trace_scenario import evidence
        snapshot = evidence(False)
        snapshot['human_sources'] = [{'text': '待核实'}]
        card = self.mod.build(self.turn, {'synthetic': True, 'verified_evidence': snapshot})
        self.assertEqual(card['state'], 'blocked')
        self.assertEqual(card['choices'], [])
        self.assertTrue(card['requirements'])

    def test_optional_execution_reason_is_validated_and_preserved(self):
        self.choose()
        self.result = self.mod.act(self.turn, self.result, 'approve')
        output = self.mod.act(self.turn, self.result, 'execute', reason=' 已核实仓库放行记录 ')
        self.assertEqual(output['resolution_actions'][-1]['reason'], '已核实仓库放行记录')
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, output, 'execute', reason='   ')

    def test_approval_revoked_while_acquiring_lock_prevents_submission(self):
        approved = self.approve_po()
        def lock(sql, *args):
            if 'tabOperations Approval' in sql:
                approved.workflow_state = '已驳回'
        self.f.db.sql = lock
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='PO', source_hash=approved.source_hash)
        self.assertEqual(self.po.submissions, 0)

    def test_approval_hash_and_native_submit_permissions_are_required(self):
        approved = self.approve_po()
        self.po.terms = 'changed terms'
        self.assertEqual(self.mod.build(self.turn, self.result)['approved_orders'], [])
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='PO', source_hash=approved.source_hash)
        self.po.terms = 'original'
        self.po.denied.add('submit')
        with self.assertRaises(Denied):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='PO', source_hash=approved.source_hash)
        self.assertEqual(self.po.submissions, 0)

    def test_approved_po_submit_does_not_duplicate_order_or_reuse_decision_approval(self):
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='PO', source_hash=self.approval.document_hash(self.po))
        approved = self.approve_po()
        output = self.mod.act(self.turn, self.result, 'submit_order', order_name='PO', source_hash=approved.source_hash)
        self.mod.act(self.turn, output, 'submit_order', order_name='PO', source_hash=approved.source_hash)
        self.assertEqual(self.po.submissions, 1)
        self.assertEqual(output['resolution']['approved_orders'][0]['docstatus'], 1)
        self.assertTrue(any('tabPurchase Order' in call[0] for call in self.f.locks))
        self.assertTrue(any('tabOperations Approval' in call[0] for call in self.f.locks))

    def test_arbitrary_order_or_action_arguments_are_rejected(self):
        approved = self.approve_po()
        with self.assertRaises(Denied):
            self.mod.act(self.turn, self.result, 'submit_order', order_name='ANOTHER', source_hash=approved.source_hash)
        with self.assertRaises(Invalid):
            self.mod.act(self.turn, self.result, 'choose', plan_id='sales_first', qty=999)


class OnHandScopeTests(unittest.TestCase):
    def setUp(self):
        self.f = FakeFrappe()
        self.f.get_all = lambda kind, **kw: []
        self.f.db.get_value = lambda kind, *a, **kw: NS(actual_qty=320, modified='v1') if kind == 'Bin' else '件'
        self.case = Doc(name='CASE', company='Company', item_code='CONNECTOR', warehouse='WH',
                        is_simulation=0, event_time='2026-11-01 09:00:00', blocked_qty=20,
                        supplier_result=json.dumps({'status': 'confirmed_simulation', 'original_qty': 600}),
                        possible_early_qty=200, early_arrival='2026-11-02T08:00:00+08:00',
                        priority_sales_order=None, purchase_order='PO')
        for kind, name in [('Company', 'Company'), ('Item', 'CONNECTOR'), ('Warehouse', 'WH')]:
            self.f.docs[kind, name] = Doc(name=name, company='Company', is_group=0, disabled=0)
        self.f.docs['Supply Case', 'CASE'] = self.case
        with patch.dict(sys.modules, {'frappe': self.f, 'frappe.utils': NS(flt=float, now_datetime=datetime.now)}):
            self.api = load('_on_hand_api_test', 'api.py')

    def test_on_hand_ignores_all_purchase_and_simulated_supply_without_querying_po(self):
        queried = []
        def read(kind, **kwargs):
            queried.append(kind)
            if kind == 'Purchase Order Item':
                raise AssertionError('Future PO pool must not be queried')
            return []
        self.f.get_all = read
        ctx = self.api.snapshot(self.case, scope='on_hand_only')
        self.assertEqual(ctx['supplies'], [])
        self.assertEqual(ctx['scope'], 'on_hand_only')
        self.assertEqual(compare(ctx)['plans'][0]['current_free_qty'], 300)
        self.assertNotIn('Purchase Order Item', queried)

    def test_on_hand_checks_stock_permission_and_warehouse_company(self):
        self.f.permissions['Stock Reservation Entry', 'read'] = False
        with self.assertRaises(Denied):
            self.api.snapshot(self.case, scope='on_hand_only')
        self.f.permissions.clear()
        self.f.docs['Warehouse', 'WH'].company = 'Other'
        with self.assertRaises(Invalid):
            self.api.snapshot(self.case, scope='on_hand_only')

    def test_simulation_cannot_use_new_proposal_scope(self):
        self.case.is_simulation = 1
        with self.assertRaises(Invalid):
            self.api.propose('CASE', 'sales_first', 'unused', 'reason', scope='on_hand_only')

    def test_missing_stock_unit_blocks_new_scope(self):
        self.f.db.get_value = lambda kind, *a, **kw: NS(actual_qty=320, modified='v1') if kind == 'Bin' else None
        with self.assertRaises(Invalid):
            self.api.snapshot(self.case, scope='on_hand_only')

    def test_partially_delivered_sales_order_reserves_full_remaining_quantity(self):
        line = Doc(name='SO-LINE', stock_qty=300, delivered_qty=100, conversion_factor=1,
                   delivery_date='2026-11-02')
        source = Doc(name='SO', doctype='Sales Order', company='Company', status='To Deliver',
                     items=[line], modified='v1')
        self.f.docs['Sales Order', 'SO'] = source
        self.case.blocked_qty = 0
        self.f.db.get_value = lambda kind, *a, **kw: NS(actual_qty=200, modified='v1') if kind == 'Bin' else '米'
        self.f.get_all = lambda kind, **kw: [NS(parent='SO', name='SO-LINE')] if kind == 'Sales Order Item' else []
        ctx = self.api.snapshot(self.case, scope='on_hand_only')
        self.assertEqual(ctx['demands'][0]['qty'], 200)
        plan = compare(ctx)['plans'][0]
        plan['scope'] = 'on_hand_only'
        decision = Doc(name='DEC', supply_case='CASE', selected_plan='sales_first', snapshot_hash=digest(ctx),
                       plan_json=json.dumps(plan), workflow_state='已批准', execution_result=None)
        decision.save = lambda: decision
        decision.add_comment = lambda *a: None
        self.f.docs['Supply Decision', 'DEC'] = decision
        original_get_doc, created = self.f.get_doc, []
        def get_doc(kind, name=None):
            if isinstance(kind, dict):
                self.assertEqual(kind['doctype'], 'Stock Reservation Entry')
                doc = Doc(name='SRE-1', **kind)
                doc.insert = lambda: doc
                def submit():
                    self.assertEqual(doc.voucher_qty, 300)
                    self.assertEqual(doc.reserved_qty, 200)
                    self.assertLessEqual(doc.reserved_qty, doc.voucher_qty - line.delivered_qty)
                    doc.submissions += 1
                doc.submit = submit
                created.append(doc)
                return doc
            return original_get_doc(kind, name)
        self.f.get_doc = get_doc
        native = NS(get_available_qty_to_reserve=lambda *a: 200)
        with patch.dict(sys.modules, {'erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry': native}):
            receipt = self.api.execute('DEC')
            self.assertEqual(self.api.execute('DEC'), receipt)
        self.assertEqual(receipt['reservations'], ['SRE-1'])
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].submissions, 1)

    def test_scope_is_fingerprinted_and_approval_revalidates_same_scope(self):
        ctx = self.api.snapshot(self.case, scope='on_hand_only')
        legacy = {k: v for k, v in ctx.items() if k != 'scope'}
        self.assertNotEqual(digest(ctx), digest(legacy))
        plan = compare(ctx)['plans'][0]
        plan['scope'] = 'on_hand_only'
        decision = Doc(supply_case='CASE', selected_plan='sales_first', snapshot_hash=digest(ctx),
                       plan_json=json.dumps(plan), workflow_state='待审批', reason='reason')
        decision.get_doc_before_save = lambda: None
        self.api.validate_decision(decision)
        self.case.blocked_qty += 1
        with self.assertRaises(Invalid):
            self.api.validate_decision(decision)

    def test_execution_rechecks_same_scope_and_returns_same_receipt_on_retry(self):
        ctx = self.api.snapshot(self.case, scope='on_hand_only')
        plan = compare(ctx)['plans'][0]
        plan['scope'] = 'on_hand_only'
        decision = Doc(name='DEC', supply_case='CASE', selected_plan='sales_first', snapshot_hash=digest(ctx),
                       plan_json=json.dumps(plan), workflow_state='已批准', execution_result=None)
        decision.save = lambda: decision
        decision.add_comment = lambda *a: None
        self.f.docs['Supply Decision', 'DEC'] = decision
        self.case.blocked_qty += 1
        with self.assertRaises(Invalid):
            self.api.execute('DEC')
        self.assertEqual(decision.workflow_state, '已批准')
        self.case.blocked_qty -= 1
        receipt = self.api.execute('DEC')
        self.assertEqual(receipt['reservations'], [])
        self.assertEqual(receipt['executed_by'], 'buyer')
        self.assertEqual(self.api.execute('DEC'), receipt)
        self.assertEqual(decision.workflow_state, '已执行')


if __name__ == '__main__':
    unittest.main()
