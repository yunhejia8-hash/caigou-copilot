"""Order preferences and bounded, non-executing checks for connector-1286.

Ordering is an enterprise decision, not an industry law. This module does not
approve reservations, authenticate messages, release stock or schedule work.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json


_WORK_ORDER = 'https://docs.frappe.io/erpnext/work-order'
_RULES = (
    {'id': 'ORDER-PREFERENCE', 'label': '订单先后由企业选择', 'kind': '企业策略',
     'description': '本产品默认按需料时点排序；企业可根据客户合同、违约影响等说明理由后指定顺序。这不是行业统一规则，也不构成审批。',
     'source_title': '本次业务策略；调整账号、时间与理由见上方记录', 'source_url': ''},
    {'id': 'RESERVATION', 'label': '已预留料不能直接挪用', 'kind': '本案例硬约束',
     'description': '参考 ERPNext 工单预留控制；本案例 C 预留调整须先批准，并落实原需料时点前的补回责任。优先级不能替代该批准。',
     'source_title': 'ERPNext · Stock Reservation for Work Order',
     'source_url': 'https://docs.frappe.io/erpnext/stock-reservation-for-work-order'},
    {'id': 'QUALITY', 'label': '待检料不能算合格可用', 'kind': '本案例硬约束',
     'description': '参考 ERPNext 质检控制；本案例待检冻结量不参与分配，供应商声称合格也不等于本厂完成检验并放行。',
     'source_title': 'ERPNext · Quality Inspection',
     'source_url': 'https://docs.frappe.io/erpnext/user/manual/en/quality-inspection'},
    {'id': 'USABLE-BY-DEADLINE', 'label': '逐单核对合格可用时点', 'kind': '本案例硬约束',
     'description': '参考 ERPNext 工单计划时间与用料管理；本案例按各工单需料时点检查到仓、收货及放行，不把可发货日期当作可领料时间。',
     'source_title': 'ERPNext · Work Order；本案例用料时点', 'source_url': _WORK_ORDER},
    {'id': 'LINKED-DEMAND', 'label': '销售订单与关联工单不重复计量', 'kind': '本案例口径',
     'description': 'ERPNext 支持工单关联销售订单；本案例以工单未领料需求计量，对应销售订单是同一需求，不能再加一次。',
     'source_title': 'ERPNext · Work Order / Sales Order', 'source_url': _WORK_ORDER},
    {'id': 'WHOLE-BATCH', 'label': '整批限制与替代料许可', 'kind': '本案例硬约束',
     'description': '本案例工单标记不可拆批，部分数量不等于可以开工；整批限制不是行业法规。替代料也须有明确许可，参考 ERPNext 替代料配置，不能凭名称相似自行替换。',
     'source_title': '本案例整批限制；ERPNext · Item Alternative',
     'source_url': 'https://docs.frappe.io/erpnext/item-alternative'},
)


def _text(value, label, maximum=140):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + '无效。')
    return value.strip()


def _time(value, label, require_zone=False):
    value = _text(value, label, 80)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(label + '必须为有效日期时间。') from None
    if 'T' not in value and ' ' not in value:
        raise ValueError(label + '须包含具体时间。')
    if require_zone and parsed.tzinfo is None:
        raise ValueError(label + '须包含时区。')
    return parsed


def _quantity(value, label, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(label + '必须为有效整数件数。')
    return value


def _requirements(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('scenario_key') != 'connector-1286':
        raise ValueError('订单优先级仅支持连接器演示场景。')
    rows = snapshot.get('requirements')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError('场景缺少有效需求表。')
    identifiers = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('工单需求格式无效。')
        identifiers.append(_text(row.get('id'), '工单编号'))
        _text(row.get('sales_order'), '关联销售订单')
        _quantity(row.get('quantity'), '需求数量', positive=True)
        _time(row.get('material_required_at'), '需料时点', require_zone=True)
        if type(row.get('split_allowed')) is not bool:
            raise ValueError('工单必须明确是否允许拆批。')
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('工单需求重复，不能重复计算。')
    return rows


def make_policy(snapshot, order=None, reason=None, recorded_by=None, recorded_at=None):
    """Validate a complete order preference; callers supply the server identity/time."""
    rows = _requirements(snapshot)
    identifiers = [row['id'] for row in rows]
    if order is None:
        mode = 'due_time'
        order = [row['id'] for row in sorted(rows, key=lambda row: (
            _time(row['material_required_at'], '需料时点', require_zone=True), row['id']))]
        reason = '默认按需料时点从早到晚；同一时点按工单编号排序。' if reason is None else _text(reason, '排序理由', 300)
        if recorded_by is not None or recorded_at is not None:
            recorded_by = _text(recorded_by, '记录账号')
            _time(recorded_at, '记录时间')
    else:
        mode = 'manual'
        if (not isinstance(order, list) or any(not isinstance(item, str) for item in order)
                or len(order) != len(identifiers) or len(set(order)) != len(order)
                or set(order) != set(identifiers)):
            raise ValueError('手动排序须包含全部工单编号，不能重复、遗漏或使用其他订单。')
        reason = _text(reason, '排序理由', 300)
        recorded_by = _text(recorded_by, '记录账号')
        _time(recorded_at, '记录时间')
    policy = {'mode': mode, 'order': list(order), 'reason': reason,
              'recorded_by': recorded_by, 'recorded_at': recorded_at}
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    policy['version'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]
    return policy


def _validated_policy(snapshot, policy):
    if not isinstance(policy, dict) or policy.get('mode') not in ('due_time', 'manual'):
        raise ValueError('排序策略无效。')
    expected = make_policy(snapshot, policy.get('order') if policy['mode'] == 'manual' else None,
                           policy.get('reason'), policy.get('recorded_by'), policy.get('recorded_at'))
    if policy != expected:
        raise ValueError('排序策略或版本已变化，请重新确认。')
    return expected


def assess(snapshot, policy):
    """Allocate two separate hypothetical pools, then expose unmet conditions.

    ``assessed`` confirms arithmetic only. A conditional allocation is never a
    promise to start or deliver; unknown receipt/quality times stay unknown.
    """
    policy = _validated_policy(snapshot, policy)
    requirements = {row['id']: row for row in _requirements(snapshot)}
    ranked = [{key: requirements[name][key] for key in ('id', 'sales_order', 'quantity', 'material_required_at')}
              for name in policy['order']]
    base = {'ranked_orders': ranked, 'rules': deepcopy(list(_RULES)), 'execution_authorized': False}
    if snapshot.get('human_sources'):
        return {**base, 'status': 'needs_verification',
                'basis': '历史需求待核验；排序表仅保留原始需求记录，不代表当前需求已确认。',
                'summary': '存在未核实的人员补充，已暂停本轮分配及数值结论；先核验输入，再按所选顺序重算。',
                'allocations': [], 'conditions': ['逐条核验人员补充与原始证据的关系；补充不自动成为到货承诺、质检放行或预留审批。'],
                'tradeoff': '保留所选订单顺序；旧分配不再作为本轮有效方案，也不擅自认定补充内容已改变事实。',
                'totals': {key: None for key in ('confirmed_supply', 'demand', 'confirmed_gap', 'conditional_supply', 'conditional_gap')}}

    inventory = snapshot.get('inventory', {})
    if inventory.get('buckets_disjoint') is not True:
        raise ValueError('库存状态的互斥口径未明确，不能相减计算。')
    free = (_quantity(inventory.get('on_hand'), '账面库存')
            - _quantity(inventory.get('reserved'), '预留库存')
            - _quantity(inventory.get('quality_hold'), '待检库存'))
    if free < 0:
        raise ValueError('库存状态合计超过账面量，请先核验。')
    reservation = snapshot.get('protected_reservation', {})
    release = _quantity(reservation.get('proposed_release'), '拟申请调整量')
    if release > inventory['reserved']:
        raise ValueError('拟申请调整量超过已预留量。')
    _time(reservation.get('material_required_at'), 'C原需料时点', require_zone=True)
    if type(snapshot.get('updated')) is not bool:
        raise ValueError('场景证据版本状态无效。')
    source_id = 'E11' if snapshot['updated'] else 'E05'
    matching = [row for row in snapshot.get('rich_sources', []) if row.get('id') == source_id]
    if len(matching) != 1:
        raise ValueError('提前批缺少唯一有效来源，不能测算。')
    early = _quantity(matching[0].get('claims', {}).get('early_quantity'), '提前批候选量')
    # Neither a supplier claim nor a preference moves any quantity into the
    # confirmed pool. Independent pools prevent counting allocations twice.
    confirmed_remaining = free
    pools = {'free': free, 'release': release, 'early': early}
    demand = sum(row['quantity'] for row in ranked)
    conditional_supply = sum(pools.values())
    allocations, conditions = [], []
    for priority, row in enumerate(ranked, 1):
        required = row['quantity']
        confirmed = min(required, confirmed_remaining)
        confirmed_remaining -= confirmed
        needed, used = required, {}
        for name in ('free', 'release', 'early'):
            used[name] = min(needed, pools[name])
            pools[name] -= used[name]
            needed -= used[name]
        candidate = required - needed
        allocations.append({'work_order': row['id'], 'sales_order': row['sales_order'],
                            'required': required, 'confirmed_allocated': confirmed,
                            'confirmed_gap': required-confirmed, 'conditional_allocated': candidate,
                            'conditional_gap': needed, 'priority': priority})
        deadline = row['material_required_at']
        if used['release']:
            conditions.append(f"{row['id']} 候选依赖调整 C 预留 {used['release']} 件，须批准且在 {deadline} 前可领用；目前未获批准。")
        if used['early']:
            conditions.append(f"{row['id']} 候选依赖 {source_id} 提前批 {used['early']} 件，须在 {deadline} 前到仓、完成本厂收货检验并可领用；到仓及本厂放行时间均未确认，供应商可发不等于按时可用。")
        if requirements[row['id']]['split_allowed'] is False:
            conditions.append(f"{row['id']} 不允许拆批：确定可用测算仍缺 {required-confirmed} 件；"
                              + (f"候选数量也缺 {needed} 件，不能整批开工。" if needed else '候选数量配平仍须满足审批与时间条件，不能承诺齐料开工。'))
    release_used = release - pools['release']
    if release_used:
        conditions.append(f"拟调整 C 预留 {release_used} 件必须落实同量补回，在 {reservation['material_required_at']} 前成为合格可用料；供应商预计到货日期不等于此时点承诺。")
    conditions.extend([f"待检冻结 {inventory['quality_hold']} 件不计入可用；优先级不能代替品质放行。",
                       '这里只测算该连接器，其他物料、生产及交付条件尚未核实；数量配平不等于成品可按时交付。'])
    totals = {'confirmed_supply': free, 'demand': demand, 'confirmed_gap': max(0, demand-free),
              'conditional_supply': conditional_supply, 'conditional_gap': max(0, demand-conditional_supply)}
    order_label = ' → '.join(policy['order'])
    allocation_label = '；'.join(f"{row['work_order']} 候选 {row['conditional_allocated']}，缺 {row['conditional_gap']}" for row in allocations)
    return {**base, 'status': 'assessed',
            'basis': '合成原始证据的数量条件测算；不是生产或交付承诺。',
            'summary': f"按 {order_label} 虚拟测算。确定可用 {free} 件，需求 {demand} 件，仍缺 {totals['confirmed_gap']} 件；条件供给 {conditional_supply} 件，条件缺口 {totals['conditional_gap']} 件。审批及合格可用时间未落实，不代表可开工或已执行。",
            'allocations': allocations, 'conditions': conditions,
            'tradeoff': f"所选顺序为 {order_label}：{allocation_label}。顺序改变候选料的归属，不增加库存；首单确定可用仍缺 {allocations[0]['confirmed_gap']} 件，须继续核实硬约束。",
            'totals': totals}


def model_evidence(snapshot, policy, assessment):
    """Return an isolated model projection with one active priority assessment."""
    policy = _validated_policy(snapshot, policy)
    if assessment != assess(snapshot, policy):
        raise ValueError('评估结果与本轮排序或证据不一致，请重新测算。')
    result = deepcopy(snapshot)
    for key in ('plan', 'verified_plan', 'deterministic_conclusion', 'change'):
        result.pop(key, None)
    if assessment['status'] == 'needs_verification':
        result['calculation'] = {'status': '人员补充待核实，旧数值结论暂停。'}
    else:
        calculation = result.get('calculation', {})
        for key in ('conditional_a_allocation', 'conditional_b_available', 'conditional_b_gap',
                    'allocations', 'priority', 'policy', 'order'):
            calculation.pop(key, None)
    result['priority_policy'] = deepcopy(policy)
    result['priority_assessment'] = deepcopy(assessment)
    result['evidence_precedence'] = (result.get('evidence_precedence', '')
        + ' 本轮订单先后只按 priority_policy，分配及约束检查只按 priority_assessment；来源中的历史部门优先诉求不是当前策略。策略不改变原始事实，不等于审批或执行授权。')
    return result


def final_decision(answer, policy, assessment):
    """Compare the coordinator's explicit fields; never certify prose semantics.

    The model's original response and its summary remain available even when
    fields disagree. A match is not an approval or a feasibility promise.
    """
    if not isinstance(answer, str):
        raise ValueError('总控回答必须为文本。')
    check = {
        'status': 'unverified', 'issues': [], 'policy_version': policy['version'],
        'summary_verified': False,
        'scope': '仅对比结构化字段与本轮程序测算；未验证摘要语义，不代表审批或可开工。',
    }
    result = {'answer': answer, 'raw_answer': answer, 'decision_check': check}

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('重复JSON字段。')
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError('不支持非有限数值。')

    content = answer.strip()
    lines = content.splitlines()
    if len(lines) >= 3 and lines[0].lower() in ('```json', '```') and lines[-1] == '```':
        content = '\n'.join(lines[1:-1])
    try:
        decision = json.loads(content, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (TypeError, ValueError, RecursionError):
        check['reason'] = '总控未返回可可靠解析的决策字段，无法自动核对；保留原文供复核。'
        check['summary'] = check['reason']
        return result
    if not isinstance(decision, dict):
        check['reason'] = '总控决策不是JSON对象，无法自动核对；保留原文供复核。'
        check['summary'] = check['reason']
        return result
    if not isinstance(decision.get('summary'), str) or not decision['summary'].strip() or len(decision['summary']) > 600:
        check['reason'] = '总控缺少有效短摘要，尚未形成完整决策响应；保留原文供复核。'
        check['summary'] = check['reason']
        return result
    result['answer'] = decision['summary'].strip()
    issues = check['issues']
    expected_keys = {'policy_version', 'order', 'confirmed_gap', 'conditional_gap',
                     'allocations', 'execution_authorized', 'summary'}
    for key in sorted(expected_keys - set(decision)):
        issues.append('缺少字段：' + key)
    for key in sorted(set(decision) - expected_keys):
        issues.append('存在未校验的额外字段：' + key)
    if decision.get('policy_version') != policy['version']:
        issues.append('策略版本与本轮不一致。')
    if decision.get('order') != policy['order']:
        issues.append('订单顺序与本轮选择不一致，应为 ' + ' → '.join(policy['order']) + '。')
    if decision.get('execution_authorized') is not False:
        issues.append('执行授权必须为 false；本轮没有批准或执行业务操作。')

    def compare_number(actual, expected, label):
        if type(actual) is not type(expected) or actual != expected:
            expected_text = 'null（人员补充待核验，数值暂停）' if expected is None else str(expected)
            issues.append(label + '与程序测算不一致，应为 ' + expected_text + '。')

    for key, label in (('confirmed_gap', '确定可用缺口'), ('conditional_gap', '条件缺口')):
        compare_number(decision.get(key), assessment['totals'][key], label)
    numeric_fields = ('required', 'confirmed_allocated', 'confirmed_gap', 'conditional_allocated', 'conditional_gap')
    rows = decision.get('allocations')
    if not isinstance(rows, list):
        issues.append('allocations 必须为完整分配列表。')
    else:
        expected_rows = {row['work_order']: row for row in assessment['allocations']}
        actual_rows = {}
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                issues.append(f'分配第 {index} 项不是有效对象。')
                continue
            work_order = row.get('work_order')
            if not isinstance(work_order, str) or work_order not in expected_rows:
                issues.append(f'分配第 {index} 项包含本轮未允许的工单。')
                continue
            if work_order in actual_rows:
                issues.append(work_order + ' 分配重复，不能重复计量。')
                continue
            actual_rows[work_order] = row
            required_keys = {'work_order', *numeric_fields}
            if not required_keys <= set(row) or set(row) - required_keys - {'priority', 'sales_order'}:
                issues.append(work_order + ' 分配字段缺失或包含额外字段。')
            for key in numeric_fields:
                compare_number(row.get(key), expected_rows[work_order][key], work_order + '.' + key)
            if 'priority' in row:
                compare_number(row['priority'], expected_rows[work_order]['priority'], work_order + '.priority')
            if 'sales_order' in row and row['sales_order'] != expected_rows[work_order]['sales_order']:
                issues.append(work_order + ' 关联销售订单与程序核对不一致。')
        for missing in sorted(set(expected_rows) - set(actual_rows)):
            issues.append('缺少 ' + missing + ' 的分配结果。')
        if len(rows) != len(expected_rows):
            issues.append('分配行数与本轮程序测算不一致。')
    if issues:
        check['status'] = 'mismatch'
        check['reason'] = '总控结构化字段与本轮程序测算存在冲突，需纠正后再评审；摘要语义仍未验证。'
    else:
        check['status'] = 'matched'
        check['reason'] = ('字段与本轮暂停状态一致；尚无有效数量结论，摘要语义未验证。'
                           if assessment['status'] == 'needs_verification' else
                           '仅结构化字段与本轮程序条件测算一致；摘要语义未验证，审批和到货条件仍待确认。')
    check['summary'] = check['reason']
    return result
