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
