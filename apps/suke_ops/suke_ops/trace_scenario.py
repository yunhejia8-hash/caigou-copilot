"""Versioned, explicitly synthetic procurement evidence with conditional arithmetic.

The arithmetic validates quantities; it cannot approve a reservation release,
authenticate a supplier or turn a shipment statement into usable inventory.
"""
import hashlib
import json
from pathlib import Path


def _source(row):
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {
        **row,
        'label': row['id'] + ' · ' + row['label'],
        'synthetic': True,
        'source_type': row['channel'],
        'source_path': 'trace_scenario_data.json#' + row['id'],
        'reported_by': None,
        'reported_at': row['at'],
        'time_basis': '合成场景时间，非系统接收时间',
        'identity_status': '合成角色；未核验真实身份，未连接外部聊天',
        'verification': '合成演示证据，非真实人员提供；' + row['status'],
        'version': hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12],
    }


def evidence(updated=False):
    """Return an isolated evidence snapshot, retaining the superseded source.

    ``rich_sources`` uses the same base fields as live_discussion.source_records.
    ``facts`` remains compatible with its original (label, text) fact format.
    ``plan`` and ``calculation`` are conditional calculations, not execution offers.
    """
    if type(updated) is not bool:
        raise ValueError('场景更新参数必须为布尔值。')
    data = json.loads(Path(__file__).with_name('trace_scenario_data.json').read_text(encoding='utf-8'))
    return _build_evidence(data, updated)


