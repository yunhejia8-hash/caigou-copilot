"""A human decision and execution boundary after the existing agent discussion.

build is read-only. act returns a replacement result and never commits; its
caller must lock the owned Agent Turn and save the result in the same transaction.
No model is allowed to invoke these actions or supply quantities/approval states.
"""
from copy import deepcopy
import re

import frappe
from frappe.utils import now_datetime

from suke_ops import api, approval

SCOPE = 'on_hand_only'
ACTIONS = {'bind_case': {'case_name', 'reason'}, 'choose': {'plan_id', 'fingerprint', 'reason'},
           'approve': {'reason'}, 'reject': {'reason'}, 'execute': {'reason'},
           'submit_order': {'order_name', 'source_hash', 'reason'}}


def _text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        frappe.throw(label + '不能为空或超过长度限制')
    return value.strip()


def _owner(turn):
    if frappe.session.user == 'Guest' or turn.owner != frappe.session.user:
        frappe.throw('只能操作自己的讨论结果', frappe.PermissionError)


def wants_resolution(question):
    """Only gate the UI; never derive case identities, quantities or authority."""
    return isinstance(question, str) and bool(re.search(
        r'缺料|争料|供应.{0,24}(?:延期|延迟|晚[一二三四五六七八九十\d]+天|先发)|'
        r'(?:延期|延迟|晚[一二三四五六七八九十\d]+天).{0,24}(?:到货|连接器|物料|先发)|'
        r'先保.{0,20}(?:客户|开工|批次)|销售.{0,30}生产.{0,30}(?:库存|优先|争|料)', question))


def _empty(kind='unbound'):
    return {'kind': kind, 'state': 'blocked', 'title': '请选择本次供应异常',
            'summary': '先关联实际异常及原单据，再形成可选择的分配方案。',
            'case': None, 'fingerprint': '', 'choices': [], 'selected_plan': None,
            'decision': None, 'approved_orders': [], 'requirements': [],
            'can': dict.fromkeys(('choose', 'approve', 'reject', 'execute', 'submit_orders'), False)}


def _synthetic(result):
    card = _empty('synthetic')
    card.update(title='演示方案 · 待核实条件',
                summary='以下仅为合成证据测算，可调整订单优先级复核；不会创建审批、采购单或库存预留。')
    snapshot = result.get('verified_evidence') or result.get('scenario') or {}
    if snapshot.get('scenario_key') != 'connector-1286':
        card['requirements'] = ['演示讨论不能转为真实业务执行，请另行绑定真实异常。']
        return card
    from suke_ops.priority_policy import make_policy, assess
    # Validate the evidence requirements before offering a choice. The source
    # snapshot time is a calculation basis, never a newly recorded user action.
    try:
        default = make_policy(snapshot)
        orders = default['order']
        assessments = []
        for order in (orders, list(reversed(orders))):
            policy = make_policy(snapshot, order, '演示候选排序；不构成执行授权',
                                 '演示候选（来源快照时点，非真实操作）', snapshot.get('as_of'))
            assessments.append((order, assess(snapshot, policy)))
    except (ValueError, TypeError, KeyError):
        card['requirements'] = ['演示证据缺少有效需求、来源时间或库存条件，请核实后重新测算。']
        return card
    card['source_as_of'] = snapshot['as_of']
    for index, (order, assessment) in enumerate(assessments):
        if assessment['status'] != 'assessed':
            card['summary'] = assessment['summary']
            card['requirements'] = assessment['conditions']
            return card
        card['choices'].append({'id': 'demo-' + str(index + 1), 'title': ' → '.join(order),
            'summary': assessment['summary'], 'stock_uom': '件', 'allocations': [
                {'document': row['work_order'], 'document_type': 'Work Order', 'qty': row['required'],
                 'stock_uom': '件',
                 'allocated_qty': row['confirmed_allocated'], 'shortage_qty': row['confirmed_gap'],
                 'current_new_qty': row['confirmed_allocated'],
                 'conditional_allocated_qty': row['conditional_allocated'],
                 'conditional_shortage_qty': row['conditional_gap']} for row in assessment['allocations']],
            'shortage_qty': assessment['totals']['confirmed_gap'],
            'current_free_qty': assessment['totals']['confirmed_supply'],
            'conditional_supply_qty': assessment['totals']['conditional_supply'],
            'conditional_shortage_qty': assessment['totals']['conditional_gap'], 'cost': None,
            'cost_note': '未取得真实报价；不能估作零费用', 'conditions': assessment['conditions']})
    card['state'] = 'needs_choice' if card['choices'] else 'blocked'
    card['requirements'] = ['请使用“调整优先级并复核”比较演示方案；真实执行入口保持关闭。']
    return card


