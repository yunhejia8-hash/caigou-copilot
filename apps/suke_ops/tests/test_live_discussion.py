import unittest
from suke_ops.live_discussion import evidence, discuss


class DiscussionTests(unittest.TestCase):
    def test_evidence_is_server_selected(self):
        self.assertEqual(evidence(0)['calculation']['free_stock'], 220)
        self.assertIsNone(evidence(0)['calculation']['expedite_incremental_cost_if_approved'])
        self.assertEqual(evidence(0, True)['calculation']['expedite_incremental_cost_if_approved'], 480)
        for index, updated in [(-1, False), (6, False), (True, False), (1, True)]:
            with self.assertRaises(ValueError):
                evidence(index, updated)

    def test_real_orchestration_publishes_ten_turns_and_passes_feedback(self):
        published = []
        reads = []
        def provider(system, messages, tools):
            if isinstance(messages[-1]['content'], str):
                return {'content': [{'type': 'tool_use', 'id': 'read', 'name': 'read_evidence', 'input': {}}]}
            reads.append(messages[-1]['content'][0]['content'])
            return {'content': [{'type': 'text', 'text': '测试反馈，非真实模型'}], 'stop_reason': 'end_turn'}
        result = discuss(evidence(0), '测试', published.append, provider)
        self.assertEqual(len(published), 10)
        self.assertEqual([len(rows) for rows in published], list(range(1, 11)))
        self.assertIn('部门初核', reads[-1])
        self.assertIn('总控追问', reads[-1])
        self.assertFalse(result['partial'])
        self.assertEqual(result['offers'], [])

    def test_failure_is_explicit_and_never_a_preset_answer(self):
        def fail(*args):
            raise RuntimeError('secret provider error')
        result = discuss(evidence(0), '测试', lambda rows: None, fail)
        self.assertTrue(result['partial'])
        self.assertNotIn('secret', str(result))
        self.assertTrue(all(row['status'] == 'failed' for row in result['discussion']))

    def test_first_case_uses_program_conclusion_even_if_model_is_wrong(self):
        def provider(system, messages, tools):
            if isinstance(messages[-1]['content'], str):
                return {'content': [{'type': 'tool_use', 'id': 'read', 'name': 'read_evidence', 'input': {}}]}
            return {'content': [{'type': 'text', 'text': '09:00晚于10:00，没有隐性费用'}], 'stop_reason': 'end_turn'}
        result = discuss(evidence(0, True), '测试', lambda rows: None, provider)
        self.assertNotIn('09:00晚于10:00', result['answer'])
        self.assertIn('其他损失与隐性费用未核实', result['answer'])
        self.assertIn('09:00晚于10:00', result['discussion'][-1]['answer'])
        self.assertTrue(result['review_required'])

    def test_audit_records_actual_tool_read_before_reply_and_no_fake_person(self):
        states = []
        def provider(system, messages, tools):
            if isinstance(messages[-1]['content'], str):
                return {'content': [{'type': 'tool_use', 'id': 'read', 'name': 'read_evidence', 'input': {'evidence_id': 'E02'}}]}
            return {'content': [{'type': 'text', 'text': '已核对E02，仍需负责人确认。'}], 'stop_reason': 'end_turn'}
        result = discuss(evidence(0), '测试', lambda rows: None, provider, on_event=states.append)
        self.assertEqual([e['kind'] for e in result['events'][:4]], ['started', 'query', 'evidence', 'reply'])
        self.assertEqual(result['events'][2]['source_ids'], ['E02'])
        self.assertEqual(len(states[0]['discussion']), 0)
        self.assertTrue(all(e['at'] for e in result['events']))
        self.assertTrue(all(s['reported_by'] is None for s in result['sources']))
        self.assertEqual(result['discussion'][0]['source_ids'], ['E02'])
        self.assertIn('E02', result['events'][2]['versions'])

    def test_human_note_keeps_attribution_and_disables_old_program_plan(self):
        snapshot = evidence(0, True)
        snapshot['human_sources'] = [{'id': 'H1', 'label': '补充', 'text': '到货需重新确认',
                                     'reported_by': 'test@example.invalid', 'reported_at': '2026-10-31 13:00:00',
                                     'source_type': '登录用户补充', 'source_path': 'previous-test',
                                     'verification': '未核实', 'version': 'test-version'}]
        def fail(*args):
            raise RuntimeError('unavailable')
        result = discuss(snapshot, '测试', lambda rows: None, fail)
        self.assertEqual(result['sources'][-1]['reported_by'], 'test@example.invalid')
        self.assertNotIn('可在10:00用料截止前完整满足', result['answer'])