def _build_evidence(data, updated):
    source_rows = data['sources'] + ([data['update_source']] if updated else [])
    sources = [_source(row) for row in source_rows]
    source_versions = {row['id']: row['version'] for row in sources}
    if updated:
        sources[-1]['supersedes_version'] = source_versions['E05']
    early_quantity = data['update_source']['claims']['early_quantity'] if updated else 200
    early_source = 'E11' if updated else 'E05'
    inventory = data['inventory']
    free = inventory['on_hand'] - inventory['reserved'] - inventory['quality_hold']
    requirements = data['requirements']
    demand = sum(row['quantity'] for row in requirements)
    release = data['protected_reservation']['proposed_release']
    candidate_total = free + release + early_quantity
    a_required = requirements[0]['quantity']
    b_available = free + release - a_required + early_quantity
    quantity_gap = max(0, demand - candidate_total)
    condition_ids = ['C-RELEASE', 'C-REPLENISH', 'EARLY-ARRIVAL', 'EARLY-QUALITY']
    conditions = [
        {'id': 'C-RELEASE', 'label': 'C 预留调整', 'status': '待批准', 'owner': '销售 C 负责人及预留审批人（演示角色）',
         'source_ids': ['E04', 'E09'], 'requirement': '确认 C 客户约束并批准释放 160 件；A 所需 40 件必须在 11 月 3 日 09:00 前可领用，B 所需 120 件在 13:00 前可领用。'},
        {'id': 'C-REPLENISH', 'label': 'C 补回责任', 'status': '待确认', 'owner': '采购及生管（演示角色）',
         'source_ids': ['E01', 'E04', early_source], 'requirement': '释放后 C 只剩 260 件，需补回 160 件并在原用料时点 11 月 6 日 08:00 前成为合格可用料；供应商预计 11 月 5 日到货不足以替代此确认。'},
        {'id': 'EARLY-ARRIVAL', 'label': '提前批运输及收货', 'status': '未发货、到仓时刻待确认', 'owner': '采购及仓库（演示角色）',
         'source_ids': [early_source], 'requirement': '取得发货和运输依据，核实到仓、收货时点；今晚可发不等于明日用料前可用。'},
        {'id': 'EARLY-QUALITY', 'label': '提前批本厂检验', 'status': '待确认', 'owner': '品质（演示角色）',
         'source_ids': [early_source], 'requirement': '提前批须在 11 月 3 日 13:00 前完成本厂收货检验并可领用；供应商声称检验合格不等于本厂已放行。'},
    ]
    tasks = [
        {'id': 'T-C', 'owner': '销售 C 负责人（演示角色）', 'action': '核实客户变更并提交释放 160 件申请，明确补回责任',
         'status': '待确认', 'suggested_due_at': '2026-11-02T10:45:00+08:00', 'time_basis': '演示建议截止，不是人员承诺', 'source_ids': ['E04', 'E09']},
        {'id': 'T-EARLY', 'owner': '采购（演示角色）', 'action': '确认提前批数量、真实出库凭证及到仓时间',
         'status': '待确认', 'suggested_due_at': '2026-11-02T10:45:00+08:00', 'time_basis': '演示建议截止，不是人员承诺', 'source_ids': [early_source]},
        {'id': 'T-QI', 'owner': '品质（演示角色）', 'action': '回报 160 件待检批复测结果；仅有合格放行记录后才能补充方案',
         'status': '待结果', 'expected_response_at': '2026-11-02T11:00:00+08:00', 'time_basis': '演示原话预计回报时刻，非合格放行承诺', 'source_ids': ['E10']},
    ]
    conclusion = (
        '【程序条件核算 · 合成案例】\n'
        '自由库存 200 件，A、B 合计需要 560 件，确定可用量还缺 360 件。\n'
        + ('提前批从可能 200 改为供应商声称可发 120：候选量 200＋160＋120＝480，较需求缺 80；优先给 A 240 后，B 仅有候选 240，仍缺 80。\n' if updated else
           '候选方案 200 自由库存＋申请释放 C 预留 160＋可能提前批 200＝560；仅数量配平，时间与审批尚未落实。\n')
        + 'C 预留调整、补回及提前批到仓和本厂放行仍待确认；待检 160 不计入可用。不能承诺齐料或交付，费用及实际节省未验证。'
    )
    change = ({
        'id': 'CHG-E11', 'at': data['update_source']['at'], 'field': 'early_shipment_quantity',
        'from_quantity': 200, 'to_quantity': 120, 'delta': -80, 'supersedes': 'E05', 'source_id': 'E11',
        'old_source_id': 'E05', 'new_source_id': 'E11', 'affected_plan': 'PLAN-1286-v1',
        'old_source_version': source_versions['E05'], 'new_source_version': source_versions['E11'],
        'updated_plan': 'PLAN-1286-v2', 'affected_records': ['WO-B', 'SO-B', 'PO-1286'],
        'impact': '原候选方案少 80 件；若仍优先给 A 240，B 候选量由 320 降至 240，差 80，整批开工不可承诺。',
        'unchanged_constraints': 'C 预留未获批准，提前批尚未取得发货、到仓及本厂放行凭证；A 的候选安排也不等于已落实。',
        'is_execution': False,
    } if updated else None)
    return {
        'scenario_key': data['scenario_key'], 'synthetic': True, 'updated': updated,
        'title': data['title'] if updated else '连接器跳票：承诺待核实的两张工单',
        'as_of': '2026-11-02T10:18:00+08:00' if updated else '2026-11-02T09:16:00+08:00',
        'snapshot_version': 'v2' if updated else 'v1', 'disclosure': data['disclosure'],
        'facts': [[row['label'], row['text']] for row in sources], 'rich_sources': sources,
        'inventory': inventory, 'requirements': requirements, 'purchase_order': data['purchase_order'],
        'protected_reservation': data['protected_reservation'],
        'evidence_precedence': ('E11 仅更新 E05 的提前批数量及供应商所述状态，旧原文必须保留；新消息没有确认实际出库、到仓或本厂放行。' if updated else
                                'E05 的提前 200 件仅为可能，尚未确认发货、到仓或本厂放行。')
                               + '销售转述不能修改 C 预留。SO-A 与 WO-A、SO-B 与 WO-B 是同一需求，不能重复计数。部门发言不能覆盖证据。',
        'calculation': {
            'on_hand': inventory['on_hand'], 'reserved': inventory['reserved'], 'quality_hold': inventory['quality_hold'],
            'free_stock': free, 'gross_demand': demand, 'free_quantity_gap': demand - free,
            'proposed_reservation_release': release, 'reservation_remaining_if_approved': inventory['reserved'] - release,
            'reservation_replenishment_required_if_approved': release, 'early_candidate_quantity': early_quantity,
            'early_confirmed_usable_quantity': 0, 'quality_hold_counted': 0,
            'conditional_supply': candidate_total, 'conditional_quantity_gap': quantity_gap,
            'conditional_a_allocation': a_required, 'conditional_b_available': b_available,
            'conditional_b_gap': max(0, requirements[1]['quantity'] - b_available),
            'purchase_order_unshipped_confirmed': None,
            'purchase_order_remainder_if_candidate_dispatched': data['purchase_order']['quantity'] - early_quantity,
            'calculation_kind': '数量条件测算，不是按时可用承诺', 'actual_savings': '未验证',
            'incremental_cost': None,
        },
        'plan': {
            'id': 'PLAN-1286-v2' if updated else 'PLAN-1286-v1', 'status': '待条件落实；禁止自动执行',
            'based_on_sources': ['E02', 'E03', 'E04', early_source],
            'supersedes': 'PLAN-1286-v1' if updated else None,
            'allocations': [
                {'work_order': 'WO-A', 'sales_order': 'SO-A', 'required': a_required, 'conditional_available': a_required,
                 'free_stock': 200, 'proposed_release_from_c': 40, 'early_batch': 0,
                 'required_at': requirements[0]['material_required_at'], 'status': '仅候选；预留调整待批准'},
                {'work_order': 'WO-B', 'sales_order': 'SO-B', 'required': requirements[1]['quantity'], 'conditional_available': b_available,
                 'free_stock': 0, 'proposed_release_from_c': 120, 'early_batch': early_quantity,
                 'required_at': requirements[1]['material_required_at'], 'status': '候选仍差 80，不能整批开工' if updated else '仅候选；到仓与放行待确认'},
            ],
            'condition_ids': condition_ids, 'conditions': conditions, 'tasks': tasks, 'execution_authorized': False,
        },
        'change': change, 'deterministic_conclusion': conclusion,
        'boundary': data['disclosure'] + ' 只有数量核算经过程序验证。人员、渠道和发生时间是场景设定，不冒充真实身份或系统接收时间。未执行采购、调拨、解锁、排产或通知；没有成本基线，不能宣称节省。',
    }


