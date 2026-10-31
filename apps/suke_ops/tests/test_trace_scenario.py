import unittest
from datetime import datetime

from suke_ops.trace_scenario import apply_update, evidence


class TraceScenarioTests(unittest.TestCase):
    def test_inventory_is_disjoint_and_sales_are_not_double_counted(self):
        snapshot = evidence()
        calc = snapshot['calculation']
        self.assertEqual(calc['on_hand'] - calc['reserved'] - calc['quality_hold'], 200)
        self.assertEqual(calc['gross_demand'], 240 + 320)
        self.assertEqual(calc['free_quantity_gap'], 360)
        self.assertEqual({row['sales_order'] for row in snapshot['requirements']}, {'SO-A', 'SO-B'})
        self.assertIn('不能重复计数', snapshot['evidence_precedence'])
        self.assertIn('其他物料', dict(snapshot['facts'])['E03 · 工单需求及销售关联'])

    def test_supplier_update_keeps_old_evidence_and_links_versions(self):
        before, after = evidence(), evidence(True)
        self.assertEqual(len(after['rich_sources']), len(before['rich_sources']) + 1)
        self.assertEqual(after['rich_sources'][:-1], before['rich_sources'])
        old = next(row for row in before['rich_sources'] if row['id'] == 'E05')
        new = after['rich_sources'][-1]
        self.assertEqual(new['supersedes'], old['id'])
        self.assertNotEqual(new['version'], old['version'])
        self.assertEqual(new['claims']['early_quantity'], 120)
        self.assertEqual(old['claims']['early_quantity'], 200)
        self.assertIn('今晚可能先出 200', old['text'])
        self.assertIn('今晚可以发这 120', new['text'])
        self.assertEqual(after['change']['affected_plan'], before['plan']['id'])
        self.assertEqual(after['change']['updated_plan'], after['plan']['id'])
        self.assertEqual(after['change']['delta'], -80)
        self.assertIn('WO-B', after['change']['affected_records'])
        self.assertIsNone(before['change'])
        self.assertFalse(before['updated'])
        self.assertTrue(after['updated'])
        self.assertEqual(new['supersedes_version'], old['version'])
        self.assertNotIn('E11', before['evidence_precedence'])
        self.assertNotIn('120', before['title'])

    def test_quantity_balance_does_not_convert_pending_supply_to_firm_supply(self):
        before, after = evidence(), evidence(True)
        self.assertEqual(before['calculation']['conditional_supply'], 560)
        self.assertEqual(before['calculation']['conditional_quantity_gap'], 0)
        self.assertEqual(after['calculation']['conditional_supply'], 480)
        self.assertEqual(after['calculation']['conditional_quantity_gap'], 80)
        for snapshot in (before, after):
            calc = snapshot['calculation']
            self.assertEqual(calc['early_confirmed_usable_quantity'], 0)
            self.assertEqual(calc['quality_hold_counted'], 0)
            self.assertEqual(calc['free_quantity_gap'], 360)
            self.assertFalse(snapshot['plan']['execution_authorized'])
            self.assertEqual(calc['actual_savings'], '未验证')
            self.assertIsNone(calc['incremental_cost'])
            self.assertIsNone(calc['purchase_order_unshipped_confirmed'])
            self.assertIn('不能承诺', snapshot['deterministic_conclusion'])

    def test_conditional_allocations_conserve_stock_and_leave_c_obligation(self):
        for updated in (False, True):
            snapshot = evidence(updated)
            calc, allocations = snapshot['calculation'], snapshot['plan']['allocations']
            self.assertEqual(sum(row['free_stock'] for row in allocations), 200)
            self.assertEqual(sum(row['proposed_release_from_c'] for row in allocations), 160)
            self.assertEqual(sum(row['early_batch'] for row in allocations), 120 if updated else 200)
            for row in allocations:
                self.assertEqual(row['free_stock'] + row['proposed_release_from_c'] + row['early_batch'], row['conditional_available'])
            self.assertEqual(calc['conditional_a_allocation'], 240)
            self.assertEqual(calc['conditional_b_available'], 240 if updated else 320)
            self.assertEqual(calc['conditional_b_gap'], 80 if updated else 0)
            self.assertEqual(calc['reservation_remaining_if_approved'], 260)
            self.assertEqual(calc['reservation_replenishment_required_if_approved'], 160)
            self.assertEqual(calc['purchase_order_remainder_if_candidate_dispatched'], 880 if updated else 800)

    def test_time_conditions_and_no_invented_arrival_or_quality_release(self):
        snapshot = evidence(True)
        a, b = snapshot['requirements']
        self.assertEqual(a['material_required_at'], '2026-11-03T09:00:00+08:00')
        self.assertEqual(b['material_required_at'], '2026-11-03T13:00:00+08:00')
        self.assertLess(datetime.fromisoformat(a['material_required_at']), datetime.fromisoformat(b['material_required_at']))
        self.assertEqual(snapshot['protected_reservation']['material_required_at'], '2026-11-06T08:00:00+08:00')
        claims = snapshot['rich_sources'][-1]['claims']
        self.assertIsNone(claims['arrival_at'])
        self.assertIsNone(claims['usable_at'])
        self.assertIsNone(claims['dispatched_quantity'])
        condition_ids = {row['id'] for row in snapshot['plan']['conditions']}
        self.assertEqual(condition_ids, {'C-RELEASE', 'C-REPLENISH', 'EARLY-ARRIVAL', 'EARLY-QUALITY'})
        quality = next(row for row in snapshot['rich_sources'] if row['id'] == 'E10')
        self.assertIsNone(quality['claims']['released_quantity'])
        self.assertEqual(quality['claims']['expected_result_at'], '2026-11-02T11:00:00+08:00')
        self.assertIn('不是放行承诺', quality['status'])

    def test_synthetic_attribution_and_original_text_are_not_real_employee_claims(self):
        snapshot = evidence(True)
        self.assertTrue(snapshot['synthetic'])
        self.assertIn('未接入真实', snapshot['disclosure'])
        for source in snapshot['rich_sources']:
            self.assertTrue(source['synthetic'])
            self.assertIsNone(source['reported_by'])
            self.assertIn('演示', source['actor'])
            self.assertIn('非系统接收时间', source['time_basis'])
            self.assertIn('未核验真实身份', source['identity_status'])
            self.assertEqual(source['reported_at'], source['at'])
            self.assertEqual(len(source['version']), 12)
        self.assertEqual(snapshot['facts'], [[row['label'], row['text']] for row in snapshot['rich_sources']])

    def test_snapshots_are_independent_and_update_flag_is_strict(self):
        snapshot = evidence(True)
        snapshot['rich_sources'][0]['text'] = 'tampered'
        snapshot['plan']['conditions'].clear()
        self.assertNotEqual(evidence(True)['rich_sources'][0]['text'], 'tampered')
        self.assertEqual(len(evidence(True)['plan']['conditions']), 4)
        for flag in (1, 0, 'true', None, []):
            with self.assertRaises(ValueError):
                evidence(flag)

    def test_apply_update_attributes_trigger_without_impersonating_supplier(self):
        before = evidence()
        after = apply_update(before, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')
        change, source = after['change'], after['rich_sources'][-1]
        self.assertTrue(after['updated'])
        self.assertEqual(after['previous_turn'], 'turn-001')
        self.assertEqual(change['recorded_by'], 'demo@example.invalid')
        self.assertEqual(change['recorded_at'], '2026-10-31 16:00:00')
        self.assertEqual(change['source_versions']['E05'], change['old_source_version'])
        self.assertEqual(change['source_versions']['E11'], source['version'])
        self.assertEqual(source['ingestion']['kind'], 'synthetic_event_trigger')
        self.assertEqual(source['ingestion']['recorded_by'], 'demo@example.invalid')
        self.assertIsNone(source['reported_by'])
        self.assertEqual(source['reported_at'], '2026-11-02T10:18:00+08:00')
        self.assertIn('不是供应商真实上传', source['ingestion']['note'])
        self.assertFalse(before['updated'])
        self.assertEqual(len(before['rich_sources']), 10)

    def test_apply_update_rejects_repeat_notes_unknown_event_and_changed_evidence(self):
        invalid = [
            (evidence(True), 'supplier_120'),
            ({**evidence(), 'human_sources': [{'id': 'H1'}]}, 'supplier_120'),
            ({**evidence(), 'scenario_key': 'other'}, 'supplier_120'),
            (evidence(), 'approve_release'),
        ]
        tampered = evidence()
        next(row for row in tampered['rich_sources'] if row['id'] == 'E05')['version'] = 'changed'
        invalid.append((tampered, 'supplier_120'))
        for snapshot, key in invalid:
            with self.assertRaises(ValueError):
                apply_update(snapshot, key, 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')
        for metadata in [('', 'user', 'time'), ('turn', '', 'time'), ('turn', 'user', None)]:
            with self.assertRaises(ValueError):
                apply_update(evidence(), 'supplier_120', *metadata)

    def test_apply_update_rejects_any_old_source_change_even_if_supplier_version_matches(self):
        for changed_field in ('text', 'version', 'status', 'actor', 'reported_at'):
            snapshot = evidence()
            original = next(row for row in snapshot['rich_sources'] if row['id'] == 'E05')['version']
            source = next(row for row in snapshot['rich_sources'] if row['id'] == 'E02')
            source[changed_field] = 'older inventory source value'
            self.assertEqual(next(row for row in snapshot['rich_sources'] if row['id'] == 'E05')['version'], original)
            with self.subTest(field=changed_field), self.assertRaisesRegex(ValueError, '业务基线已变化'):
                apply_update(snapshot, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')
        for alter in (lambda rows: rows.pop(0), lambda rows: rows.append(dict(rows[0]))):
            snapshot = evidence()
            alter(snapshot['rich_sources'])
            with self.assertRaises(ValueError):
                apply_update(snapshot, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')

    def test_apply_update_rejects_old_business_baseline_without_source_changes(self):
        alterations = {
            'inventory': lambda row: row.update(free=250),
            'requirements': lambda rows: rows[0].update(quantity=200),
            'purchase_order': lambda row: row.update(quantity=1200),
            'protected_reservation': lambda row: row.update(proposed_release=100),
            'calculation': lambda row: row.update(free_stock=250),
            'plan': lambda row: row['allocations'][0].update(conditional_available=200),
            'facts': lambda rows: rows[0].append('old fact'),
        }
        for key, alter in alterations.items():
            snapshot = evidence()
            alter(snapshot[key])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '业务基线已变化'):
                apply_update(snapshot, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')
        snapshot = evidence()
        del snapshot['requirements']
        with self.assertRaises(ValueError):
            apply_update(snapshot, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')

    def test_apply_update_loads_one_fixture_version_for_validation_and_creation(self):
        from pathlib import Path
        from unittest.mock import patch
        snapshot = evidence()
        original_read = Path.read_text
        reads = []

        def tracked_read(path, *args, **kwargs):
            reads.append(path.name)
            return original_read(path, *args, **kwargs)

        with patch.object(Path, 'read_text', tracked_read):
            result = apply_update(snapshot, 'supplier_120', 'turn-001', 'demo@example.invalid', '2026-10-31 16:00:00')
        self.assertEqual(reads, ['trace_scenario_data.json'])
        self.assertEqual(result['rich_sources'][:-1], snapshot['rich_sources'])


if __name__ == '__main__':
    unittest.main()
