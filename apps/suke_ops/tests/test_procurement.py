"""采购闭环和经验规则通过公开接口验证，不依赖模型或数据库。"""
import unittest
from itertools import permutations
from suke_ops.procurement import match, validate_conditions, mine, replay, progress, savings


class ProcurementTests(unittest.TestCase):
    def test_conditions_are_typed_and_bounded(self):
        condition = [{'field': 'late_days', 'op': 'gte', 'value': 2}]
        self.assertTrue(match(condition, {'late_days': 3}))
        self.assertFalse(match(condition, {}))
        self.assertFalse(match(condition, {'late_days': '3'}))
        for bad in ([{'field': '__class__', 'op': 'eq', 'value': 'x'}],
                    [{'field': 'late_days', 'op': 'eval', 'value': 1}],
                    [{'field': 'late_days', 'op': 'gte', 'value': float('nan')}], []):
            with self.assertRaises(ValueError):
                validate_conditions(bad)

    def examples(self):
        return [{'name': str(i), 'process': str(i), 'stage': 'order', 'verified': True,
                 'synthetic': False, 'observed_at': f'2026-10-{i+1:02}T09:00:00',
                 'conditions': [{'field': 'late_days', 'op': 'gte', 'value': 1}],
                 'context': {'late_days': 2}, 'recommendation': '核对分批交付及备用供应商',
                 'outcome': 'positive'} for i in range(5)]

    def test_mining_deduplicates_processes_and_separates_holdout(self):
        rows = self.examples()
        rows += [dict(rows[0], name='duplicate')]
        candidate = mine(rows)[0]
        self.assertEqual(len(candidate['training_ids']), 3)
        self.assertEqual(len(candidate['holdout_ids']), 2)
        self.assertFalse(set(candidate['training_ids']) & set(candidate['holdout_ids']))
        self.assertEqual(candidate['support'], 5)
        self.assertEqual(candidate['report']['negative'], 0)

    def test_unknown_unverified_and_synthetic_are_not_real_evidence(self):
        for key, value in [('verified', False), ('synthetic', True), ('outcome', 'unknown')]:
            rows = [dict(r, **{key: value}) for r in self.examples()]
            self.assertEqual(mine(rows), [])

    def test_multiple_processes_sharing_same_order_are_one_sample(self):
        rows = [dict(r, case_id='same-purchase-order') for r in self.examples()]
        self.assertEqual(mine(rows), [])

    def test_condition_permutations_cannot_inflate_independent_cases(self):
        conditions=[{'field':'late_days','op':'gte','value':1},{'field':'quote_count','op':'gte','value':1},{'field':'supplier','op':'eq','value':'TEST'}]
        rows=[dict(row,name=f"{row['name']}-{n}",case_id=row['process'],conditions=list(order),context={'late_days':2,'quote_count':2,'supplier':'TEST'})
              for row in self.examples() for n,order in enumerate(permutations(conditions))]
        self.assertEqual(mine(rows[:12]),[])
        candidate=mine(rows)[0]
        self.assertEqual(candidate['support'],5)
        ids={row['name']:row['case_id'] for row in rows}
        self.assertFalse({ids[n] for n in candidate['training_ids']} & {ids[n] for n in candidate['holdout_ids']})

    def test_counterexamples_prevent_publication(self):
        rows = self.examples()
        rows[-1]['outcome'] = 'negative'
        self.assertFalse(mine(rows)[0]['report']['eligible'])
        report = replay(rows[0], rows, training_ids=['0', '1', '2'])
        self.assertEqual(report['negative'], 1)
        self.assertEqual(report['tested'], 2)

    def test_future_outcomes_and_duplicate_process_labels_are_excluded(self):
        rows = self.examples()
        self.assertEqual(mine(rows, as_of='2026-10-03T09:00:00'), [])
        rows += [dict(rows[0], name='contradiction', outcome='negative')]
        self.assertEqual(mine(rows), [])

    def test_replay_does_not_use_future_counterexample_or_unrelated_stage(self):
        rows=self.examples()
        future=dict(rows[0],name='future',outcome='negative',observed_at='2026-12-01T00:00:00')
        report=replay(rows[0],[*rows,future],as_of='2026-10-31T00:00:00')
        self.assertEqual(report['tested'],5)
        self.assertEqual(report['negative'],0)
        other=dict(rows[0],name='other-stage',stage='inquiry',outcome='negative')
        self.assertEqual(mine([*rows,other])[0]['support'],5)

    def test_split_receipts_and_returns_never_mark_complete_early(self):
        docs = [{'doctype': 'Material Request', 'docstatus': 1},
                {'doctype': 'Purchase Order', 'docstatus': 1},
                {'doctype': 'Purchase Receipt', 'docstatus': 1}]
        result = progress(docs, ordered={'item|件': 100}, received={'item|件': 80})
        self.assertEqual(next(r for r in result if r['id'] == 'receipt')['state'], 'partial')
        result = progress(docs, ordered={'item|件': 100}, received={'item|件': 100})
        self.assertEqual(next(r for r in result if r['id'] == 'receipt')['state'], 'done')
        self.assertNotEqual(next(r for r in result if r['id'] == 'finance')['state'], 'done')

    def test_financial_savings_requires_comparable_verified_baseline(self):
        self.assertIsNone(savings(1000, 800, comparable=False, verified=True))
        self.assertIsNone(savings(1000, 800, comparable=True, verified=False))
        self.assertEqual(savings(1000, 1100, comparable=True, verified=True), -100)


if __name__ == '__main__':
    unittest.main()