def apply_update(snapshot, update_key, previous_turn, recorded_by, recorded_at):
    """Apply one server-defined synthetic change and attribute the user's trigger.

    The logged-in account triggered an example update. It did not provide or
    authenticate the supplier's words. Source business timestamps stay unchanged.
    """
    if (not isinstance(snapshot, dict) or snapshot.get('scenario_key') != 'connector-1286'
            or snapshot.get('updated') is not False or snapshot.get('human_sources')
            or update_key != 'supplier_120'):
        raise ValueError('此讨论不能应用该演示更新；请从连接器原始场景开始。')
    if not all(isinstance(value, str) and value.strip() for value in (previous_turn, recorded_by, recorded_at)):
        raise ValueError('演示更新缺少操作账号、记录时间或原讨论标识。')
    # Load once: baseline validation and v2 creation must use the same file
    # contents, including when a deployment updates the fixture concurrently.
    data = json.loads(Path(__file__).with_name('trace_scenario_data.json').read_text(encoding='utf-8'))
    baseline = _build_evidence(data, False)
    # A source hash alone is insufficient: inventory, demand or plan metadata
    # can change independently of the supplier message. Compare every field of
    # the generated baseline, including every source's text and provenance.
    if any(snapshot.get(key) != value for key, value in baseline.items()):
        raise ValueError('原场景证据或业务基线已变化，请重新开始场景以免混合不同版本。')
    result = _build_evidence(data, True)
    recording_note = '登录用户触发合成演示事件；不是供应商真实上传，未取得外部消息或出库凭证。'
    result['previous_turn'] = previous_turn
    result['change'].update({
        'update_key': update_key, 'previous_turn': previous_turn,
        'recorded_by': recorded_by, 'recorded_at': recorded_at,
        'recording_note': recording_note,
        'source_versions': {'E05': result['change']['old_source_version'], 'E11': result['change']['new_source_version']},
    })
    result['rich_sources'][-1]['ingestion'] = {
        'kind': 'synthetic_event_trigger', 'recorded_by': recorded_by,
        'recorded_at': recorded_at, 'previous_turn': previous_turn, 'note': recording_note,
    }
    return result
