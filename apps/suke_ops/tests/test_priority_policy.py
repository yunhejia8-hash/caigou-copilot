from copy import deepcopy
import json
import unittest

from suke_ops.priority_policy import assess, final_decision, make_policy, model_evidence
from suke_ops.trace_scenario import apply_update, evidence


class PriorityPolicyTests(unittest.TestCase):
    def manual(self, snapshot, order=('WO-B', 'WO-A')):
        return make_policy(snapshot, list(order), '客户 B 停线影响较大，本轮先测算保 B。',
                           'buyer@example.invalid', '2026-11-02 10:20:00')

    def test_default_order_uses_actual_due_time_and_stable_id_tie_break(self):
        snapshot = evidence()
        self.assertEqual(make_policy(snapshot)['mode'], 'due_time')
        self.assertEqual(make_policy(snapshot)['order'], ['WO-A', 'WO-B'])
        snapshot['requirements'][1]['material_required_at'] = '2026-11-03T08:30:00+07:00'
        self.assertEqual(make_policy(snapshot)['order'], ['WO-A', 'WO-B'])
        snapshot['requirements'][1]['material_required_at'] = snapshot['requirements'][0]['material_required_at']
        snapshot['requirements'].reverse()
        self.assertEqual(make_policy(snapshot)['order'], ['WO-A', 'WO-B'])

    def test_manual_order_requires_exact_complete_set(self):
        snapshot = evidence()
        for order in ('WO-B', [], ['WO-A'], ['WO-A', 'WO-A'], ['WO-A', 'WO-X'],
                      ['WO-A', 'WO-B', 'WO-C'], ['WO-A', 1], ['WO-A', ['WO-B']]):
            with self.subTest(order=order), self.assertRaises(ValueError):
                make_policy(snapshot, order, '测试理由', 'buyer', '2026-11-02T10:20:00+08:00')

    def test_manual_order_requires_reason_and_server_attribution(self):
        snapshot = evidence()
        valid = {'reason': '客户影响确认后优先测算B', 'recorded_by': 'buyer',
                 'recorded_at': '2026-11-02T10:20:00+08:00'}
        for field, invalids in {'reason': (None, '', ' ', '长' * 301, 1),
                               'recorded_by': (None, '', ' ', 1),
                               'recorded_at': (None, '', '昨天', '2026-11-02', '2026-19-02T10:20:00')}.items():
            for invalid in invalids:
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    make_policy(snapshot, ['WO-B', 'WO-A'], **{**valid, field: invalid})
        policy = make_policy(snapshot, ['WO-B', 'WO-A'], **valid)
        self.assertEqual(policy['recorded_by'], 'buyer')
        self.assertEqual(policy['recorded_at'], valid['recorded_at'])

    def test_invalid_scenario_or_requirement_cannot_be_ranked(self):
        for snapshot in (None, {}, {'scenario_key': 'another'},
                         {'scenario_key': 'connector-1286', 'requirements': []}):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                make_policy(snapshot)
        for field, invalid in (('quantity', True), ('quantity', -1), ('quantity', 2.5),
                               ('material_required_at', 'tomorrow'),
                               ('material_required_at', '2026-11-03T09:00:00'),
                               ('split_allowed', None)):
            snapshot = evidence()
            snapshot['requirements'][0][field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                make_policy(snapshot)
        snapshot = evidence()
        snapshot['requirements'].append(deepcopy(snapshot['requirements'][0]))
        with self.assertRaises(ValueError):
            make_policy(snapshot)

    def test_policy_version_detects_changes_and_rejects_tampering(self):
        snapshot = evidence()
        policy = self.manual(snapshot)
        self.assertEqual(policy, self.manual(snapshot))
        self.assertNotEqual(policy['version'], self.manual(snapshot, ('WO-A', 'WO-B'))['version'])
        for key, value in (('order', ['WO-A', 'WO-B']), ('reason', '不同理由'),
                           ('mode', 'unknown'), ('version', 'forged')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                assess(snapshot, {**policy, key: value})
        default = make_policy(snapshot)
        with self.assertRaises(ValueError):
            assess(snapshot, {**default, 'order': ['WO-B', 'WO-A']})

    def test_both_versions_and_rankings_conserve_each_supply_pool(self):
        expected = {
            (False, 'WO-A'): {'WO-A': 240, 'WO-B': 320},
            (False, 'WO-B'): {'WO-A': 240, 'WO-B': 320},
            (True, 'WO-A'): {'WO-A': 240, 'WO-B': 240},
            (True, 'WO-B'): {'WO-A': 160, 'WO-B': 320},
        }
        for updated in (False, True):
            snapshot = evidence(updated)
            for order in (('WO-A', 'WO-B'), ('WO-B', 'WO-A')):
                with self.subTest(updated=updated, order=order):
                    result = assess(snapshot, self.manual(snapshot, order))
                    allocations, totals = result['allocations'], result['totals']
                    self.assertEqual(result['status'], 'assessed')
                    self.assertEqual([row['work_order'] for row in allocations], list(order))
                    self.assertEqual([row['priority'] for row in allocations], [1, 2])
                    self.assertEqual({row['work_order']: row['conditional_allocated'] for row in allocations},
                                     expected[(updated, order[0])])
                    self.assertEqual(totals['confirmed_supply'], 200)
                    self.assertEqual(totals['demand'], 560)
                    self.assertEqual(totals['confirmed_gap'], 360)
                    self.assertEqual(totals['conditional_supply'], 480 if updated else 560)
                    self.assertEqual(totals['conditional_gap'], 80 if updated else 0)
                    self.assertEqual(sum(row['confirmed_allocated'] for row in allocations), 200)
                    self.assertEqual(sum(row['confirmed_gap'] for row in allocations), totals['confirmed_gap'])
                    self.assertEqual(sum(row['conditional_allocated'] for row in allocations), totals['conditional_supply'])
                    self.assertEqual(sum(row['conditional_gap'] for row in allocations), totals['conditional_gap'])
                    for row in allocations:
                        self.assertEqual(row['confirmed_allocated'] + row['confirmed_gap'], row['required'])
                        self.assertEqual(row['conditional_allocated'] + row['conditional_gap'], row['required'])
                    self.assertEqual(allocations[0]['confirmed_gap'], 40 if order[0] == 'WO-A' else 120)
                    self.assertFalse(result['execution_authorized'])

    def test_manual_b_priority_moves_early_batch_deadline_to_a_at_nine(self):
        snapshot = evidence(True)
        a_first = assess(snapshot, self.manual(snapshot, ('WO-A', 'WO-B')))
        b_first = assess(snapshot, self.manual(snapshot))
        self.assertTrue(any('WO-B' in row and '提前批' in row and '13:00:00' in row for row in a_first['conditions']))
        a_early = [row for row in b_first['conditions'] if 'WO-A' in row and '提前批' in row]
        self.assertEqual(len(a_early), 1)
        self.assertIn('09:00:00', a_early[0])
        self.assertNotIn('13:00:00', a_early[0])
        self.assertIn('均未确认', a_early[0])
        self.assertIn('不等于按时可用', a_early[0])
        self.assertTrue(any('WO-A' in row and '不能整批开工' in row for row in b_first['conditions']))
        self.assertTrue(any('补回' in row and '2026-11-06T08:00:00+08:00' in row for row in b_first['conditions']))
        self.assertIn('不代表可开工或已执行', a_first['summary'])

    def test_manual_note_pauses_allocation_without_adopting_its_claim(self):
        snapshot = evidence(True)
        snapshot['human_sources'] = [{'id': 'H1', 'text': '供应商说全部已经到了。',
                                      'reported_by': 'buyer', 'reported_at': '2026-11-02T10:20:00+08:00'}]
        policy = self.manual(snapshot)
        original = deepcopy(snapshot)
        result = assess(snapshot, policy)
        self.assertEqual(result['status'], 'needs_verification')
        self.assertEqual(result['allocations'], [])
        self.assertTrue(all(value is None for value in result['totals'].values()))
        self.assertIn('历史需求待核验', result['basis'])
        self.assertEqual([row['id'] for row in result['ranked_orders']], ['WO-B', 'WO-A'])
        self.assertIn('不擅自认定', result['tradeoff'])
        self.assertFalse(result['execution_authorized'])
        projection = model_evidence(snapshot, policy, result)
        self.assertEqual(set(projection['calculation']), {'status'})
        self.assertEqual(projection['human_sources'], snapshot['human_sources'])
        self.assertEqual(snapshot, original)

    def test_model_projection_isolates_policy_and_keeps_canonical_snapshot_unchanged(self):
        snapshot = evidence(True)
        original = deepcopy(snapshot)
        policy = self.manual(snapshot)
        assessment = assess(snapshot, policy)
        projection = model_evidence(snapshot, policy, assessment)
        for key in ('plan', 'change', 'deterministic_conclusion', 'verified_plan'):
            self.assertNotIn(key, projection)
        for key in ('conditional_a_allocation', 'conditional_b_available', 'conditional_b_gap'):
            self.assertNotIn(key, projection['calculation'])
        for key in ('facts', 'rich_sources', 'requirements', 'inventory', 'protected_reservation'):
            self.assertEqual(projection[key], snapshot[key])
        self.assertEqual(projection['priority_policy'], policy)
        self.assertEqual(projection['priority_assessment'], assessment)
        self.assertNotIn('rulepriority', projection)
        projection['priority_policy']['order'].reverse()
        projection['rich_sources'][0]['text'] = 'mutated'
        projection['priority_assessment']['allocations'][0]['conditional_allocated'] = 999
        self.assertEqual(snapshot, original)
        self.assertEqual(policy['order'], ['WO-B', 'WO-A'])
        self.assertEqual(assessment['allocations'][0]['conditional_allocated'], 320)
        with self.assertRaises(ValueError):
            model_evidence(snapshot, policy, assess(snapshot, make_policy(snapshot)))

    def test_policy_assessment_does_not_invalidate_canonical_supplier_update(self):
        snapshot = evidence()
        policy = self.manual(snapshot)
        model_evidence(snapshot, policy, assess(snapshot, policy))
        updated = apply_update(snapshot, 'supplier_120', 'prior-test', 'buyer', '2026-11-02T10:20:00+08:00')
        result = assess(updated, policy)
        self.assertEqual(result['allocations'][0]['work_order'], 'WO-B')
        self.assertEqual(result['allocations'][1]['conditional_gap'], 80)

    def test_rules_expose_enterprise_choice_and_case_constraints_with_sources(self):
        result = assess(evidence(), make_policy(evidence()))
        rules = result['rules']
        self.assertEqual(len(rules), 6)
        self.assertEqual(len({row['id'] for row in rules}), 6)
        self.assertEqual(rules[0]['kind'], '企业策略')
        self.assertIn('不是行业统一规则', rules[0]['description'])
        self.assertEqual(rules[0]['source_url'], '')  # Enterprise choice has no invented external authority.
        for row in rules[1:]:
            self.assertTrue(all(row.get(key) for key in ('id', 'label', 'kind', 'description', 'source_title', 'source_url')))
            self.assertTrue(row['source_url'].startswith('https://'))
        rules[0]['description'] = 'changed by caller'
        self.assertNotEqual(assess(evidence(), make_policy(evidence()))['rules'][0]['description'], 'changed by caller')

    def test_invalid_or_ambiguous_supply_cannot_be_silently_calculated(self):
        for mutate in (
            lambda snapshot: snapshot['inventory'].update(buckets_disjoint=False),
            lambda snapshot: snapshot['inventory'].update(on_hand=1),
            lambda snapshot: snapshot['protected_reservation'].update(proposed_release=1000),
            lambda snapshot: snapshot.update(rich_sources=[]),
        ):
            snapshot = evidence()
            mutate(snapshot)
            with self.assertRaises(ValueError):
                assess(snapshot, make_policy(snapshot))

    def decision_input(self, human=False):
        snapshot = evidence(True)
        if human:
            snapshot['human_sources'] = [{'id': 'H1', 'text': '新消息待核实。'}]
        policy = self.manual(snapshot)
        assessment = assess(snapshot, policy)
        fields = ('work_order', 'required', 'confirmed_allocated', 'confirmed_gap',
                  'conditional_allocated', 'conditional_gap')
        decision = {'policy_version': policy['version'], 'order': policy['order'],
                    'confirmed_gap': assessment['totals']['confirmed_gap'],
                    'conditional_gap': assessment['totals']['conditional_gap'],
                    'allocations': [{key: row[key] for key in fields} for row in assessment['allocations']],
                    'execution_authorized': False, 'summary': '按所选顺序复核；审批及到货条件仍待确认。'}
        return policy, assessment, decision

    def test_final_decision_matches_fields_but_never_certifies_summary_semantics(self):
        policy, assessment, decision = self.decision_input()
        # A field match must not turn arbitrary model prose into a verified fact.
        decision['summary'] = '模型错误说全部已经开工。'
        raw = json.dumps(decision, ensure_ascii=False)
        result = final_decision(raw, policy, assessment)
        self.assertEqual(result['raw_answer'], raw)
        self.assertEqual(result['answer'], decision['summary'])
        self.assertEqual(result['decision_check']['status'], 'matched')
        self.assertEqual(result['decision_check']['issues'], [])
        self.assertFalse(result['decision_check']['summary_verified'])
        self.assertIn('摘要语义未验证', result['decision_check']['reason'])
        self.assertEqual(result['decision_check']['summary'], result['decision_check']['reason'])
        self.assertIn('不代表审批或可开工', result['decision_check']['scope'])

    def test_final_decision_checks_optional_priority_and_sales_order_instead_of_rejecting_them(self):
        policy, assessment, decision = self.decision_input()
        for actual, expected in zip(decision['allocations'], assessment['allocations']):
            actual.update(priority=expected['priority'], sales_order=expected['sales_order'])
        self.assertEqual(final_decision(json.dumps(decision), policy, assessment)['decision_check']['status'], 'matched')
        decision['allocations'][0]['priority'] = 2
        decision['allocations'][0]['sales_order'] = 'SO-A'
        result = final_decision(json.dumps(decision), policy, assessment)
        self.assertEqual(result['decision_check']['status'], 'mismatch')
        self.assertEqual(len(result['decision_check']['issues']), 2)

    def test_final_decision_flags_priority_quantities_version_and_execution_conflicts(self):
        policy, assessment, decision = self.decision_input()
        decision.update(order=['WO-A', 'WO-B'], policy_version='old-version', confirmed_gap=80,
                        conditional_gap=0, execution_authorized=True)
        decision['allocations'][1]['conditional_allocated'] = 240
        decision['allocations'][1]['conditional_gap'] = 0
        result = final_decision(json.dumps(decision), policy, assessment)
        self.assertEqual(result['answer'], decision['summary'])
        self.assertEqual(result['decision_check']['status'], 'mismatch')
        self.assertEqual(result['decision_check']['summary'], result['decision_check']['reason'])
        issues = '\n'.join(result['decision_check']['issues'])
        for label in ('策略版本', '订单顺序', '确定可用缺口', '条件缺口', '执行授权',
                      'WO-A.conditional_allocated', 'WO-A.conditional_gap'):
            self.assertIn(label, issues)

    def test_final_decision_requires_exact_integer_fields_and_complete_rows(self):
        for mutate in (
            lambda value: value.update(confirmed_gap=360.0),
            lambda value: value.update(execution_authorized=0),
            lambda value: value.update(approved=True),
            lambda value: value.pop('conditional_gap'),
            lambda value: value['allocations'][0].update(required=True),
            lambda value: value['allocations'][0].update(extra='not_checked'),
            lambda value: value['allocations'].pop(),
            lambda value: value['allocations'].append(deepcopy(value['allocations'][0])),
            lambda value: value['allocations'][0].update(work_order='WO-X'),
        ):
            policy, assessment, decision = self.decision_input()
            mutate(decision)
            with self.subTest(decision=decision):
                result = final_decision(json.dumps(decision), policy, assessment)
                self.assertEqual(result['decision_check']['status'], 'mismatch')
                self.assertTrue(result['decision_check']['issues'])

    def test_final_decision_unparseable_or_ambiguous_json_remains_unverified(self):
        policy, assessment, decision = self.decision_input()
        for raw in ('自然语言原话，尚无可比较字段。', '[]', '{broken',
                    '{"summary":"first","summary":"second"}',
                    json.dumps({**decision, 'summary': ['unexpected array']}),
                    json.dumps({**decision, 'summary': ' '}),
                    json.dumps(decision).replace('360', 'NaN', 1)):
            with self.subTest(raw=raw):
                result = final_decision(raw, policy, assessment)
                self.assertEqual(result['raw_answer'], raw)
                self.assertEqual(result['answer'], raw)
                self.assertEqual(result['decision_check']['status'], 'unverified')
                self.assertEqual(result['decision_check']['summary'], result['decision_check']['reason'])
                self.assertFalse(result['decision_check']['summary_verified'])

    def test_final_decision_accepts_json_fence_and_keeps_original_response(self):
        policy, assessment, decision = self.decision_input()
        raw = '```json\n' + json.dumps(decision, ensure_ascii=False) + '\n```'
        result = final_decision(raw, policy, assessment)
        self.assertEqual(result['decision_check']['status'], 'matched')
        self.assertEqual(result['raw_answer'], raw)
        self.assertEqual(result['answer'], decision['summary'])

    def test_final_decision_human_notes_require_null_totals_and_no_old_allocations(self):
        policy, assessment, decision = self.decision_input(human=True)
        result = final_decision(json.dumps(decision), policy, assessment)
        self.assertEqual(result['decision_check']['status'], 'matched')
        self.assertIn('尚无有效数量结论', result['decision_check']['reason'])
        _, _, old_decision = self.decision_input()
        decision['allocations'] = old_decision['allocations']
        decision['confirmed_gap'] = 360
        decision['conditional_gap'] = 80
        result = final_decision(json.dumps(decision), policy, assessment)
        self.assertEqual(result['decision_check']['status'], 'mismatch')
        self.assertTrue(any('null' in issue for issue in result['decision_check']['issues']))

    def test_final_decision_does_not_mutate_server_policy_or_assessment(self):
        policy, assessment, decision = self.decision_input()
        original_policy, original_assessment = deepcopy(policy), deepcopy(assessment)
        final_decision(json.dumps(decision), policy, assessment)
        self.assertEqual(policy, original_policy)
        self.assertEqual(assessment, original_assessment)


if __name__ == '__main__':
    unittest.main()