def _is_demo(case):
    return bool(case.is_simulation or str(case.item_code).startswith('SUKE-') or '演示' in str(case.company))


def _check_po(po, case):
    po.check_permission('read')
    if po.company != case.company or not any(row.item_code == case.item_code and row.warehouse == case.warehouse for row in po.items):
        frappe.throw('采购单不属于本次异常的公司、物料和仓库', frappe.PermissionError)
    for doc in [po, *po.items, *(po.get('taxes') or []), *(po.get('payment_schedule') or [])]:
        for field in doc.meta.fields:
            if field.permlevel and not doc.has_permlevel_access_to(field.fieldname):
                frappe.throw('没有读取采购清单所需字段的权限', frappe.PermissionError)
    return po


def _approved_orders(case):
    if not case.purchase_order or _is_demo(case):
        return []
    po = _check_po(frappe.get_doc('Purchase Order', case.purchase_order), case)
    if po.docstatus not in (0, 1) or po.status in ('Closed', 'Cancelled', 'Completed'):
        return []
    signature = approval.document_hash(po)
    rows = frappe.get_list('Operations Approval', filters={'source_type': 'Purchase Order',
        'source_name': po.name, 'workflow_state': '已批准', 'source_hash': signature},
        fields=['name'], order_by='modified desc', limit_page_length=1)
    if not rows:
        return []
    approved = frappe.get_doc('Operations Approval', rows[0].name)
    approved.check_permission('read')
    if (approved.source_type != 'Purchase Order' or approved.source_name != po.name
            or approved.workflow_state != '已批准' or approved.source_hash != signature):
        return []
    return [{'name': po.name, 'supplier': po.supplier, 'currency': po.currency,
             'grand_total': po.grand_total, 'approval': approved.name,
             'source_hash': signature, 'docstatus': po.docstatus,
             'items': [{'item_code': row.item_code, 'qty': row.qty, 'uom': row.uom,
                        'rate': row.rate, 'amount': row.amount, 'warehouse': row.warehouse,
                        'schedule_date': str(row.schedule_date)} for row in po.items],
             'can_submit': po.docstatus == 0 and bool(frappe.has_permission('Purchase Order', 'submit', doc=po))}]


def _choice(plan, stock_uom=None):
    unit = stock_uom or '（库存单位未提供）'
    return {'id': plan['id'], 'title': plan['title'],
            'summary': f"本次可分配现存自由库存 {plan['current_free_qty']:g} {unit}；分配后未覆盖需求 {plan['shortage_qty']:g} {unit}。",
            'stock_uom': stock_uom,
            'allocations': [{**{key: row[key] for key in ('document', 'document_type', 'qty', 'allocated_qty',
                              'shortage_qty', 'current_new_qty')}, 'stock_uom': stock_uom} for row in plan['allocations']],
            'shortage_qty': plan['shortage_qty'], 'current_free_qty': plan['current_free_qty'],
            'cost': None, 'cost_note': '本次仅分配现存；催交或追加采购费用尚未核实。',
            'conditions': ['保留所有现有预留归属；不释放或挪用其他订单的锁定库存。',
                           '可能先发、在途和模拟承诺均不计入本次可执行库存。',
                           '冻结/待检数量按异常记录扣除，执行前须由仓库核实该记录；不代表质检自动放行。',
                           '缺口只作为待处理事项；不自动转为追加采购数量，原延期采购仍按原单跟进。']}


def _case_name(result):
    context = result.get('resolution_context') or {}
    if context.get('explicit_case'):
        return _text(context['explicit_case'], '异常单号', 140)
    names = list(dict.fromkeys(context.get('case_names') or []))
    return names[0] if len(names) == 1 else None


