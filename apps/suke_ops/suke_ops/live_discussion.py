"""Read-only, bounded real-model discussion over explicitly synthetic evidence."""
import json
import time
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


def discuss(snapshot, question, publish, provider=None):
    """4 departments, coordinator challenge, 4 responses, final synthesis; max 30 calls."""
    deadline = time.monotonic() + 780
    transcript, trace, budget, tool_budget = [], [], [30], [40]
    specs = [runtime.schema('read_evidence', '读取合成业务事实、程序计算及本轮部门讨论；不读写ERP', {})]
    def dispatch(module, tool, args):
        if tool != 'read_evidence' or args:
            raise RuntimeError('不支持此工具。')
        return {'evidence': snapshot, 'discussion': list(transcript)}
    def turn(module, phase, task):
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
        transcript.append(row)
        publish(list(transcript))
        return row
    task = '这是合成场景真实模型讨论。先read_evidence，只引用证据编号，不虚构事实。evidence_precedence、verified_plan和calculation是程序约束。其他Agent可能出错，必须按原始证据纠正，不能把部门推测复述为事实。' + question
    for module in runtime.MODULES:
        turn(module, '部门初核', task + ' 从本部门说明事实、冲突、待确认条件，不超过200字。')
    turn('control', '总控追问', task + ' 读取四部门反馈，指出分歧并向各部门提出需要复核的问题；不得执行。')
    for module in runtime.MODULES:
        turn(module, '部门复核', task + ' 读取总控追问和其他部门反馈，回应本部门问题；无证据则明确待确认，不超过200字。')
    final = turn('control', '处置建议', task + ' 汇总两轮反馈，列出可选方案、数量依据、增量费用、待确认条件、责任人与审批事项。明确失败部门。没有基线不能宣称节省。')
    failed = any(row['status'] == 'failed' for row in transcript)
    answer = '模型讨论已结束，所有部门发言与建议仍需核实，不代表事实已确认。\n' + final['answer']
    if snapshot.get('verified_plan'):
        answer = ('【程序核算结论；模型发言仅供复核】\n'
                  '若备用采购获批且150件于11月3日09:00可用，220+150=370件，可在10:00用料截止前完整满足销售300件，余70件。这不是成品交付保证。\n'
                  '生产原08:00开工仍无法满足。原厂200件于14:00可用已确认，70+200=270；生产改期至11月4日获批后可分配240件，余30。\n'
                  '已知增量费用480元；其他损失与隐性费用未核实，实际节省未验证。采购及生产改期仍待批准。\n'
                  '下方保留真实模型原话用于复核。模型可能误述时间或成本，不得据此直接执行。')
    if failed:
        answer += '\n部分讨论未完成，不能作为完整处置方案。'
    return {'answer': answer, 'review_required': True,
            'discussion': transcript, 'verified_evidence': snapshot, 'trace': trace, 'offers': [], 'synthetic': True,
            'mode': '真实模型讨论 / 合成证据 / 不执行', 'partial': failed}
