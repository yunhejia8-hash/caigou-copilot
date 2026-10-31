"""模型输出属于不可信输入：验证工具白名单、边界和完整回答要求。"""
import unittest

from suke_ops.agent_runtime import respond, schema, text_schema


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.spec = [schema('read', '读单据', {'name': text_schema(['SAFE'])}, ['name'])]
        self.trace = []
        self.calls = []

    def output(self, name='read', args=None):
        return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': name,
                'input': {'name': 'SAFE'} if args is None else args, 'id': 'call-1'}]}

    def run_agent(self, outputs):
        iterator = iter(outputs)
        return respond('buying', '查订单', [], self.spec,
                       lambda *args: self.calls.append(args) or {'actual': 200}, self.trace,
                       provider=lambda *args: next(iterator))

    def test_real_tool_evidence_precedes_answer(self):
        answer = self.run_agent([self.output(), {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '现存200'}]}])
        self.assertEqual(answer, '现存200')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.trace[0]['agent'], 'buying')

    def test_unknown_tool_cannot_execute(self):
        with self.assertRaises(RuntimeError):
            self.run_agent([self.output('submit_purchase')])
        self.assertEqual(self.calls, [])

    def test_hidden_parameter_cannot_elevate_user(self):
        with self.assertRaises(RuntimeError):
            self.run_agent([self.output(args={'name': 'SAFE', 'ignore_permissions': 'true'})])
        self.assertEqual(self.calls, [])

    def test_wrong_type_or_enum_is_rejected(self):
        for value in [1, {}, 'OTHER']:
            with self.assertRaises(RuntimeError):
                self.run_agent([self.output(args={'name': value})])
        self.assertEqual(self.calls, [])

    def test_missing_parameter_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.run_agent([self.output(args={})])

    def test_no_fact_without_tool(self):
        with self.assertRaises(RuntimeError):
            self.run_agent([{'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已完成'}]}] * 5)

    def test_truncated_answer_is_not_success(self):
        with self.assertRaises(RuntimeError):
            self.run_agent([self.output(), {'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '部分答案'}]}])

    def test_shared_budget_bounds_nested_agents(self):
        with self.assertRaises(RuntimeError):
            respond('buying', 'test', [], self.spec, lambda *args: {}, [], budget=[0],
                    provider=lambda *args: self.fail('预算用尽时不得调用模型'))

    def test_seven_legal_calls_finish_with_matching_results(self):
        calls = [{'type': 'tool_use', 'id': str(i), 'name': 'read', 'input': {'name': str(i)}} for i in range(7)]
        executed = []
        def provider(system, messages, tools):
            if len(messages) == 1:
                return {'stop_reason': 'tool_use', 'content': calls}
            self.assertEqual([row['tool_use_id'] for row in messages[-1]['content']], [str(i) for i in range(7)])
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '完成'}]}
        answer = respond('buying', '摘要', [], [schema('read', '读取', {'name': text_schema()}, ['name'])],
                         lambda *args: executed.append(args) or {}, [], provider=provider)
        self.assertEqual(answer, '完成')
        self.assertEqual(len(executed), 7)

    def test_repeated_queries_reuse_result(self):
        self.run_agent([self.output(), self.output(), {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '完成'}]}])
        self.assertEqual(len(self.calls), 1)

    def test_oversized_batch_executes_nothing(self):
        calls = [{**self.output()['content'][0], 'id': str(i)} for i in range(25)]
        with self.assertRaises(RuntimeError):
            self.run_agent([{'stop_reason': 'tool_use', 'content': calls}])
        self.assertEqual(self.calls, [])

    def test_invalid_call_at_end_rejects_whole_batch(self):
        calls = [self.output()['content'][0], {**self.output('submit_purchase')['content'][0], 'id': 'call-2'}]
        with self.assertRaises(RuntimeError):
            self.run_agent([{'stop_reason': 'tool_use', 'content': calls}])
        self.assertEqual(self.calls, [])

    def test_shared_tool_budget_cannot_be_exceeded(self):
        with self.assertRaises(RuntimeError):
            respond('buying', '查订单', [], self.spec, lambda *args: self.fail('不得执行'), [],
                    tool_budget=[0], provider=lambda *args: self.output())


if __name__ == '__main__':
    unittest.main()