def build(turn, result):
    """Read current authorities, never infer a real case from the chat wording."""
    _owner(turn)
    if result.get('synthetic') or (result.get('verified_evidence') or result.get('scenario') or {}).get('synthetic'):
        return _synthetic(result)
    context = result.get('resolution_context') or {}
    if not (context.get('explicit_case') or context.get('case_names') or result.get('resolution')
            or wants_resolution(turn.question)):
        return None
    name = _case_name(result)
    if not name:
        card = _empty()
        card['requirements'] = ['请指定本次供应异常单号；不能仅凭“600件连接器”匹配公司、采购单或订单。']
        return card
    card = _empty('erp')
    try:
        case = api.read_case(name)
        analysis = api.get_analysis(name, scope=SCOPE)
        stock_uom = analysis['context'].get('stock_uom')
        card.update(case={'name': case.name, 'item_code': case.item_code, 'warehouse': case.warehouse,
                          'is_simulation': _is_demo(case), 'purchase_order': case.purchase_order,
                          'stock_uom': stock_uom},
                    fingerprint=analysis['fingerprint'], choices=[_choice(p, stock_uom) for p in analysis['plans']],
                    title='选择本次现存分配方案', state='needs_choice',
                    summary='先选择保障顺序并送审；审批通过后仅执行现存自由库存预留。未来到货不作为现存。')
        if _is_demo(case):
            card.update(kind='synthetic', summary='这是合成业务案例；仅展示候选分配，不允许从聊天执行真实单据。')
            card['requirements'] = ['合成案例不能生成真实审批或采购执行。']
            return card
        old = result.get('resolution') or {}
        selected = old.get('decision') if (old.get('case') or {}).get('name') == case.name else None
        if selected:
            card.update({key: old[key] for key in ('selected_by', 'selected_at', 'reason') if key in old})
        roles = set(frappe.get_roles())
        approver = frappe.session.user == 'Administrator' or bool(roles & {'Supply Approver', 'System Manager'})
        planner = frappe.session.user == 'Administrator' or bool(roles & api.PLANNERS)
        card['can']['choose'] = planner and bool(frappe.has_permission('Supply Decision', 'create'))
        if selected:
            decision = frappe.get_doc('Supply Decision', selected['name'])
            decision.check_permission('read')
            plan = api.loads(decision.plan_json, {})
            if decision.supply_case != case.name or plan.get('scope') != SCOPE:
                frappe.throw('原方案不属于本次现存分配范围', frappe.PermissionError)
            card.update(decision={'name': decision.name, 'workflow_state': decision.workflow_state},
                        selected_plan=decision.selected_plan)
            writable = bool(frappe.has_permission('Supply Decision', 'write', doc=decision))
            if decision.workflow_state == '已执行':
                receipt = api.loads(decision.execution_result, {})
                reserved = bool(receipt.get('reservations'))
                card.update(state='executed', title='已执行现存分配' if reserved else '已完成方案跟进', choices=[_choice(plan, stock_uom)],
                            fingerprint=decision.snapshot_hash,
                            execution=receipt,
                            summary=('已创建获批的现存库存预留；' if reserved else '本次没有新增库存预留；具体跟进待办见执行回执。') + '缺口跟进不等于已采购或已到货。')
                card['can']['choose'] = False
            elif decision.snapshot_hash != analysis['fingerprint']:
                card.update(state='stale', title='依据已变化，请重新选择',
                            summary='旧方案不能继续批准或执行；以下是当前供需重新计算的方案。')
                card['requirements'] = ['原审批失去执行依据，请选择当前版本并重新送审。']
            elif decision.workflow_state == '待审批':
                card.update(state='pending_approval', title='方案已送审，等待负责人决定')
                card['can'].update(choose=False, approve=approver and writable, reject=approver and writable)
            elif decision.workflow_state == '已批准':
                card.update(state='approved', title='方案已批准，等待执行')
                card['can'].update(choose=False, execute=approver and writable)
            elif decision.workflow_state == '已驳回':
                card.update(state='blocked', title='方案已驳回，请调整选择或说明')
                card['requirements'] = [decision.reason]
        try:
            card['approved_orders'] = _approved_orders(case)
        except frappe.PermissionError:
            card['requirements'].append('没有读取相关采购审批清单的权限；未据此判断订单是否已审批。')
        card['can']['submit_orders'] = any(row['can_submit'] for row in card['approved_orders'])
        if not card['approved_orders']:
            card['requirements'].append('没有核实到与当前订单内容一致的已批准采购清单；本次不会生成或追加采购单。')
        return card
    except (frappe.PermissionError, frappe.DoesNotExistError):
        card = _empty('erp')
        card.update(title='当前依据不可读取', summary='来源不存在或权限已变化，无法继续审批或执行。',
                    requirements=['请核实异常及其公司、库存、预留和关联订单的访问权限。'])
        return card
    except (ValueError, frappe.ValidationError):
        card = _empty('erp')
        card.update(title='当前依据尚不能生成可执行方案',
                    summary='库存、预留、冻结口径或来源字段不完整，不能继续分配。',
                    requirements=['请先核实真实异常的公司、仓库、库存状态、订单数量及时间字段，再刷新结果。'])
        return card


