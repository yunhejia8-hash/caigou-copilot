import json
import threading
import unittest
from suke_ops.live_discussion import evidence, discuss, source_records


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
        for index, updated in [(-1, False), (7, False), (True, False), (1, True)]:
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

    def test_connector_versions_keep_confirmed_and_conditional_gaps_distinct(self):
        for updated, conditional_gap, candidate_total in ((False, 0, 560), (True, 80, 480)):
            with self.subTest(updated=updated):
                snapshot = evidence(6, updated)
                wrong_model_answer = '确定可用缺口只有80，预留和提前批已落实。'
                def provider(system, messages, tools):
                    return {'content': [{'type': 'text', 'text': wrong_model_answer}], 'stop_reason': 'end_turn'}
                result = discuss(snapshot, '分析连接器缺料', lambda rows: None, provider)
                self.assertNotEqual(result['answer'], snapshot['deterministic_conclusion'])
                self.assertIn(wrong_model_answer, result['answer'])
                self.assertEqual(result['discussion'][-1]['answer'], wrong_model_answer)
                assessment = result['priority_assessment']
                self.assertEqual(assessment['totals']['confirmed_gap'], 360)
                self.assertEqual(assessment['totals']['conditional_gap'], conditional_gap)
                self.assertEqual(assessment['totals']['conditional_supply'], candidate_total)
                self.assertFalse(assessment['execution_authorized'])
                self.assertEqual(result['decision_check']['status'], 'unverified')
                calculation = result['verified_evidence']['calculation']
                self.assertEqual(calculation['free_quantity_gap'], 360)
                self.assertEqual(calculation['early_confirmed_usable_quantity'], 0)
                self.assertEqual(calculation['conditional_quantity_gap'], conditional_gap)
                self.assertEqual(calculation['conditional_supply'], candidate_total)
                self.assertEqual(calculation['conditional_b_gap'], conditional_gap)
                self.assertFalse(result['verified_evidence']['plan']['execution_authorized'])
                self.assertTrue(result['review_required'])
                if updated:
                    self.assertEqual(assessment['allocations'][1]['conditional_gap'], 80)
                    self.assertEqual(result['verified_evidence']['plan']['supersedes'], 'PLAN-1286-v1')
                else:
                    self.assertEqual(assessment['allocations'][1]['conditional_gap'], 0)

    def test_manual_b_first_reaches_every_phase_and_single_source_read(self):
        from suke_ops.priority_policy import make_policy
        snapshot = evidence(6, True)
        policy = make_policy(snapshot, ['WO-B', 'WO-A'], '本次先保B，确认A影响',
                             'buyer@example.invalid', '2026-11-02T10:30:00+08:00')
        payloads, states = [], []
        def provider(system, messages, tools):
            if isinstance(messages[-1]['content'], str):
                payloads.append(json.loads(messages[-1]['content'].split('\n', 1)[1]))
                return {'content': [{'type': 'tool_use', 'id': 'source', 'name': 'read_evidence',
                                     'input': {'evidence_id': 'E11'}}]}
            payloads.append(json.loads(messages[-1]['content'][0]['content']))
            return {'content': [{'type': 'text', 'text': '本轮先保B；A条件量缺80，提前批须09:00前可用。'}], 'stop_reason': 'end_turn'}
        result = discuss(snapshot, '本次先保B', lambda rows: None, provider,
                         on_event=states.append, priority_policy=policy)
        self.assertEqual(len(payloads), 20)
        for payload in payloads:
            self.assertEqual(payload['evidence']['priority_policy'], policy)
            allocations = payload['evidence']['priority_assessment']['allocations']
            self.assertEqual([row['work_order'] for row in allocations], ['WO-B', 'WO-A'])
            self.assertEqual([row['conditional_allocated'] for row in allocations], [320, 160])
            self.assertNotIn('plan', payload['evidence'])
            self.assertNotIn('deterministic_conclusion', payload['evidence'])
        self.assertTrue(all(state['priority_policy'] == policy for state in states))
        self.assertEqual([row['phase'] for row in result['discussion']],
                         ['部门初核'] * 4 + ['总控追问'] + ['部门复核'] * 4 + ['处置建议'])
        self.assertIn('本轮先保B', result['answer'])
        self.assertEqual(result['verified_evidence'], snapshot)

    def test_final_structured_fields_are_checked_without_extra_model_round(self):
        from suke_ops.priority_policy import make_policy
        snapshot = evidence(6, True)
        policy = make_policy(snapshot, ['WO-B', 'WO-A'], '先核对B的保供条件',
                             'buyer@example.invalid', '2026-11-02T10:30:00+08:00')
        calls = []
        def provider(system, messages, tools):
            payload = json.loads(messages[-1]['content'].split('\n', 1)[1])
            calls.append(payload)
            answer = '核查本部门信息，条件未落实。'
            if len(payload['discussion']) == 9:
                check = payload['evidence']['priority_assessment']
                answer = json.dumps({'policy_version': policy['version'], 'order': policy['order'],
                    'confirmed_gap': check['totals']['confirmed_gap'],
                    'conditional_gap': check['totals']['conditional_gap'],
                    'allocations': [{key: row[key] for key in ('work_order', 'required', 'confirmed_allocated',
                        'confirmed_gap', 'conditional_allocated', 'conditional_gap')} for row in check['allocations']],
                    'execution_authorized': False, 'summary': 'B先保，但仍待预留审批；A候选量缺80，提前批须09:00前可用。'}, ensure_ascii=False)
            return {'content': [{'type': 'text', 'text': answer}], 'stop_reason': 'end_turn'}
        result = discuss(snapshot, '先保B', lambda rows: None, provider, priority_policy=policy)
        self.assertEqual(len(calls), 10)
        self.assertEqual(result['decision_check']['status'], 'matched')
        self.assertIn('B先保', result['answer'])
        self.assertNotIn('policy_version', result['answer'])
        self.assertIn('policy_version', result['discussion'][-1]['raw_answer'])

    def test_rich_model_projection_removes_duplicate_text_but_preserves_audit_snapshot(self):
        for updated in (False, True):
            with self.subTest(updated=updated):
                snapshot = evidence(6, updated)
                original = json.loads(json.dumps(snapshot))
                payloads, states = [], []
                def provider(system, messages, tools):
                    payloads.append(json.loads(messages[-1]['content'].split('\n', 1)[1]))
                    return {'content': [{'type': 'text', 'text': '以已预读原始证据及条件核算为准。'}], 'stop_reason': 'end_turn'}
                result = discuss(snapshot, '测试去重', lambda rows: None, provider, on_event=states.append)
                initial = next(payload for payload in payloads if not payload['discussion'])
                self.assertEqual(snapshot, original)
                self.assertEqual(result['verified_evidence'], original)
                self.assertEqual(result['sources'], source_records(original))
                self.assertTrue(all(state['verified_evidence'] == original for state in states))
                self.assertNotIn('rich_sources', initial['evidence'])
                self.assertNotIn('human_sources', initial['evidence'])
                self.assertNotIn('facts', initial['evidence'])
                for key in ('as_of', 'snapshot_version', 'boundary'):
                    self.assertEqual(initial['evidence'][key], original[key])
                for key in ('plan', 'change', 'deterministic_conclusion'):
                    self.assertNotIn(key, initial['evidence'])
                self.assertEqual(initial['evidence']['priority_assessment'], result['priority_assessment'])
                model_sources = {row['id']: row for row in initial['sources']}
                for row in original['rich_sources']:
                    actual = model_sources[row['id']]
                    for key in ('text', 'actor', 'channel', 'status', 'at', 'version', 'verification',
                                'identity_status', 'time_basis', 'claims', 'supersedes', 'supersedes_version'):
                        if key in row:
                            self.assertEqual(actual[key], row[key])
                if updated:
                    self.assertEqual(model_sources['E11']['supersedes_version'], model_sources['E05']['version'])
                    self.assertEqual(model_sources['E05']['claims']['early_quantity'], 200)
                    self.assertEqual(model_sources['E11']['claims']['early_quantity'], 120)
                old_payload = {'evidence': original, 'sources': source_records(original), 'discussion': []}
                old_size = len(json.dumps(old_payload, ensure_ascii=False).encode())
                new_size = len(json.dumps(initial, ensure_ascii=False).encode())
                self.assertLess(new_size, old_size)  # Includes the new active policy and constraints.
                final_payload = next(payload for payload in payloads if len(payload['discussion']) == 9)
                for row in final_payload['discussion']:
                    self.assertIn('answer', row)
                    self.assertIn('source_ids', row)
                    self.assertIn('at', row)
                    self.assertNotIn('summary', row)
                    self.assertNotIn('duration_ms', row)
                self.assertTrue(all('summary' in row and 'duration_ms' in row for row in result['discussion']))

    def test_model_projection_preserves_unmatched_facts_and_human_attribution(self):
        snapshot = evidence(6, True)
        unmatched = ['独立约束', '此事实尚未关联来源卡片，仍须保留。']
        snapshot['facts'].append(unmatched)
        human = {'id': 'H1', 'label': '登录用户补充', 'text': '收到的新消息未核实。',
                 'reported_by': 'buyer@example.invalid', 'reported_at': '2026-11-02T10:30:00+08:00',
                 'source_type': '登录用户补充', 'version': 'human-version', 'verification': '未核实'}
        snapshot['human_sources'] = [human]
        payloads = []
        def provider(system, messages, tools):
            payloads.append(json.loads(messages[-1]['content'].split('\n', 1)[1]))
            return {'content': [{'type': 'text', 'text': '新信息待核实。'}], 'stop_reason': 'end_turn'}
        result = discuss(snapshot, '测试补充', lambda rows: None, provider)
        self.assertEqual(payloads[0]['evidence']['facts'], [unmatched])
        self.assertEqual(next(row for row in payloads[0]['sources'] if row['id'] == 'H1'), human)
        self.assertEqual(result['sources'][-1], human)
        self.assertNotEqual(result['answer'], snapshot['deterministic_conclusion'])

    def test_single_source_lookup_keeps_original_words_version_and_trace(self):
        snapshot = evidence(6, True)
        looked_up = []
        def provider(system, messages, tools):
            if isinstance(messages[-1]['content'], str):
                return {'content': [{'type': 'tool_use', 'id': 'source', 'name': 'read_evidence',
                                     'input': {'evidence_id': 'E11'}}]}
            looked_up.append(json.loads(messages[-1]['content'][0]['content']))
            return {'content': [{'type': 'text', 'text': 'E11更改的是候选数量，确定可用量仍缺360。'}], 'stop_reason': 'end_turn'}
        result = discuss(snapshot, '复查供应商原话', lambda rows: None, provider)
        original = next(row for row in snapshot['rich_sources'] if row['id'] == 'E11')
        self.assertEqual(len(looked_up), 10)
        for payload in looked_up:
            self.assertEqual(len(payload['sources']), 1)
            source = payload['sources'][0]
            for key in ('id', 'text', 'status', 'at', 'actor', 'version', 'supersedes_version', 'claims'):
                self.assertEqual(source[key], original[key])
            self.assertNotIn('facts', payload['evidence'])
            self.assertEqual(payload['evidence']['snapshot_version'], 'v2')
        actual_reads = [event for event in result['events']
                        if event['kind'] == 'evidence' and event.get('origin') == 'model_tool']
        self.assertEqual(len(actual_reads), 10)
        self.assertTrue(all(event['source_ids'] == ['E11'] for event in actual_reads))
        self.assertTrue(all(event['versions'] == {'E11': original['version']} for event in actual_reads))
