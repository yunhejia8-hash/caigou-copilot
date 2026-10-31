"""Read-only, bounded real-model discussion over explicitly synthetic evidence."""
import json
import time
import hashlib
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from pathlib import Path
from suke_ops import agent_runtime as runtime


def evidence(index, updated=False):
    if type(index) is not int or not 0 <= index < 7 or type(updated) is not bool:
        raise ValueError('场景参数无效。')
    if index == 6:
        from suke_ops.trace_scenario import evidence as trace_evidence
        return trace_evidence(updated)
    rows = json.loads(Path(__file__).with_name('scenario_facts.json').read_text())
    row = rows[index]
    if updated and index != 0:
        raise ValueError('此场景没有补充证据。')
    return {'synthetic': True, 'title': row['title'], 'facts': row['facts'] + (row['event_facts'] if updated else []),
            'evidence_precedence': 'E04更新E01的提前批状态，E05更新约束；新证据优先。部门发言不能覆盖原始证据。',
            'verified_plan': ({
                'allocation': '220自由库存+150备用=370；销售分配300，余70；11月3日14:00加入原厂200后有270，改期至11月4日生产用240，余30。',
                'constraints': '备用150件11月3日09:00可用，采购待审批。原厂200件14:00可用已确认，原单剩400。14:00不能满足销售10:00或生产08:00的原截止。原预留及待检不使用，禁止拆批，生产改期需批准。',
                'no_expedite': '原截止两项均不能完整满足；14:00自由量420，比540少120。0加急费用不等于最低总损失。'
            } if index == 0 and updated else {}),
            'calculation': ({'free_stock': 220, 'gross_demand': 540, 'free_quantity_gap': 320,
                             'original_deadline_sales_if_expedite_approved': 300 if updated else 0,
                             'original_deadline_production': 0,
                             'expedite_incremental_cost_if_approved': 480 if updated else None,
                             'actual_savings': '未验证'} if index == 0 else {}),
            'boundary': '不是ERP实单；没有执行工具。费用不是节省，未确认条件不能作为承诺。'}


def source_records(snapshot):
    if snapshot.get('rich_sources'):
        return snapshot['rich_sources'] + snapshot.get('human_sources', [])
    records = []
    for label, value in snapshot['facts']:
        records.append({'id': label.split(' · ')[0], 'label': label, 'text': value,
                        'source_type': '合成场景文件', 'source_path': 'scenario_facts.json',
                        'reported_by': None, 'reported_at': None,
                        'verification': '演示设定，非真实员工提供',
                        'version': hashlib.sha256((label + value).encode()).hexdigest()[:12]})
    return records + snapshot.get('human_sources', [])


def short_reply(text, limit=140):
    plain = re.sub(r'[#*`>|]', '', text)
    plain = re.sub(r'\s+', ' ', plain).strip()
    return plain if len(plain) <= limit else plain[:limit] + '…'



def _model_payload(snapshot, selected_sources, discussion, single_source=False):
    """Keep one copy of source text in model context, without changing audit data.

    Rich cases store the same source three ways for UI/backward compatibility:
    facts, rich_sources and the tool's sources. Only the model projection is
    compacted. Unmatched facts and all business constraints remain available.
    """
    if single_source:
        relevant_keys = ('scenario_key', 'synthetic', 'snapshot_version', 'as_of',
                         'evidence_precedence', 'boundary')
        model_evidence = {key: snapshot[key] for key in relevant_keys if key in snapshot}
    else:
        model_evidence = {key: value for key, value in snapshot.items()
                          if key not in ('facts', 'rich_sources', 'human_sources')}
        covered = {(row.get('label'), row.get('text')) for row in selected_sources}
        unmatched = [fact for fact in snapshot.get('facts', []) if tuple(fact) not in covered]
        if unmatched:
            model_evidence['facts'] = unmatched

    model_sources = []
    for source in selected_sources:
        row = dict(source)
        # The rich source's original channel/time already contains these values.
        # Preserve different timestamps (e.g. actual receipt/ingestion time).
        if 'channel' in row and row.get('source_type') == row['channel']:
            row.pop('source_type', None)
        if 'at' in row and row.get('reported_at') == row['at']:
            row.pop('reported_at', None)
        model_sources.append(row)
    # Full replies are retained; summary and duration are derived UI fields.
    model_discussion = [{key: value for key, value in row.items()
                         if key not in ('summary', 'duration_ms')} for row in discussion]
    return {'evidence': model_evidence, 'sources': model_sources, 'discussion': model_discussion}


