import json
import threading
import unittest
from suke_ops.live_discussion import evidence, discuss


class DiscussionTests(unittest.TestCase):
    def test_preloads_count_towards_forty_read_limit(self):
        for count in (3, 4):
            def provider(system, messages, tools):
                if isinstance(messages[-1]['content'], str):
                    return {'content': [{'type': 'tool_use', 'id': str(i), 'name': 'read_evidence',
                             'input': {'evidence_id': 'E0' + str(i + 1)}} for i in range(count)]}
                return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已核对'}]}
            result = discuss(evidence(0, True), '核对', lambda rows: None, provider)
            reads = [row for row in result['events'] if row['kind'] == 'query']
            self.assertLessEqual(len(reads), 40)
            self.assertEqual(result['partial'], count == 4)

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
        buying = [event for event in result['events'] if event['module'] == 'buying' and event['phase'] == '部门初核']
        self.assertEqual([event['kind'] for event in buying], ['started', 'query', 'evidence', 'query', 'evidence', 'reply'])
        self.assertEqual(buying[1]['origin'], 'program_preload')
        self.assertEqual(buying[3]['origin'], 'model_tool')
        self.assertEqual(buying[4]['source_ids'], ['E02'])
        self.assertEqual(len(states[0]['discussion']), 0)
        self.assertTrue(all(e['at'] for e in result['events']))
        self.assertTrue(all(s['reported_by'] is None for s in result['sources']))
        self.assertIn('E02', result['discussion'][0]['source_ids'])
        self.assertIn('E02', buying[4]['versions'])

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

    def test_parallel_rounds_use_phase_barriers_and_one_model_call_per_turn(self):
        barriers = {0: threading.Barrier(4), 5: threading.Barrier(4)}
        calls, callback_threads, states, published = [], [], [], []
        lock = threading.Lock()
        caller = threading.get_ident()

        def provider(system, messages, tools):
            payload = json.loads(messages[-1]['content'].split('\n', 1)[1])
            previous = payload['discussion']
            with lock:
                calls.append((len(previous), threading.get_ident(), payload))
            if len(previous) in barriers:
                # A serial implementation cannot pass this barrier. No wall-clock
                # speed assertion or sleep is needed to prove overlap.
                barriers[len(previous)].wait(timeout=5)
            return {'content': [{'type': 'text', 'text': '已根据预读E01核查，待负责人确认。'}],
                    'stop_reason': 'end_turn'}

        def on_event(state):
            callback_threads.append(threading.get_ident())
            states.append(state)

        def publish(rows):
            callback_threads.append(threading.get_ident())
            published.append(rows)

        result = discuss(evidence(0), '测试', publish, provider, on_event=on_event)
        self.assertFalse(result['partial'])
        self.assertEqual(len(calls), 10)
        self.assertEqual(sum(item['tool'] == 'model_request' for item in result['trace']), 10)
        self.assertEqual(result['timing']['model_calls'], 10)
        self.assertEqual(result['timing']['department_concurrency'], 4)
        self.assertGreaterEqual(result['timing']['total_ms'], 0)
        self.assertTrue(all(row['duration_ms'] >= 0 for row in result['discussion']))
        self.assertEqual(sorted(count for count, _, _ in calls), [0, 0, 0, 0, 4, 5, 5, 5, 5, 9])
        for count in (0, 5):
            self.assertEqual(len({thread for size, thread, _ in calls if size == count}), 4)
        self.assertEqual(set(callback_threads), {caller})
        self.assertEqual([len(rows) for rows in published], list(range(1, 11)))
        self.assertEqual([event['id'] for event in result['events']], list(range(1, 41)))
        self.assertEqual(len(states), 40)
        for state in states:
            self.assertEqual([event['id'] for event in state['events']], list(range(1, len(state['events']) + 1)))
        for module in ('buying', 'selling', 'stock', 'manufacturing'):
            for phase in ('部门初核', '部门复核'):
                events = [event for event in result['events'] if event['module'] == module and event['phase'] == phase]
                self.assertEqual([event['kind'] for event in events], ['started', 'query', 'evidence', 'reply'])
                self.assertEqual(events[1]['origin'], 'program_preload')
                self.assertTrue(events[2]['versions'])
                self.assertGreaterEqual(events[3]['duration_ms'], 0)
        final_input = next(payload for size, _, payload in calls if size == 9)
        self.assertEqual(len(final_input['discussion']), 9)
        self.assertEqual({row['phase'] for row in final_input['discussion']}, {'部门初核', '总控追问', '部门复核'})

    def test_one_failed_department_does_not_cancel_peers_or_hide_failure(self):
        observed = []
        def provider(system, messages, tools):
            payload = json.loads(messages[-1]['content'].split('\n', 1)[1])
            if '你是库存 Agent' in system:
                raise RuntimeError('secret provider error')
            observed.append(payload)
            return {'content': [{'type': 'text', 'text': '本部门已核查；库存反馈缺失待补充。'}],
                    'stop_reason': 'end_turn'}
        result = discuss(evidence(0), '测试', lambda rows: None, provider)
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['discussion']), 10)
        failed = [row for row in result['discussion'] if row['status'] == 'failed']
        self.assertEqual([row['module'] for row in failed], ['stock', 'stock'])
        self.assertEqual(sum(row['status'] == 'done' for row in result['discussion']), 8)
        self.assertNotIn('secret provider error', str(result))
        final_input = next(payload for payload in observed if len(payload['discussion']) == 9)
        self.assertEqual(sum(row['status'] == 'failed' for row in final_input['discussion']), 2)
        self.assertIn('部分讨论未完成', result['answer'])

    def test_preloaded_sources_publish_before_slow_department_finishes(self):
        peer_finished = threading.Event()
        emitted = []
        def provider(system, messages, tools):
            payload = json.loads(messages[-1]['content'].split('\n', 1)[1])
            if not payload['discussion'] and '你是库存 Agent' in system:
                if not peer_finished.wait(timeout=5):
                    raise RuntimeError('callbacks were blocked by worker')
            return {'content': [{'type': 'text', 'text': '已核查。'}], 'stop_reason': 'end_turn'}
        def on_event(state):
            event = state['events'][-1]
            emitted.append(event)
            if event['phase'] == '部门初核' and event['kind'] == 'reply' and event['module'] != 'stock':
                peer_finished.set()
        result = discuss(evidence(0), '测试', lambda rows: None, provider, on_event=on_event)
        self.assertFalse(result['partial'])
        stock_reply = next(event['id'] for event in emitted if event['module'] == 'stock' and event['phase'] == '部门初核' and event['kind'] == 'reply')
        peer_reply = next(event['id'] for event in emitted if event['module'] != 'stock' and event['phase'] == '部门初核' and event['kind'] == 'reply')
        self.assertLess(peer_reply, stock_reply)

    def test_each_turn_keeps_an_independent_bounded_model_budget(self):
        calls = []
        lock = threading.Lock()
        def provider(system, messages, tools):
            with lock:
                calls.append(system)
            return {'content': [{'type': 'tool_use', 'id': 'repeat', 'name': 'read_evidence', 'input': {}}]}
        result = discuss(evidence(0), '测试', lambda rows: None, provider)
        self.assertTrue(result['partial'])
        self.assertEqual(len(calls), 30)
        self.assertEqual(len(result['discussion']), 10)
        self.assertTrue(all(row['status'] == 'failed' for row in result['discussion']))
        self.assertEqual(sum('你是库存 Agent' in system for system in calls), 6)

    def test_one_excessive_tool_batch_does_not_exhaust_peer_budgets(self):
        def provider(system, messages, tools):
            if '你是库存 Agent' in system:
                return {'content': [{'type': 'tool_use', 'id': str(index), 'name': 'read_evidence', 'input': {}}
                                    for index in range(5)]}
            return {'content': [{'type': 'text', 'text': '已根据预读核查。'}], 'stop_reason': 'end_turn'}
        result = discuss(evidence(0), '测试', lambda rows: None, provider)
        self.assertTrue(result['partial'])
        self.assertEqual(sum(row['status'] == 'done' for row in result['discussion']), 8)
        self.assertTrue(all(row['module'] == 'stock' for row in result['discussion'] if row['status'] == 'failed'))
        self.assertFalse(any(event.get('origin') == 'model_tool' for event in result['events']))
