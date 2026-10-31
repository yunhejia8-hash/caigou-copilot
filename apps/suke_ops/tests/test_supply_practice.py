"""Local practice registry checks: entirely in-memory, no ERP or network."""
from copy import deepcopy
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


class Invalid(Exception):
    pass


class Denied(Invalid):
    pass


class Doc(NS):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def check_permission(self, action):
        if self.get('denied'):
            raise Denied('denied')


class PracticeTests(unittest.TestCase):
    def setUp(self):
        f = self.f = ModuleType('frappe')
        f.local, f.session = NS(site='focus.local'), NS(user='Administrator')
        f.PermissionError = Denied
        f.throw = lambda message, exception=Invalid: (_ for _ in ()).throw(exception(message))
        f.has_permission = lambda *a, **k: True
        self.case = Doc(name='CASE', company='Sandbox Company', item_code='RAW', warehouse='STOCK',
                        purchase_order='PO', priority_sales_order='SO-A', is_simulation=1)
        self.manifest = {
            'dataset_id': 'SUPPLY-PRACTICE-USER', 'tag': 'USER', 'version': 1, 'site': 'focus.local',
            'label': '本地虚拟数据实操 · USER', 'company': 'Sandbox Company', 'case_name': 'CASE',
            'raw_item': 'RAW', 'stock_warehouse': 'STOCK', 'quality_warehouse': 'QUALITY',
            'item_codes': ['RAW', 'FG-A', 'FG-B'], 'warehouses': ['STOCK', 'QUALITY', 'FG'],
            'parties': {'supplier': 'VIRTUAL-SUPPLIER'},
            'source_lines': [
                {'doctype': 'Sales Order', 'document': 'SO-C', 'line': 'C-LINE', 'item_code': 'RAW', 'warehouse': 'STOCK'},
                {'doctype': 'Work Order', 'document': 'WO-A', 'line': 'A-LINE', 'item_code': 'RAW', 'warehouse': 'STOCK'},
                {'doctype': 'Work Order', 'document': 'WO-B', 'line': 'B-LINE', 'item_code': 'RAW', 'warehouse': 'STOCK'},
            ],
            'sales_orders': [{'name': 'SO-A'}, {'name': 'SO-B'}, {'name': 'SO-C'}],
            'work_orders': [{'name': 'WO-A', 'sales_order': 'SO-A', 'sales_order_item': 'FG-A-LINE'},
                            {'name': 'WO-B', 'sales_order': 'SO-B', 'sales_order_item': 'FG-B-LINE'}],
            'purchase_orders': [{'name': 'PO', 'approval': 'APP', 'items': [
                {'line': 'PO-LINE', 'item_code': 'RAW', 'warehouse': 'STOCK',
                 'material_request': 'MR', 'material_request_item': 'MR-LINE'}]}],
            'stock_entries': [{'name': 'OPENING-620'}, {'name': 'OPENING-160'}],
            'initial_reservations': [{'name': 'C-RESERVATION'}],
        }
        self.registry = Doc(name='SUPPLY-PRACTICE-USER', dataset_key='SUPPLY-PRACTICE-USER', tag='USER',
                            active=1, company='Sandbox Company', supply_case='CASE',
                            manifest_json=json.dumps(self.manifest))
        self.po = Doc(name='PO', company='Sandbox Company', supplier='VIRTUAL-SUPPLIER', items=[
            Doc(name='PO-LINE', item_code='RAW', warehouse='STOCK', material_request='MR', material_request_item='MR-LINE')])
        self.docs = {
            ('Supply Practice Dataset', self.registry.name): self.registry,
            ('Supply Case', 'CASE'): self.case,
            ('Purchase Order', 'PO'): self.po,
            ('Sales Order', 'SO-C'): Doc(name='SO-C', company='Sandbox Company', items=[
                Doc(name='C-LINE', item_code='RAW', warehouse='STOCK')]),
            ('Warehouse', 'QUALITY'): Doc(name='QUALITY', company='Sandbox Company'),
            ('Bin', 'QUALITY-BIN'): Doc(name='QUALITY-BIN', actual_qty=160),
            ('Stock Reservation Entry', 'C-RESERVATION'): Doc(name='C-RESERVATION', company='Sandbox Company',
                item_code='RAW', warehouse='STOCK', voucher_type='Sales Order', voucher_no='SO-C', voucher_detail_no='C-LINE'),
        }
        for role in ('A', 'B'):
            self.docs['Work Order', 'WO-' + role] = Doc(name='WO-' + role, company='Sandbox Company',
                sales_order='SO-' + role, sales_order_item='FG-' + role + '-LINE', required_items=[
                    Doc(name=role + '-LINE', item_code='RAW', source_warehouse='STOCK')])
        self.ctx = {'item_code': 'RAW', 'warehouse': 'STOCK', 'stock_uom': 'Nos', 'actual_qty': 620, 'blocked_qty': 0,
            'demands': [{'document_type': 'Work Order', 'document': 'WO-A', 'id': 'A-LINE', 'qty': 240},
                        {'document_type': 'Work Order', 'document': 'WO-B', 'id': 'B-LINE', 'qty': 320},
                        {'document_type': 'Sales Order', 'document': 'SO-C', 'id': 'C-LINE', 'qty': 420}],
            'reservations': [{'id': 'C-RESERVATION', 'document_type': 'Sales Order',
                              'document': 'SO-C', 'demand_id': 'C-LINE', 'qty': 420}]}
        self.metadata = {'Supply Practice Dataset', 'Supply Case', 'Supply Decision', 'Operations Approval'}
        self.calls = []
        f.db = NS(exists=self.exists, get_value=self.value, get_single_value=lambda *a: 1,
                  savepoint=lambda *a: self.calls.append('savepoint'),
                  rollback=lambda **k: self.calls.append('rollback'),
                  sql=self.sql)
        f.get_doc = lambda doctype, name=None: Doc(**doctype) if isinstance(doctype, dict) else self.docs[doctype, name]
        f.get_list = self.listing
        f.get_all = lambda *a, **k: [NS(name='QUALITY-BIN')]
        utils = NS(getdate=lambda v: date.fromisoformat(v), nowdate=lambda: '2026-11-01',
                   now_datetime=lambda: datetime(2026, 11, 1, 9))
        path = Path(__file__).resolve().parents[1] / 'suke_ops' / 'supply_practice.py'
        spec = importlib.util.spec_from_file_location('_practice_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'frappe': f, 'frappe.utils': utils}):
            spec.loader.exec_module(self.module)

    def exists(self, kind, filters):
        if kind == 'DocType':
            return filters in self.metadata
        return (kind, filters) in self.docs if isinstance(filters, str) else False

    def value(self, kind, filters, field):
        if kind == 'Supply Practice Dataset':
            if all(self.registry.get(key) == value for key, value in filters.items()):
                return self.registry.get(field)
        return None

    def listing(self, kind, **kwargs):
        self.calls.append(('listing', kwargs))
        return [NS(supply_case='CASE')]

    def sql(self, query, values, **kwargs):
        self.calls.append(('sql', query, values))
        return [dict(vars(self.registry))] if values == self.registry.supply_case else []

    def update_manifest(self):
        self.registry.manifest_json = json.dumps(self.manifest)

    def test_context_requires_server_registration_and_keeps_synthetic_flag(self):
        info = self.module.context(self.case)
        self.assertTrue(info['registered'])
        self.assertEqual(info['scope'], 'isolated_erp')
        self.assertEqual(self.case.is_simulation, 1)
        info['manifest']['item_codes'].append('FOREIGN')
        self.assertNotIn('FOREIGN', self.module.context(self.case)['item_codes'])
        unregistered = deepcopy(self.case)
        unregistered.name = 'UNREGISTERED'
        self.assertIsNone(self.module.context(unregistered))

    def test_wrong_site_or_disabled_registry_never_authorizes_execution(self):
        self.f.local.site = 'production.local'
        self.assertIsNone(self.module.context(self.case))
        self.f.local.site = 'focus.local'
        self.registry.active = 0
        self.assertIsNone(self.module.context(self.case))

    def test_registered_case_identity_mutations_fail_closed(self):
        for field, value in [('company', 'Real Company'), ('item_code', 'REAL'), ('warehouse', 'REAL-WH'),
                             ('purchase_order', 'REAL-PO'), ('is_simulation', 0)]:
            with self.subTest(field=field):
                altered = deepcopy(self.case)
                setattr(altered, field, value)
                with self.assertRaises(Invalid):
                    self.module.context(altered)

    def test_manifest_site_or_dataset_forgery_is_rejected(self):
        for field, value in [('site', 'production.local'), ('dataset_id', 'OTHER'), ('case_name', 'OTHER')]:
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                self.update_manifest()
                with self.assertRaises(Invalid):
                    self.module.context(self.case)
                self.manifest[field] = original
                self.update_manifest()

    def test_read_permissions_are_enforced(self):
        self.registry.denied = True
        with self.assertRaises(Denied):
            self.module.context(self.case)

    def test_current_user_family_is_ordered_by_newest_registration(self):
        self.assertEqual(self.module.current_case(), 'CASE')
        params = self.calls[-1][1]
        self.assertEqual(params['order_by'], 'creation desc')
        self.assertEqual(params['or_filters'], [{'tag': 'USER'}, {'tag': ['like', 'USER-%']}])
        self.assertEqual(params['limit_page_length'], 1)
        self.module.current_case('QA')
        self.assertEqual(self.calls[-1][1]['filters'], {'active': 1, 'tag': 'QA'})

    def test_snapshot_accepts_registered_sources_and_later_valid_reservations(self):
        self.assertTrue(self.module.validate_snapshot(self.case, self.ctx)['registered'])
        self.docs['Stock Reservation Entry', 'NEW-RESERVATION'] = Doc(name='NEW-RESERVATION',
            company='Sandbox Company', item_code='RAW', warehouse='STOCK', voucher_type='Work Order',
            voucher_no='WO-A', voucher_detail_no='A-LINE')
        self.ctx['reservations'].append({'id': 'NEW-RESERVATION', 'document_type': 'Work Order',
                                        'document': 'WO-A', 'demand_id': 'A-LINE', 'qty': 200})
        self.assertTrue(self.module.validate_snapshot(self.case, self.ctx)['registered'])

    def test_snapshot_rejects_unregistered_demand_before_reading_it(self):
        self.ctx['demands'].append({'document_type': 'Sales Order', 'document': 'REAL-SO', 'id': 'REAL-LINE'})
        with self.assertRaises(Invalid):
            self.module.validate_snapshot(self.case, self.ctx)

    def test_snapshot_rejects_extra_source_lines_and_work_order_relink(self):
        wo = self.docs['Work Order', 'WO-A']
        wo.required_items.append(Doc(name='UNREGISTERED', item_code='REAL', source_warehouse='REAL-WH'))
        with self.assertRaises(Invalid):
            self.module.validate_snapshot(self.case, self.ctx)
        wo.required_items.pop()
        wo.sales_order = 'REAL-SO'
        with self.assertRaises(Invalid):
            self.module.validate_snapshot(self.case, self.ctx)

    def test_snapshot_rejects_reservation_outside_registered_company_or_row(self):
        reservation = self.docs['Stock Reservation Entry', 'C-RESERVATION']
        reservation.company = 'Real Company'
        with self.assertRaises(Invalid):
            self.module.validate_snapshot(self.case, self.ctx)
        reservation.company = 'Sandbox Company'
        reservation.voucher_detail_no = 'REAL-LINE'
        with self.assertRaises(Invalid):
            self.module.validate_snapshot(self.case, self.ctx)

    def test_order_requires_registered_supplier_and_every_registered_line(self):
        self.assertTrue(self.module.check_order(self.case, self.po)['registered'])
        self.po.supplier = 'REAL-SUPPLIER'
        with self.assertRaises(Invalid):
            self.module.check_order(self.case, self.po)
        self.po.supplier = 'VIRTUAL-SUPPLIER'
        self.po.items.append(Doc(name='REAL-LINE', item_code='REAL', warehouse='REAL-WH'))
        with self.assertRaises(Invalid):
            self.module.check_order(self.case, self.po)

    def test_order_source_relink_is_rejected(self):
        self.po.items[0].material_request_item = 'REAL-MR-LINE'
        with self.assertRaises(Invalid):
            self.module.check_order(self.case, self.po)

    def test_real_unregistered_cases_are_not_assigned_practice_scope(self):
        self.case.name = 'REAL-CASE'
        self.case.is_simulation = 0
        self.assertIsNone(self.module.validate_snapshot(self.case, self.ctx))
        with self.assertRaises(Invalid):
            self.module.check_order(self.case, self.po)

    def test_lock_case_locks_inactive_rows_and_rereads_authorization(self):
        self.assertTrue(self.module.lock_case(self.case)['registered'])
        self.assertIn('for update', self.calls[-1][1])
        self.assertNotIn('active', self.calls[-1][1])
        self.registry.active = 0
        self.assertIsNone(self.module.lock_case(self.case))
        with self.assertRaises(Invalid):
            self.module.check_order(self.case, self.po)

    def test_lock_case_uses_current_locked_row_not_stale_active_snapshot(self):
        self.f.db.sql = lambda *a, **k: [{**vars(self.registry), 'active': 0}]
        self.assertTrue(self.module.context(self.case)['registered'])
        self.assertIsNone(self.module.lock_case(self.case))

    def test_manifest_changes_change_scope_version(self):
        before = self.module.context(self.case)['version']
        self.manifest['label'] = 'Updated registered evidence'
        self.update_manifest()
        self.assertNotEqual(before, self.module.context(self.case)['version'])

    def test_summary_uses_live_inventory_and_does_not_double_count_finished_sales(self):
        api = ModuleType('suke_ops.api')
        api.snapshot = lambda case, scope: self.ctx
        api._scope_read = lambda doc: doc.check_permission('read')
        with patch.dict(sys.modules, {'suke_ops.api': api}):
            result = self.module.summary(self.case)
            self.assertEqual([row['value'] for row in result['metrics']], [620, 420, 200, 160, 780, 560])
            self.ctx['actual_qty'] = 700
            self.docs['Bin', 'QUALITY-BIN'].actual_qty = 80
            result = self.module.summary(self.case)
            self.assertEqual([row['value'] for row in result['metrics']], [700, 420, 280, 80, 780, 560])
        self.assertTrue(any(row['doctype'] == 'Operations Approval' for row in result['sources']))

    def test_seed_retry_returns_registry_without_creating_or_resetting_records(self):
        self.assertEqual(self.module.seed()['dataset_id'], 'SUPPLY-PRACTICE-USER')
        self.assertEqual(self.calls, [])

    def test_seed_rejects_other_site_nonadmin_or_disabled_stock_setting_before_writes(self):
        self.f.local.site = 'production.local'
        with self.assertRaises(Denied):
            self.module.seed('NEW')
        self.f.local.site = 'focus.local'
        self.f.session.user = 'buyer'
        with self.assertRaises(Denied):
            self.module.seed('NEW')
        self.f.session.user = 'Administrator'
        self.f.db.get_single_value = lambda *a: 0
        with self.assertRaises(Invalid):
            self.module.seed('NEW')
        self.assertEqual(self.calls, [])

    def test_install_skips_other_sites_without_mutation(self):
        self.f.local.site = 'production.local'
        self.assertTrue(self.module.install()['skipped'])
        self.assertEqual(self.calls, [])

    def test_outbound_notification_prevents_seed_without_disabling_rule(self):
        original = self.f.db.exists
        self.f.db.exists = lambda kind, filters: True if (kind == 'DocType' and filters == 'Notification') or kind == 'Notification' else original(kind, filters)
        self.f.get_meta = lambda kind: NS(has_field=lambda name: True)
        with self.assertRaisesRegex(Invalid, '通知或Webhook'):
            self.module.seed('NEW')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
