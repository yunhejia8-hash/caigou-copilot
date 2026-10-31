"""离线验证金蝶只读协议、金额精度、分页与失败恢复边界。"""
import json
import unittest
from decimal import Decimal

from suke_ops.finance_sync.core import normalize, synchronize, validate_binding
from suke_ops.finance_sync.connectors import KingdeeConnector, MockConnector, connector_for


def binding():
    return {'form_id': 'AP_Payable', 'fields': {
        'source_id': 'FID', 'number': 'FBillNo', 'source_org': 'FOrgId.FNumber',
        'modified_at': 'FModifyDate', 'source_status': 'FDocumentStatus',
        'supplier_code': 'FSupplierId.FNumber', 'currency': 'FCurrencyId.FNumber',
        'amount': 'FALLAMOUNT', 'outstanding': 'FUnpaidAmount'},
        'status_map': {'C': 'active', 'Z': 'cancelled'}, 'grain': 'document'}


class SDK:
    def __init__(self, replies):
        self.replies, self.queries = iter(replies), []

    def ExecuteBillQuery(self, data):
        self.queries.append(json.loads(data))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value


class FinanceTests(unittest.TestCase):
    def test_document_grain_rejects_line_ids_and_duplicate_headers(self):
        mapping=binding();mapping['fields']['line_id']='FEntryId'
        with self.assertRaises(ValueError):validate_binding('payable',mapping)
        row=MockConnector('100').fetch('payable',None,'2026-10-31T00:00:00+00:00',0,1)[0]
        with self.assertRaises(ValueError):normalize('payable',dict(row,line_id='entry1'),'100','+08:00')
        class Duplicates(MockConnector):
            def fetch(self,*args):return [row,dict(row)]
        with self.assertRaises(ValueError):synchronize(Duplicates('100'),'payable','100','+08:00',None,'2026-10-31T00:00:00+00:00',lambda r:None)

    def test_subsecond_source_versions_and_query_bounds_are_preserved(self):
        from suke_ops.finance_sync.core import timestamp
        earlier=timestamp('2026-10-30T00:00:00.100+00:00')
        later=timestamp('2026-10-30T00:00:00.900+00:00')
        self.assertLess(earlier,later)
        sdk=SDK(['[]']);KingdeeConnector(sdk,binding(),'100','+08:00').fetch('payable',earlier,later,0,2)
        self.assertIn('08:00:00.100000',sdk.queries[0]['FilterString'])
        self.assertIn('08:00:00.900000',sdk.queries[0]['FilterString'])

    def test_sdk_read_only_query_mapping_and_org_window(self):
        sdk = SDK([json.dumps([['1', 'AP1', '100', '2026-10-30 08:00:00', 'C', 'S1', 'CNY', '0.10', '0.10']])])
        c = KingdeeConnector(sdk, binding(), '100', '+08:00')
        rows = c.fetch('payable', '2026-10-29T00:00:00+00:00', '2026-10-31T00:00:00+00:00', 0, 2)
        row = normalize('payable', rows[0], '100', '+08:00')
        self.assertEqual(row['amount'], '0.10')
        self.assertEqual(row['modified_at'], '2026-10-30T00:00:00+00:00')
        q = sdk.queries[0]
        self.assertIn("FOrgId.FNumber='100'", q['FilterString'])
        self.assertIn('2026-10-29 08:00:00', q['FilterString'])
        self.assertEqual(q['Limit'], 2)
        self.assertEqual(row['state'], 'active')

    def test_failed_business_response_never_becomes_empty_success_or_exposes_secret(self):
        for reply in ({'Result': {'ResponseStatus': {'IsSuccess': False, 'Errors': [{'Message': 'SECRET'}]}}},
                      [{'Result': {'ResponseStatus': {'IsSuccess': False}}}],
                      [['too', 'short']], RuntimeError('SECRET')):
            c = KingdeeConnector(SDK([reply]), binding(), '100', '+08:00')
            with self.assertRaisesRegex(ValueError, '金蝶') as e:
                c.fetch('payable', None, '2026-10-31T00:00:00+00:00', 0, 10)
            self.assertNotIn('SECRET', str(e.exception))

    def test_mapping_and_values_fail_closed(self):
        for change in ({'form_id': 'AP;DROP'}, {'grain': 'line'},
                       {'fields': {'source_id': 'FID'}}, {'status_map': {'C': 'invented'}}):
            with self.assertRaises(ValueError):
                validate_binding('payable', dict(binding(), **change))
        for changes in ({'source_org': '200'}, {'amount': 'NaN'}, {'outstanding': 'Infinity'},
                        {'modified_at': 'yesterday'}, {'source_id': ''}, {'state': 'unmapped'}):
            row = MockConnector('100').fetch('payable', None, '2026-10-31T00:00:00+00:00', 0, 1)[0]
            with self.assertRaises(ValueError):
                normalize('payable', dict(row, **changes), '100', '+08:00')

    def test_paginated_replay_is_idempotent_and_preserves_decimal_and_cancellation(self):
        rows = {}
        def write(row):
            rows[(row['source_id'], row['line_id'])] = row
        c = MockConnector('100')
        for _ in range(3):
            result = synchronize(c, 'payable', '100', '+08:00', None,
                                 '2026-10-31T00:00:00+00:00', write, page_size=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(result['read'], 3)
        active = [r for r in rows.values() if r['state'] == 'active']
        self.assertEqual(sum((Decimal(r['outstanding']) for r in active), Decimal(0)), Decimal('0.30'))
        self.assertEqual(len([r for r in rows.values() if r['state'] == 'cancelled']), 1)

    def test_page_failure_and_row_cap_never_return_checkpoint(self):
        class Broken(MockConnector):
            def fetch(self, entity, since, until, offset, limit):
                if offset:
                    raise ValueError('模拟中断')
                return super().fetch(entity, since, until, offset, limit)
        with self.assertRaisesRegex(ValueError, '中断'):
            synchronize(Broken('100'), 'payable', '100', '+08:00', None,
                        '2026-10-31T00:00:00+00:00', lambda r: None, page_size=2)
        with self.assertRaisesRegex(ValueError, '上限'):
            synchronize(MockConnector('100'), 'payable', '100', '+08:00', None,
                        '2026-10-31T00:00:00+00:00', lambda r: None, page_size=1, max_pages=1)

    def test_unimplemented_vendors_are_explicit_and_mock_never_opens_network(self):
        for vendor in ('yonyou', 'sap'):
            with self.assertRaisesRegex(ValueError, '尚未实现'):
                connector_for(vendor, 'mock', '100', '+08:00')
        self.assertIsInstance(connector_for('kingdee', 'mock', '100', '+08:00'), MockConnector)


if __name__ == '__main__':
    unittest.main()
