"""模型输出属于不可信输入：验证工具白名单、边界和完整回答要求。"""
import unittest
import io
import os
from unittest.mock import patch

from suke_ops.agent_runtime import respond, schema, text_schema


class AgentRuntimeTests(unittest.TestCase):
    def test_gateway_receives_application_user_agent(self):
        from suke_ops.agent_runtime import request_model
        config = {'ANTHROPIC_BASE_URL': 'https://api.openai-next.com',
                  'ANTHROPIC_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_AUTH_TOKEN': 'test-token'}
        with patch.dict(os.environ, config), patch('suke_ops.agent_runtime.urlopen',
                return_value=io.BytesIO(b'{"content":[],"stop_reason":"end_turn"}')) as transport:
            request_model('连接测试', [{'role': 'user', 'content': 'test'}], [])
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai-next.com/v1/messages')
        self.assertEqual(request.get_header('User-agent'), 'caigou-copilot/1.0')

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

    def test_action_result_required_after_analysis(self):
        self.spec.append(schema('report_action', '说明行动结果', {
            'status': text_schema(['blocked', 'analysis_only', 'draft_ready']),
            'reason': text_schema(), 'next_step': text_schema()}, ['status', 'reason', 'next_step']))
        vague = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '负责人给出选择后再处理。'}]}
        with self.assertRaisesRegex(RuntimeError, '行动结果'):
            self.run_agent([self.output(), vague, vague, vague, vague])

    def test_action_result_is_not_business_evidence(self):
        self.spec.append(schema('report_action', '说明行动结果', {
            'status': text_schema(), 'reason': text_schema(), 'next_step': text_schema()},
            ['status', 'reason', 'next_step']))
        action = self.output('report_action', {'status': 'blocked', 'reason': '缺少来源单据', 'next_step': '请提供单号'})
        vague = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '暂未建单'}]}
        with self.assertRaises(RuntimeError):
            self.run_agent([action, vague, vague, vague, vague])

    def test_missing_action_is_repaired_before_success(self):
        self.spec.append(schema('report_action', '说明行动结果', {
            'status': text_schema(), 'reason': text_schema(), 'next_step': text_schema()},
            ['status', 'reason', 'next_step']))
        answer = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '暂未建单'}]}
        action = self.output('report_action', {'status': 'blocked', 'reason': '销售和生产争料',
                                             'next_step': '请负责人选择销售优先或生产优先'})
        self.assertEqual(self.run_agent([self.output(), answer, action, answer]), '暂未建单')
        self.assertEqual([call[1] for call in self.calls], ['read', 'report_action'])

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

    def test_material_rows_validate_before_any_write(self):
        line = {'type': 'object', 'properties': {'qty': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1000000000}},
                'required': ['qty'], 'additionalProperties': False}
        self.spec = [schema('create_material_request', '创建需求单', {
            'items': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': line}}, ['items'])]
        for items in [[], [{'qty': True}], [{'qty': float('nan')}], [{'qty': float('inf')}],
                      [{'qty': 0}], [{'qty': -1}], [{'qty': '2'}], [{'qty': 1, 'ignore_permissions': True}],
                      [{'qty': 1}, {}], [{'qty': 1}] * 21]:
            with self.subTest(items=items), self.assertRaises(RuntimeError):
                self.run_agent([self.output('create_material_request', {'items': items})])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_agent([self.output('create_material_request', {'items': [{'qty': 2.5}]}),
                         {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已建单'}]}]), '已建单')
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()

class PreloadedAndBatchTests(unittest.TestCase):
    def test_synthetic_prompt_only_requests_available_tools(self):
        for module in ('control', 'buying', 'stock'):
            def provider(system, messages, specs):
                self.assertNotIn('report_action', system)
                self.assertNotIn('create_material_request', system)
                self.assertNotIn('ask_module', system)
                return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '合成证据已核对'}]}
            respond(module, '核对合成场景', [], [schema('read_evidence', '查询证据', {})],
                    lambda *args: None, [], provider=provider, initial_evidence={'synthetic': True})

    def test_preloaded_facts_allow_one_model_request_without_fake_tool_use(self):
        trace, seen = [], []
        def provider(system, messages, specs):
            seen.extend(messages)
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Reviewed'}]}
        result = respond('stock', 'Review', [], [], lambda *a: self.fail('No repeated query'), trace,
                         provider=provider, initial_evidence={'read_at': 'now', 'stock': 220})
        self.assertEqual(result, 'Reviewed')
        self.assertIn('220', seen[-1]['content'])
        self.assertEqual(len([r for r in trace if r['tool'] == 'model_request']), 1)
        self.assertTrue(all(row['role'] == 'user' for row in seen[:2]))

    def test_batch_validated_deduplicated_and_budget_reserved_before_execution(self):
        spec = [schema('read', 'read', {'name': text_schema()}, ['name'])]
        calls = [{'type': 'tool_use', 'id': str(i), 'name': 'read', 'input': {'name': value}}
                 for i, value in enumerate(['a', 'b', 'a'])]
        budget, batches = [3], []
        def batch(module, requests):
            self.assertEqual(budget[0], 0)
            batches.append(requests)
            return [{'value': args['name']} for name, args in requests]
        def provider(system, messages, tools):
            if len(messages) == 1:
                return {'stop_reason': 'tool_use', 'content': calls}
            self.assertEqual([row['tool_use_id'] for row in messages[-1]['content']], ['0', '1', '2'])
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Done'}]}
        respond('buying', 'Read', [], spec, lambda *a: self.fail('No serial dispatch'), [],
                provider=provider, batch_dispatch=batch, tool_budget=budget)
        self.assertEqual(len(batches[0]), 2)

    def test_invalid_batch_never_reaches_parallel_dispatch(self):
        output = {'content': [{'type': 'tool_use', 'id': 'a', 'name': 'forbidden', 'input': {}}]}
        with self.assertRaises(RuntimeError):
            respond('buying', 'Read', [], [], lambda *a: None, [], provider=lambda *a: output,
                    batch_dispatch=lambda *a: self.fail('Unauthorized batch'))