def discuss(snapshot, question, publish, provider=None, on_event=None):
    """Parallel department rounds, with coordinator barriers; at most 30 model calls.

    Workers only read the immutable evidence/previous-phase snapshot and call the
    model. All callbacks (including Frappe persistence) run on the calling thread.
    """
    started_at = time.monotonic()
    deadline = started_at + 780
    transcript, trace, events = [], [], []
    sources = source_records(snapshot)

    def emit(module, phase, kind, summary, at=None, **extra):
        events.append({'id': len(events) + 1, 'module': module, 'kind': kind,
                       'phase': phase, 'at': at or datetime.now(timezone.utc).isoformat(),
                       'summary': summary, **extra})
        if on_event:
            on_event({'events': list(events), 'discussion': list(transcript),
                      'sources': sources, 'verified_evidence': snapshot, 'synthetic': True})

    specs = [runtime.schema('read_evidence', '查询场景证据；可指定证据编号读取原文与提供人。未指定则返回全量证据和部门反馈。',
                            {'evidence_id': runtime.text_schema([row['id'] for row in sources])})]

    def run_phase(executor, modules, phase, task):
        # Every peer sees the same completed prior phases, never a partial peer
        # answer determined by network timing. The next phase waits for all peers.
        prior_feedback = list(transcript)
        updates = Queue()

        def worker(module):
            turn_started_at = time.monotonic()
            local_trace, read_ids = [], set()
            # Each of the ten turns owns a separate allowance: <=30 calls / 40
            # tool requests overall, without races or one department starving peers.
            model_budget, tool_budget = [3], [4]

            def queue_event(kind, summary, **extra):
                updates.put(('event', {'module': module, 'phase': phase, 'kind': kind,
                                      'summary': summary,
                                      'at': datetime.now(timezone.utc).isoformat(), **extra}))

            def read_evidence(args, preload=False):
                tool = 'read_evidence'
                if set(args) - {'evidence_id'}:
                    raise RuntimeError('不支持此工具。')
                selected = [row for row in sources if not args.get('evidence_id') or row['id'] == args['evidence_id']]
                origin = 'program_preload' if preload else 'model_tool'
                queue_event('query', ('程序预读证据 ' if preload else 'Agent查询证据 ') +
                            (args.get('evidence_id') or '清单、约束及前序部门反馈'),
                            tool=tool, query=args, origin=origin)
                result = _model_payload(snapshot, selected, prior_feedback,
                                        single_source=bool(args.get('evidence_id')))
                read_ids.update(row['id'] for row in selected)
                queue_event('evidence', '读取到 ' + str(len(selected)) + ' 条证据；可展开核对原文',
                            source_ids=[row['id'] for row in selected], tool=tool, origin=origin,
                            versions={row['id']: row['version'] for row in selected})
                return result

            def dispatch(_module, tool, args):
                if tool != 'read_evidence':
                    raise RuntimeError('不支持此工具。')
                return read_evidence(args)

            queue_event('started', '开始' + phase + '，核对本部门事实与前序反馈')
            try:
                if time.monotonic() >= deadline:
                    raise RuntimeError('讨论达到时限。')
                # The orchestration layer actually reads and records these sources
                # before the first request. This is not a fabricated model tool call.
                initial_evidence = read_evidence({}, preload=True)
                answer = runtime.respond(module, task, [], specs, dispatch, local_trace, provider=provider,
                                         budget=model_budget, tool_budget=tool_budget,
                                         initial_evidence=initial_evidence)
                row = {'module': module, 'phase': phase, 'status': 'done', 'answer': answer}
            except Exception:
                # Provider failures remain local; no credentials/raw provider errors
                # enter business logs. Other departments still finish their checks.
                row = {'module': module, 'phase': phase, 'status': 'failed',
                       'answer': '本轮模型未完成，相关结论未核实。'}
            row.update(at=datetime.now(timezone.utc).isoformat(), summary=short_reply(row['answer']),
                       source_ids=sorted(read_ids),
                       duration_ms=round((time.monotonic() - turn_started_at) * 1000))
            updates.put(('complete', (row, local_trace)))

        for module in modules:
            executor.submit(worker, module)
        pending = len(modules)
        completed = []
        while pending:
            kind, value = updates.get()
            if kind == 'event':
                emit(**value)
                continue
            row, local_trace = value
            transcript.append(row)
            trace.extend(local_trace)
            completed.append(row)
            emit(row['module'], phase, 'reply' if row['status'] == 'done' else 'failed', row['summary'],
                 at=row['at'], reply_index=len(transcript)-1, source_ids=row['source_ids'],
                 duration_ms=row['duration_ms'])
            publish(list(transcript))
            pending -= 1
        return completed

    task = '这是合成场景真实模型讨论。程序已预读并附上本轮证据和前序反馈，可直接据此回答；仅在需要单条原文时再调用read_evidence。只引用证据编号，不虚构事实。evidence_precedence、verified_plan和calculation是程序约束。其他Agent可能出错，必须按原始证据纠正，不能把部门推测复述为事实。只用最多3条短句：发现（附证据ID）、分歧/未知、下一步交给谁。总字数不超过120字，不重复背景，不写标题或表格。不编造人员姓名。' + question
    departments = ('buying', 'selling', 'stock', 'manufacturing')
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='supply-discussion') as executor:
        run_phase(executor, departments, '部门初核', task + ' 从本部门说明事实、冲突、待确认条件。')
        run_phase(executor, ('control',), '总控追问', task + ' 读取四部门反馈，指出分歧并向各部门提出需要复核的问题；不得执行。')
        run_phase(executor, departments, '部门复核', task + ' 读取总控追问和其他部门反馈，回应本部门问题；无证据则明确待确认。')
        final = run_phase(executor, ('control',), '处置建议', task + ' 汇总两轮反馈，列出可选方案、数量依据、增量费用、待确认条件、责任人与审批事项。明确失败部门。没有基线不能宣称节省。')[0]
    failed = any(row['status'] == 'failed' for row in transcript)
    answer = '模型讨论已结束，所有部门发言与建议仍需核实，不代表事实已确认。\n' + final['answer']
    if snapshot.get('deterministic_conclusion') and not snapshot.get('human_sources'):
        answer = snapshot['deterministic_conclusion']
    elif snapshot.get('verified_plan') and not snapshot.get('human_sources'):
        answer = ('【程序核算结论；模型发言仅供复核】\n'
                  '若备用采购获批且150件于11月3日09:00可用，220+150=370件，可在10:00用料截止前完整满足销售300件，余70件。这不是成品交付保证。\n'
                  '生产原08:00开工仍无法满足。原厂200件于14:00可用已确认，70+200=270；生产改期至11月4日获批后可分配240件，余30。\n'
                  '已知增量费用480元；其他损失与隐性费用未核实，实际节省未验证。采购及生产改期仍待批准。\n'
                  '下方保留真实模型原话用于复核。模型可能误述时间或成本，不得据此直接执行。')
    if failed:
        answer += '\n部分讨论未完成，不能作为完整处置方案。'
    return {'answer': answer, 'review_required': True,
            'events': events, 'sources': sources, 'discussion': transcript, 'verified_evidence': snapshot, 'trace': trace, 'offers': [], 'synthetic': True,
            'mode': '真实模型讨论 / 合成证据 / 不执行', 'partial': failed,
            'timing': {'total_ms': round((time.monotonic() - started_at) * 1000),
                       'model_calls': sum(item.get('tool') == 'model_request' for item in trace),
                       'department_concurrency': 4}}
