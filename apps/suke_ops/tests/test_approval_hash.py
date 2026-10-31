"""Approval content regression checks using synthetic in-memory documents only."""
from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from suke_ops.engine import digest


def load_approval():
    frappe = ModuleType('frappe')
    frappe.whitelist = lambda *args, **kwargs: lambda function: function
    frappe.as_json = lambda value: json.dumps(value, default=str)
    api = ModuleType('suke_ops.api')
    api.require = lambda *args, **kwargs: None
    path = Path(__file__).resolve().parents[1] / 'suke_ops' / 'approval.py'
    spec = importlib.util.spec_from_file_location('_approval_hash_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'frappe': frappe, 'suke_ops.api': api}):
        spec.loader.exec_module(module)
    return module


def legacy_hash(doc):
    """Frozen pre-extension contract: empty new fields must retain this digest."""
    fields = ('company', 'supplier', 'customer', 'currency', 'conversion_rate', 'grand_total', 'schedule_date',
              'delivery_date', 'production_item', 'bom_no', 'qty', 'planned_start_date', 'planned_end_date',
              'source_warehouse', 'fg_warehouse', 'wip_warehouse', 'stock_entry_type', 'purpose')
    row_fields = ('item_code', 'qty', 'stock_qty', 'required_qty', 'rate', 'amount', 'schedule_date', 'delivery_date',
                  'warehouse', 'source_warehouse', 's_warehouse', 't_warehouse', 'uom', 'conversion_factor', 'basic_rate',
                  'charge_type', 'account_head', 'tax_amount', 'cost_center')
    payload = {field: doc.get(field) for field in fields}
    for table in ('items', 'required_items', 'taxes'):
        payload[table] = [{field: row.get(field) for field in row_fields} for row in doc.get(table, [])]
    return digest(json.loads(json.dumps(payload, default=str)))


class ApprovalHashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.approval = load_approval()

    def setUp(self):
        self.doc = {'doctype': 'Purchase Order', 'company': 'Synthetic Company',
                    'supplier': 'Synthetic Supplier', 'currency': 'CNY', 'conversion_rate': 1,
                    'grand_total': 1000, 'schedule_date': date(2026, 11, 5),
                    'items': [{'item_code': 'CONNECTOR', 'qty': 50, 'stock_qty': 50,
                               'uom': '件', 'conversion_factor': 1, 'rate': 20, 'amount': 1000,
                               'warehouse': 'Synthetic Warehouse', 'schedule_date': date(2026, 11, 5)}],
                    'taxes': []}

    def changed(self, before, after):
        self.assertNotEqual(self.approval.document_hash(before), self.approval.document_hash(after))

    def test_empty_new_fields_keep_legacy_approval_hash(self):
        expected = legacy_hash(self.doc)
        self.assertEqual(self.approval.document_hash(self.doc), expected)
        self.doc.update(terms='', tc_name=None, payment_terms_template='', incoterm='', named_place=None,
                        apply_discount_on='', discount_amount=0, additional_discount_percentage=0,
                        supplier_address='', shipping_address=None, buying_price_list='',
                        payment_schedule=[{'payment_term': '', 'description': None, 'discount': 0}])
        self.doc['items'][0].update(description='', brand=None, manufacturer='', manufacturer_part_no=None,
                                    supplier_part_no='', material_request='', material_request_item=None,
                                    sales_order='', sales_order_item=None, sales_order_packed_item='',
                                    supplier_quotation=None, supplier_quotation_item='')
        self.assertEqual(self.approval.document_hash(self.doc), expected)

    def test_empty_tax_terms_keep_legacy_hash(self):
        self.doc['taxes'] = [{'charge_type': 'Actual', 'account_head': 'Synthetic Tax',
                              'tax_amount': 10, 'rate': 0}]
        expected = legacy_hash(self.doc)
        self.doc['taxes'][0].update(add_deduct_tax='', included_in_print_rate=0, description=None, category='')
        self.assertEqual(self.approval.document_hash(self.doc), expected)

    def test_specification_changes_invalidate_approval(self):
        for field in ('description', 'brand', 'manufacturer', 'manufacturer_part_no', 'supplier_part_no'):
            with self.subTest(field=field):
                before = deepcopy(self.doc)
                before['items'][0][field] = 'approved specification A'
                after = deepcopy(before)
                after['items'][0][field] = 'different specification B'
                self.changed(before, after)

    def test_adding_or_removing_specification_invalidates_approval(self):
        with_description = deepcopy(self.doc)
        with_description['items'][0]['description'] = 'Only approved manufacturer grade A'
        self.changed(self.doc, with_description)
        self.changed(with_description, self.doc)

    def test_source_document_and_line_changes_invalidate_approval(self):
        for field in ('material_request', 'material_request_item', 'sales_order', 'sales_order_item',
                      'sales_order_packed_item', 'supplier_quotation', 'supplier_quotation_item'):
            with self.subTest(field=field):
                before = deepcopy(self.doc)
                before['items'][0][field] = 'APPROVED-SOURCE-A'
                after = deepcopy(before)
                after['items'][0][field] = 'OTHER-SOURCE-B'
                self.changed(before, after)

    def test_moving_specification_to_another_identical_item_line_is_a_change(self):
        self.doc['items'].append(deepcopy(self.doc['items'][0]))
        self.doc['items'][0]['description'] = 'For approved requirement A'
        after = deepcopy(self.doc)
        after['items'][1]['description'] = after['items'][0].pop('description')
        self.changed(self.doc, after)

    def test_terms_and_incoterm_changes_invalidate_approval(self):
        for field in ('terms', 'tc_name', 'incoterm', 'named_place', 'payment_terms_template'):
            with self.subTest(field=field):
                before = deepcopy(self.doc)
                before[field] = 'approved A'
                after = deepcopy(before)
                after[field] = 'changed B'
                self.changed(before, after)

    def test_payment_schedule_changes_invalidate_approval(self):
        self.doc['payment_schedule'] = [{'payment_term': 'Net 30', 'description': 'Original terms',
            'due_date': date(2026, 12, 1), 'invoice_portion': 100, 'payment_amount': 1000,
            'mode_of_payment': 'Bank', 'discount_type': 'Percentage', 'discount': 0,
            'discount_date': date(2026, 11, 15)}]
        changes = {'payment_term': 'Prepaid', 'description': 'New conditions', 'due_date': date(2026, 11, 1),
                   'invoice_portion': 50, 'payment_amount': 500, 'mode_of_payment': 'Cash',
                   'discount_type': 'Amount', 'discount': 10, 'discount_date': date(2026, 11, 10)}
        for field, value in changes.items():
            with self.subTest(field=field):
                after = deepcopy(self.doc)
                after['payment_schedule'][0][field] = value
                self.changed(self.doc, after)
        after = deepcopy(self.doc)
        after['payment_schedule'] = []
        self.changed(self.doc, after)

    def test_tax_rate_change_invalidates_approval_even_if_amount_is_unchanged(self):
        self.doc['taxes'] = [{'charge_type': 'On Net Total', 'account_head': 'Synthetic Tax',
                              'tax_amount': 100, 'rate': 10}]
        after = deepcopy(self.doc)
        after['taxes'][0]['rate'] = 13
        self.changed(self.doc, after)

    def test_modified_creation_and_status_noise_does_not_invalidate_approval(self):
        self.doc['items'][0].update(description='Approved connector grade', manufacturer_part_no='MPN-A',
                                    material_request='MR-A', material_request_item='MR-LINE-A')
        self.doc.update(terms='Approved terms', incoterm='FCA',
                        payment_schedule=[{'payment_term': 'Net 30', 'payment_amount': 1000}])
        before = self.approval.document_hash(self.doc)
        for row in [self.doc, *self.doc['items'], *self.doc['payment_schedule']]:
            row.update(modified='2026-11-01 10:00:00', creation='2026-11-01 09:00:00',
                       modified_by='someone', owner='buyer', status='Updated', docstatus=1)
        self.assertEqual(self.approval.document_hash(self.doc), before)

    def test_purchase_only_specification_extension_preserves_other_document_hashes(self):
        self.doc['doctype'] = 'Sales Order'
        expected = self.approval.document_hash(self.doc)
        self.doc['items'][0].update(description='Other module text', manufacturer_part_no='Other MPN')
        self.assertEqual(self.approval.document_hash(self.doc), expected)


if __name__ == '__main__':
    unittest.main()
