import copy
import unittest
from suke_ops.engine import compare, allocate, negotiate_simulated


def fixture():
    return {'as_of': '2026-10-30T09:00:00+08:00', 'actual_qty': 320, 'blocked_qty': 0,
            'demands': [{'id': 'sales', 'kind': 'sales', 'qty': 300, 'need_by': '2026-10-31T17:00:00+08:00'},
                        {'id': 'protected', 'kind': 'sales', 'qty': 120, 'need_by': '2026-10-31T17:00:00+08:00'},
                        {'id': 'wo', 'kind': 'production', 'qty': 300, 'need_by': '2026-10-31T08:00:00+08:00', 'min_batch_qty': 100, 'per_unit': 2, 'root_sales_order': 'finished-so', 'other_materials_ready': True}],
            'reservations': [{'demand_id': 'protected', 'qty': 120}],
            'supplies': [{'id': 'po', 'qty': 600, 'eta': '2026-11-03T08:00:00+08:00', 'confirmed': True},
                         {'id': 'possible', 'qty': 200, 'eta': '2026-10-31T07:00:00+08:00', 'confirmed': False}]}


class SupplyTests(unittest.TestCase):
    def test_delay_and_tentative(self):
        result = compare(fixture())
        self.assertEqual([p['shortage_qty'] for p in result['plans']], [400, 400])
        self.assertEqual(result['plans'][0]['current_free_qty'], 200)

    def test_existing_reservation_not_stolen(self):
        for plan in compare(fixture())['plans']:
            protected = next(r for r in plan['allocations'] if r['id'] == 'protected')
            self.assertEqual(protected['locked_qty'], 120)
            self.assertEqual(sum(r['current_new_qty'] for r in plan['allocations']), 200)

    def test_confirmed_split_never_becomes_800(self):
        ctx = fixture()
        deal = negotiate_simulated(600, '2026-10-31T07:00:00+08:00', '2026-11-03T08:00:00+08:00')
        self.assertEqual(sum(r['qty'] for r in deal['schedule']), 600)
        self.assertEqual(len(deal['rounds']), 2)
        self.assertEqual(deal['extra_cost'], 160)
        ctx['supplies'] = deal['schedule']
        for plan in compare(ctx)['plans']:
            self.assertEqual(plan['shortage_qty'], 200)
        sales_plan, prod_plan = compare(ctx)['plans']
        self.assertEqual(next(r for r in sales_plan['allocations'] if r['id'] == 'wo')['production_units'], 50)
        self.assertEqual(next(r for r in prod_plan['allocations'] if r['id'] == 'wo')['production_units'], 150)

    def test_on_time_normal_supply(self):
        ctx = fixture()
        ctx['supplies'] = [{'id': 'po', 'qty': 600, 'eta': '2026-10-31T07:00:00+08:00', 'confirmed': True}]
        self.assertTrue(all(p['fully_feasible'] for p in compare(ctx)['plans']))

    def test_after_production_start_is_not_in_time(self):
        ctx = fixture()
        ctx['supplies'][0]['eta'] = '2026-10-31T10:00:00+08:00'
        wo = next(r for r in allocate(ctx, 'production')['allocations'] if r['id'] == 'wo')
        self.assertEqual(wo['shortage_qty'], 100)

    def test_other_material_missing_does_not_hoard(self):
        ctx = fixture()
        ctx['demands'][2]['other_materials_ready'] = False
        plan = allocate(ctx, 'production')
        self.assertEqual(next(r for r in plan['allocations'] if r['id'] == 'wo')['allocated_qty'], 0)
        self.assertEqual(next(r for r in plan['allocations'] if r['id'] == 'sales')['allocated_qty'], 200)

    def test_below_minimum_batch(self):
        ctx = fixture()
        ctx['actual_qty'] = 170
        wo = next(r for r in allocate(ctx, 'production')['allocations'] if r['id'] == 'wo')
        self.assertEqual(wo['allocated_qty'], 0)
        self.assertFalse(wo['startable'])

    def test_unrelated_reserved_not_reusable(self):
        ctx = fixture()
        ctx['reservations'].append({'demand_id': 'outside-order', 'qty': 40})
        self.assertEqual(allocate(ctx, 'sales')['current_free_qty'], 160)

    def test_blocked_stock(self):
        ctx = fixture()
        ctx['blocked_qty'] = 40
        self.assertEqual(allocate(ctx, 'sales')['shortage_qty'], 440)

    def test_no_feasible_plan(self):
        ctx = fixture()
        ctx.update(actual_qty=0, reservations=[], supplies=[])
        self.assertEqual(allocate(ctx, 'sales')['shortage_qty'], 720)

    def test_invalid_input(self):
        ctx = fixture()
        ctx['demands'].append(ctx['demands'][0])
        with self.assertRaises(ValueError):
            compare(ctx)
        ctx = fixture()
        ctx['actual_qty'] = -1
        with self.assertRaises(ValueError):
            compare(ctx)

    def test_negotiation_limits_and_refusal(self):
        self.assertEqual(negotiate_simulated(600, '2026-10-31', '2026-11-03', 22)['status'], 'needs_human')
        self.assertEqual(negotiate_simulated(600, '2026-10-31', '2026-11-03', 23, 1)['status'], 'needs_human')
        self.assertEqual(negotiate_simulated(600, '2026-10-31', '2026-11-03', refusal=True)['status'], 'needs_human')

    def test_on_hand_key_customer_work_order_differs_from_earliest_production(self):
        ctx = {'scope': 'on_hand_only', 'as_of': '2026-11-01T09:00:00+08:00',
               'actual_qty': 620, 'blocked_qty': 0, 'supplies': [],
               'reservations': [{'demand_id': 'C', 'qty': 420}],
               'demands': [
                   {'id': 'A', 'kind': 'production', 'qty': 240, 'priority': '重点客户',
                    'need_by': '2026-11-02T13:00:00+08:00', 'min_batch_qty': 100},
                   {'id': 'B', 'kind': 'production', 'qty': 320, 'priority': '明日开工',
                    'need_by': '2026-11-02T09:00:00+08:00', 'min_batch_qty': 100},
                   {'id': 'C', 'kind': 'production', 'qty': 420,
                    'need_by': '2026-11-06T09:00:00+08:00', 'min_batch_qty': 100}]}
        sales, production = compare(ctx)['plans']
        by_id = lambda plan: {row['id']: row for row in plan['allocations']}
        self.assertEqual(by_id(sales)['A']['current_new_qty'], 200)
        self.assertEqual(by_id(sales)['B']['current_new_qty'], 0)
        self.assertEqual(by_id(production)['A']['current_new_qty'], 0)
        self.assertEqual(by_id(production)['B']['current_new_qty'], 200)
        for plan in (sales, production):
            self.assertEqual(plan['shortage_qty'], 360)
            self.assertEqual(plan['current_free_qty'], 200)
            self.assertEqual(by_id(plan)['C']['locked_qty'], 420)
            self.assertEqual(by_id(plan)['C']['current_new_qty'], 0)
        # Legacy scope retains its existing ordering, including customer priority.
        ctx.pop('scope')
        self.assertEqual(by_id(allocate(ctx, 'production'))['A']['current_new_qty'], 200)


if __name__ == '__main__':
    unittest.main()
