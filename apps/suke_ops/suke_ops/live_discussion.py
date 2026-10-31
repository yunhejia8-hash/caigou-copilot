"""Read-only, bounded real-model discussion over explicitly synthetic evidence."""
import json
import time
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from suke_ops import agent_runtime as runtime


def evidence(index, updated=False):
    if type(index) is not int or not 0 <= index < 6 or type(updated) is not bool:
        raise ValueError('场景参数无效。')
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


def discuss(snapshot, question, publish, provider=None, on_event=None):
    """4 departments, coordinator challenge, 4 responses, final synthesis; max 30 calls."""
    deadline = time.monotonic() + 780
    transcript, trace, budget, tool_budget = [], [], [30], [40]
    events, phase_now = [], ['']
    sources = source_records(snapshot)
    def emit(module, kind, summary, **extra):
        events.append({'id': len(events) + 1, 'module': module, 'kind': kind,
                       'phase': phase_now[0], 'at': datetime.now(timezone.utc).isoformat(),
                       'summary': summary, **extra})
        if on_event:
            on_event({'events': list(events), 'discussion': list(transcript),
                      'sources': sources, 'verified_evidence': snapshot, 'synthetic': True})
    specs = [runtime.schema('read_evidence', '查询场景证据；可指定证据编号读取原文与提供人。未指定则返回全量证据和部门反馈。',
                            {'evidence_id': runtime.text_schema([row['id'] for row in sources])})]
    def dispatch(module, tool, args):
        if tool != 'read_evidence' or set(args) - {'evidence_id'}:
            raise RuntimeError('不支持此工具。')
        selected = [row for row in sources if not args.get('evidence_id') or row['id'] == args['evidence_id']]
        emit(module, 'query', '查询证据 ' + (args.get('evidence_id') or '清单、约束及部门反馈'), tool=tool, query=args)
        result = {'evidence': snapshot, 'sources': selected, 'discussion': list(transcript)}
        if args.get('evidence_id'):
            result['evidence'] = {'facts': [[row['label'], row['text']] for row in selected],
                                  'boundary': snapshot['boundary']}
        emit(module, 'evidence', '读取到 ' + str(len(selected)) + ' 条证据；可展开核对原文',
             source_ids=[row['id'] for row in selected], tool=tool,
             versions={row['id']: row['version'] for row in selected})
        return result
    def turn(module, phase, task):
        phase_now[0] = phase
        emit(module, 'started', '开始' + phase + '，核对本部门事实与前序反馈')
        # A slow department cannot consume the entire discussion budget.
        local = [min(3, budget[0])]
        initial = local[0]
        try:
            if time.monotonic() >= deadline:
                raise RuntimeError('讨论达到时限。')
            answer = runtime.respond(module, task, [], specs, dispatch, trace, provider=provider,
                                     budget=local, tool_budget=tool_budget)
            row = {'module': module, 'phase': phase, 'status': 'done', 'answer': answer}
        except RuntimeError:
            row = {'module': module, 'phase': phase, 'status': 'failed', 'answer': '本轮模型未完成，相关结论未核实。'}
        budget[0] -= initial - local[0]
        row['at'] = datetime.now(timezone.utc).isoformat()
        row['summary'] = short_reply(row['answer'])
        row['source_ids'] = sorted({sid for event in events if event['module'] == module and event['phase'] == phase for sid in event.get('source_ids', [])})
        transcript.append(row)
        emit(module, 'reply' if row['status'] == 'done' else 'failed', row['summary'],
             reply_index=len(transcript)-1, source_ids=row['source_ids'])
        publish(list(transcript))
        return row
    task = '这是合成场景真实模型讨论。先read_evidence，只引用证据编号，不虚构事实。evidence_precedence、verified_plan和calculation是程序约束。其他Agent可能出错，必须按原始证据纠正，不能把部门推测复述为事实。只用最多3条短句：发现（附证据ID）、分歧/未知、下一步交给谁。总字数不超过120字，不重复背景，不写标题或表格。不编造人员姓名。' + question
    for module in runtime.MODULES:
        turn(module, '部门初核', task + ' 从本部门说明事实、冲突、待确认条件，不超过200字。')
    turn('control', '总控追问', task + ' 读取四部门反馈，指出分歧并向各部门提出需要复核的问题；不得执行。')
    for module in runtime.MODULES:
        turn(module, '部门复核', task + ' 读取总控追问和其他部门反馈，回应本部门问题；无证据则明确待确认，不超过200字。')
    final = turn('control', '处置建议', task + ' 汇总两轮反馈，列出可选方案、数量依据、增量费用、待确认条件、责任人与审批事项。明确失败部门。没有基线不能宣称节省。')
    failed = any(row['status'] == 'failed' for row in transcript)
    answer = '模型讨论已结束，所有部门发言与建议仍需核实，不代表事实已确认。\n' + final['answer']
    if snapshot.get('verified_plan') and not snapshot.get('human_sources'):
        answer = ('【程序核算结论；模型发言仅供复核】\n'
                  '若备用采购获批且150件于11月3日09:00可用，220+150=370件，可在10:00用料截止前完整满足销售300件，余70件。这不是成品交付保证。\n'
                  '生产原08:00开工仍无法满足。原厂200件于14:00可用已确认，70+200=270；生产改期至11月4日获批后可分配240件，余30。\n'
                  '已知增量费用480元；其他损失与隐性费用未核实，实际节省未验证。采购及生产改期仍待批准。\n'
                  '下方保留真实模型原话用于复核。模型可能误述时间或成本，不得据此直接执行。')
    if failed:
        answer += '\n部分讨论未完成，不能作为完整处置方案。'
    return {'answer': answer, 'review_required': True,
            'events': events, 'sources': sources, 'discussion': transcript, 'verified_evidence': snapshot, 'trace': trace, 'offers': [], 'synthetic': True,
            'mode': '真实模型讨论 / 合成证据 / 不执行', 'partial': failed}