def act(turn, result, action, **kwargs):
    """User-triggered only; caller owns transaction/Agent Turn row lock."""
    _owner(turn)
    if action not in ACTIONS or set(kwargs) - ACTIONS[action]:
        frappe.throw('不支持的决策动作或参数')
    if 'reason' in kwargs:
        kwargs['reason'] = _text(kwargs['reason'], '操作说明')
    updated = deepcopy(result)
    current = build(turn, result) or _empty()
    if current['kind'] == 'synthetic':
        frappe.throw('合成讨论只支持演示复核，不能审批或执行真实业务')
    if action == 'bind_case':
        name = _text(kwargs.get('case_name'), '异常单号', 140)
        api.read_case(name)
        if current.get('decision') and current['state'] in ('pending_approval', 'approved', 'executed'):
            frappe.throw('当前讨论已关联审批，请新建讨论处理其他异常')
        updated['resolution_context'] = {**updated.get('resolution_context', {}), 'explicit_case': name}
        updated.pop('resolution', None)
    else:
        if not current.get('case') or current['kind'] != 'erp':
            frappe.throw('请先关联可读取的真实供应异常')
        case = api.read_case(current['case']['name'])
        if _is_demo(case):
            frappe.throw('合成案例不能执行真实业务')
        if action == 'choose':
            plan = _text(kwargs.get('plan_id'), '方案编号', 80)
            fingerprint = _text(kwargs.get('fingerprint'), '依据版本', 80)
            reason = _text(kwargs.get('reason'), '选择理由')
            if fingerprint != current['fingerprint']:
                frappe.throw('库存或来源已变化，请刷新后重新选择')
            same = current['selected_plan'] == plan and current.get('decision')
            if same and current['state'] in ('pending_approval', 'approved', 'executed'):
                updated['resolution'] = current
                return updated
            if not current['can']['choose'] or plan not in {row['id'] for row in current['choices']}:
                frappe.throw('当前没有选择该方案的权限或状态不允许', frappe.PermissionError)
            decision_name = api.propose(case.name, plan, fingerprint, reason, scope=SCOPE)
            decision = frappe.get_doc('Supply Decision', decision_name)
            if decision.workflow_state in ('草稿', '已驳回'):
                api.transition(decision_name, '提交审批' if decision.workflow_state == '草稿' else '修正重提', reason)
            updated['resolution'] = {**current, 'selected_plan': plan,
                                     'decision': {'name': decision_name},
                                     'selected_by': frappe.session.user, 'selected_at': str(now_datetime()), 'reason': reason}
        elif action in ('approve', 'reject', 'execute'):
            decision = current.get('decision')
            if not decision:
                frappe.throw('尚未选择并送审方案')
            if (action == 'execute' and current['state'] == 'executed') or (action == 'approve' and current['state'] == 'approved'):
                updated['resolution'] = current
                return updated
            if not current['can'][action]:
                frappe.throw('当前状态、依据或权限不允许此动作', frappe.PermissionError)
            if action == 'execute':
                api.execute(decision['name'])
            else:
                api.transition(decision['name'], '批准' if action == 'approve' else '驳回',
                               _text(kwargs.get('reason'), '审批说明') if action == 'reject' or kwargs.get('reason') else None)
        elif action == 'submit_order':
            _submit_order(case, kwargs)
    updated['resolution'] = build(turn, updated)
    history = list(updated.get('resolution_actions') or [])
    history.append({'action': action, 'by': frappe.session.user, 'at': str(now_datetime()),
                    'case': (updated['resolution'].get('case') or {}).get('name'),
                    'decision': updated['resolution'].get('decision'),
                    'order': kwargs.get('order_name'), 'reason': kwargs.get('reason')})
    updated['resolution_actions'] = history
    return updated


def _submit_order(case, values):
    name = _text(values.get('order_name'), '采购单号', 140)
    expected = _text(values.get('source_hash'), '采购审批版本', 80)
    if name != case.purchase_order:
        frappe.throw('采购单不属于本次异常', frappe.PermissionError)
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', name)
    po = _check_po(frappe.get_doc('Purchase Order', name), case)
    if '演示' in str(po.company) or any(str(row.item_code).startswith('SUKE-') for row in po.items):
        frappe.throw('演示采购单不能从真实执行入口提交')
    if approval.document_hash(po) != expected:
        frappe.throw('采购单内容已变化，原批准不可执行')
    matching = next((row for row in _approved_orders(case) if row['name'] == name and row['source_hash'] == expected), None)
    if not matching:
        frappe.throw('没有与当前采购单内容一致的真实审批')
    frappe.db.sql('select name from `tabOperations Approval` where name=%s for update', matching['approval'])
    approved = frappe.get_doc('Operations Approval', matching['approval'])
    approved.check_permission('read')
    if (approved.workflow_state != '已批准' or approved.source_hash != expected
            or approved.source_type != 'Purchase Order' or approved.source_name != name):
        frappe.throw('采购审批状态已变化，请重新核实')
    if po.docstatus == 1:
        return  # The same approved order was already submitted; never duplicate it.
    po.check_permission('write')
    po.check_permission('submit')
    if po.docstatus != 0 or not matching['can_submit']:
        frappe.throw('当前采购单不可提交', frappe.PermissionError)
    po.submit()
