"""对话主页 API：真实业务面板、用户隔离会话、需求登记和原生草稿入口。"""
import hashlib
import json
import uuid
import time

import frappe
from frappe.utils import cint, flt, now_datetime, nowdate

from suke_ops import agent_runtime as runtime
from suke_ops import demand

MODULE_DOCS = {
    'buying': ['Material Request', 'Request for Quotation', 'Supplier Quotation', 'Purchase Order', 'Supplier', 'Purchase Receipt', 'Quality Inspection'],
    'selling': ['Customer', 'Quotation', 'Sales Order', 'Delivery Note', 'Sales Invoice'],
    'stock': ['Item', 'Warehouse', 'Bin', 'Stock Reservation Entry', 'Purchase Order', 'Purchase Receipt', 'Stock Entry', 'Stock Reconciliation', 'Quality Inspection'],
    'manufacturing': ['BOM', 'Production Plan', 'Work Order', 'Job Card', 'Stock Entry'],
}
STAGE_DOCS = {
    'buying-demand': ['Material Request', 'Item', 'Bin'],
    'buying-inquiry': ['Request for Quotation', 'Supplier Quotation', 'Supplier', 'Material Request'],
    'buying-comparison': ['Supplier Quotation', 'Request for Quotation', 'Supplier', 'Item'],
    'buying-approval': ['Operations Approval', 'Purchase Order', 'Supplier Quotation'],
    'buying-order': ['Purchase Order', 'Material Request', 'Supplier Quotation', 'Supplier'],
    'buying-receipt': ['Purchase Receipt', 'Purchase Order', 'Quality Inspection', 'Bin', 'Warehouse'],
}
AGENT_DOCS = {**MODULE_DOCS, **STAGE_DOCS}
LABELS = {'Material Request': '采购申请', 'Request for Quotation': '询价', 'Supplier Quotation': '供应商报价',
          'Purchase Order': '采购订单', 'Supplier': '供应商', 'Customer': '客户', 'Quotation': '销售报价',
          'Sales Order': '销售订单', 'Delivery Note': '发货', 'Sales Invoice': '销售账单', 'Item': '物料',
          'Warehouse': '仓库', 'Bin': '库存明细', 'Stock Reservation Entry': '库存预留', 'Purchase Receipt': '收货',
          'Stock Entry': '出入库与调拨', 'Stock Reconciliation': '盘点', 'Quality Inspection': '质检',
          'BOM': '物料清单', 'Production Plan': '生产计划', 'Work Order': '工单', 'Job Card': '工序执行'}
SHARED_DOCS = ['Supply Case', 'Supply Decision', 'Operations Approval']
FIELDS = ['name', 'modified', 'docstatus', 'status', 'title', 'supplier', 'customer', 'company', 'currency',
          'material_request_type', 'per_ordered', 'valid_till', 'source_type', 'source_name', 'reason',
          'report_date', 'inspection_type', 'reference_type', 'reference_name', 'sample_size', 'grand_total', 'schedule_date', 'delivery_date', 'transaction_date', 'posting_date', 'per_received',
          'per_delivered', 'production_item', 'qty', 'produced_qty', 'planned_start_date', 'item_code',
          'warehouse', 'actual_qty', 'reserved_qty', 'reserved_stock', 'projected_qty', 'workflow_state']
DRAFTS = {
    'purchase_from_request': ('buying', 'Material Request', 'Purchase Order', 'erpnext.stock.doctype.material_request.material_request.make_purchase_order'),
    'receive_purchase': ('stock', 'Purchase Order', 'Purchase Receipt', 'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt'),
    'deliver_sales': ('selling', 'Sales Order', 'Delivery Note', 'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note'),
    'finish_production': ('manufacturing', 'Work Order', 'Stock Entry', 'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry'),
    'inquiry_from_request': ('buying', 'Material Request', 'Request for Quotation', 'erpnext.stock.doctype.material_request.material_request.make_request_for_quotation'),
    'purchase_from_quotation': ('buying', 'Supplier Quotation', 'Purchase Order', 'erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order'),
}


def draft_modules(action):
    return {DRAFTS[action][0], *{
        'purchase_from_request': ['buying-order'],
        'inquiry_from_request': ['buying-demand', 'buying-inquiry'],
        'purchase_from_quotation': ['buying-comparison', 'buying-order'],
    }.get(action, [])}


def require_user():
    if (frappe.session.user == 'Guest' or frappe.get_cached_value('User', frappe.session.user, 'user_type') != 'System User'
            or not frappe.get_cached_value('User', frappe.session.user, 'enabled')):
        frappe.throw('请用企业内部账号登录。', frappe.PermissionError)


def check_module(module, control=False):
    if module not in AGENT_DOCS and not (control and module == 'control'):
        frappe.throw('模块不在授权范围。', frappe.PermissionError)


def fields_for(doctype):
    meta = frappe.get_meta(doctype)
    return [field for field in FIELDS if not (doctype == 'Bin' and field == 'reserved_qty') and
            (field in ('name', 'modified', 'docstatus') or
             (meta.has_field(field) and not cint(meta.get_field(field).permlevel)))]


def records(doctype, filters=None, limit=12):
    if not frappe.has_permission(doctype, 'read'):
        return []
    return frappe.get_list(doctype, fields=fields_for(doctype), filters=filters or {},
                           order_by='modified desc', limit_page_length=limit)


def count(doctype, filters):
    if not frappe.has_permission(doctype, 'read'):
        return None
    rows = frappe.get_list(doctype, fields=[{'COUNT': 'name', 'as': 'total'}], filters=filters)
    return cint(rows[0].total) if rows else 0


def module_board(module):
    check_module(module)
    if module in STAGE_DOCS:
        return stage_board(module)
    if module in ('buying', 'selling'):
        purchase = module == 'buying'
        doctype = 'Purchase Order' if purchase else 'Sales Order'
        progress, due = ('per_received', 'schedule_date') if purchase else ('per_delivered', 'delivery_date')
        pending = {'docstatus': 1, 'status': ['not in', ['Closed', 'Cancelled']], progress: ['<', 100]}
        late = {**pending, due: ['<', nowdate()]}
        metrics = [{'label': '待收货订单' if purchase else '待交付订单', 'value': count(doctype, pending), 'unit': '单'},
                   {'label': '已逾期订单', 'value': count(doctype, late), 'unit': '单'},
                   {'label': '订单草稿', 'value': count(doctype, {'docstatus': 0}), 'unit': '单'}]
        rows = records(doctype, pending)
    elif module == 'manufacturing':
        doctype = 'Work Order'
        pending = {'docstatus': 1, 'status': ['not in', ['Completed', 'Stopped', 'Closed', 'Cancelled']]}
        metrics = [{'label': '未完成工单', 'value': count(doctype, pending), 'unit': '单'},
                   {'label': '生产中', 'value': count(doctype, {'docstatus': 1, 'status': 'In Process'}), 'unit': '单'},
                   {'label': '待启动工单', 'value': count(doctype, {'docstatus': 1, 'status': 'Not Started'}), 'unit': '单'}]
        rows = records(doctype, pending)
    else:
        doctype = 'Bin'
        metrics = [{'label': '有现存量的物料仓位', 'value': count('Bin', {'actual_qty': ['>', 0]}), 'unit': '项'},
                   {'label': '有效预留记录', 'value': count('Stock Reservation Entry', {'docstatus': 1, 'status': ['not in', ['Closed', 'Cancelled', 'Delivered', 'Consumed']]}), 'unit': '条'},
                   {'label': '预计缺料仓位（未分交期）', 'value': count('Bin', {'projected_qty': ['<', 0]}), 'unit': '项'}]
        rows = records('Bin', {'actual_qty': ['!=', 0]})
    sections, signals = board_details(module, metrics)
    return {'sections': sections, 'signals': signals, 'module': module, 'as_of': nowdate(), 'label': runtime.MODULES[module], 'metrics': metrics, 'doctype': doctype,
            'stock_meaning': 'Bin.reserved_stock 是实际锁定量；projected_qty 是未分交期的账面预计量，不代表按时可交付。精确交期缺口用 compare_supply_case。',
            'rows': rows, 'row_limit': 12, 'access': frappe.has_permission(doctype, 'read'),
            'links': [{'doctype': dt, 'label': LABELS[dt]} for dt in MODULE_DOCS[module]
                      if dt != 'Bin' and frappe.has_permission(dt, 'read')]}


def stage_scope(module, doctype):
    if module in STAGE_DOCS:
        if doctype == 'Material Request':
            return {'material_request_type': 'Purchase'}
        if doctype == 'Operations Approval':
            return {'source_type': 'Purchase Order'}
    return {}


def stage_board(module):
    """进度直接引用业务状态和完成比例；六环节计数不是同一批单据的漏斗。"""
    definitions = {
        'buying-demand': ('Material Request', [('schedule_date', '需求日期'), ('per_ordered', '已订购 %')], [
            ('待提交', {'docstatus': 0}), ('待采购', {'docstatus': 1, 'per_ordered': ['<', 100], 'status': ['not in', ['Stopped', 'Cancelled']]}),
            ('已订购', {'docstatus': 1, 'per_ordered': ['>=', 100]})]),
        'buying-inquiry': ('Request for Quotation', [('transaction_date', '询价日期'), ('schedule_date', '需求日期')], [
            ('询价草稿', {'docstatus': 0}), ('已提交询价', {'docstatus': 1}), ('已取消询价', {'docstatus': 2})]),
        'buying-comparison': ('Supplier Quotation', [('supplier', '供应商'), ('currency', '币种'), ('grand_total', '报价总额'), ('valid_till', '有效至')], [
            ('报价草稿', {'docstatus': 0}), ('已提交报价', {'docstatus': 1}), ('已过有效期', {'docstatus': 1, 'valid_till': ['<', nowdate()]})]),
        'buying-approval': ('Operations Approval', [('source_name', '采购单'), ('reason', '申请说明')], [
            ('待审批', {'workflow_state': '待审批'}), ('已驳回', {'workflow_state': '已驳回'}), ('已批准', {'workflow_state': '已批准'})]),
        'buying-order': ('Purchase Order', [('supplier', '供应商'), ('schedule_date', '订单交期'), ('per_received', '已收货 %')], [
            ('订单草稿', {'docstatus': 0}), ('待收货', {'docstatus': 1, 'per_received': ['<', 100], 'status': ['not in', ['Closed', 'Cancelled']]}),
            ('已收齐', {'docstatus': 1, 'per_received': ['>=', 100]}),
            ('已逾期订单', {'docstatus': 1, 'per_received': ['<', 100], 'status': ['not in', ['Closed', 'Cancelled']], 'schedule_date': ['<', nowdate()]})]),
        'buying-receipt': ('Purchase Receipt', [('supplier', '供应商'), ('posting_date', '收货日期'), ('grand_total', '金额'), ('currency', '币种')], [
            ('待提交收货', {'docstatus': 0}), ('已提交收货', {'docstatus': 1, 'is_return': 0}), ('退货单', {'docstatus': 1, 'is_return': 1})]),
    }
    doctype, extra_columns, progress = definitions[module]
    scope = stage_scope(module, doctype)
    metrics = [{'label': label, 'value': count(doctype, {**scope, **filters}), 'unit': '单'} for label, filters in progress]
    sections = []
    if module == 'buying-receipt':
        sections, _ = board_details('stock', [])
        sections = [row for row in sections if row['doctype'] == 'Quality Inspection']
        sections.insert(0, {'doctype': 'Purchase Order', 'label': '待到货订单', 'access': frappe.has_permission('Purchase Order', 'read'),
                           'columns': [('name', '订单'), ('supplier', '供应商'), ('schedule_date', '交期'), ('per_received', '已收货 %')],
                           'rows': records('Purchase Order', {'docstatus': 1, 'per_received': ['<', 100], 'status': ['not in', ['Closed', 'Cancelled']]})})
        metrics.append({'label': '质检不合格', 'value': count('Quality Inspection', {'docstatus': ['<', 2], 'inspection_type': 'Incoming', 'status': 'Rejected'}), 'unit': '单'})
        sections[-1]['rows'] = records('Quality Inspection', {'inspection_type': 'Incoming', 'docstatus': ['<', 2]})
    elif module == 'buying-inquiry':
        sections.append({'doctype': 'Supplier Quotation', 'label': '已收到的报价（需按询价单核对）',
                         'access': frappe.has_permission('Supplier Quotation', 'read'),
                         'columns': [('name', '报价单'), ('supplier', '供应商'), ('status', '状态'), ('valid_till', '有效至')],
                         'rows': records('Supplier Quotation', {'docstatus': ['<', 2]})})
    signals = [f"{metric['label']}：{metric['value']}单，需跟进。" for metric in metrics
               if metric['value'] and metric['label'] in ('待采购', '已过有效期', '待审批', '已驳回', '待收货', '已逾期订单', '质检不合格')]
    rows = records(doctype, {**scope, 'docstatus': ['<', 2]})
    if module != 'buying-approval':
        lines = []
        for row in rows:
            detail = read_record(module, doctype, row.name)
            for item in detail.get('suppliers' if module == 'buying-inquiry' else 'items', []):
                lines.append({**row, **item, 'name': row.name})
            if module == 'buying-order' and any(item.get('schedule_date') and str(item['schedule_date']) != str(row.get('schedule_date')) for item in detail.get('items', [])):
                signals.append(f"{row.name}：订单交期与物料行交期不一致，请核实实际供货安排。")
        if module == 'buying-inquiry':
            columns = [('name', '询价单'), ('supplier', '供应商'), ('quote_status', '报价进度'), ('email_sent', '已发邮件')]
        else:
            columns = [('name', '单号'), ('item_code', '物料'), ('qty', '数量'), ('uom', '单位')]
            if module == 'buying-comparison':
                columns += [('supplier', '供应商'), ('rate', '单价'), ('currency', '币种'), ('schedule_date', '交期')]
            elif module == 'buying-order':
                columns += [('received_qty', '已收货'), ('schedule_date', '物料交期')]
            elif module == 'buying-receipt':
                columns += [('warehouse', '入库仓库'), ('quality_inspection', '质检单')]
            else:
                columns += [('schedule_date', '需求日期')]
        sections.insert(0, {'doctype': doctype, 'label': '供应商回复进度' if module == 'buying-inquiry' else '物料明细（最近单据，最多60行）',
                           'columns': columns, 'access': frappe.has_permission(doctype, 'read'), 'rows': lines[:60]})
    return {'module': module, 'label': runtime.MODULES[module], 'as_of': nowdate(), 'doctype': doctype,
            'metrics': metrics, 'sections': sections, 'signals': signals, 'access': frappe.has_permission(doctype, 'read'),
            'rows': rows, 'row_limit': 12,
            'columns': [('name', '单号'), ('workflow_state' if module == 'buying-approval' else 'status', '进度 / 状态'), *extra_columns],
            'note': '按当前账号权限统计；列表显示最近12笔。进度以各单据状态为准，环节间数量不能直接当作转化率。'}


def board_details(module, metrics):
    definitions = {
        'buying': [('Material Request', '采购申请'), ('Purchase Receipt', '到货记录'), ('Quality Inspection', '到货质检')],
        'selling': [('Delivery Note', '发货记录')],
        'stock': [('Stock Reservation Entry', '库存预留'), ('Quality Inspection', '质量检验'), ('Purchase Receipt', '收货记录')],
        'manufacturing': [('Job Card', '工序执行')],
    }
    sections = []
    for doctype, label in definitions[module]:
        columns = [('name', '单号'), ('status', '状态')]
        if doctype == 'Quality Inspection':
            columns += [('item_code', '物料'), ('report_date', '检验日期'), ('inspection_type', '类型'), ('sample_size', '样本数量'), ('reference_name', '关联单据')]
        elif doctype == 'Stock Reservation Entry':
            columns += [('item_code', '物料'), ('warehouse', '仓库'), ('reserved_qty', '预留数量')]
        else:
            columns += [('posting_date', '日期')]
        sections.append({'doctype': doctype, 'label': label, 'columns': columns,
                         'access': frappe.has_permission(doctype, 'read'), 'rows': records(doctype)})
    signals = []
    for metric in metrics:
        if metric['value'] and metric['label'] in ['已逾期订单', '待交付订单', '预计缺料仓位（未分交期）', '待启动工单', '待收货订单']:
            signals.append(f"{metric['label']}：{metric['value']}{metric['unit']}，需本模块跟进。")
    if module in ('stock', 'buying'):
        rejected = count('Quality Inspection', {'docstatus': ['<', 2], 'status': 'Rejected'})
        if rejected:
            signals.append(f'质检不合格：{rejected}单，需核对可用库存及供方处理。')
    return sections, signals


def latest_reports():
    # 同一账号的模块分析自动成为总控可读汇报，保留源会话和时间。
    result = []
    for module in AGENT_DOCS:
        rows = frappe.get_list('Agent Turn', filters={'owner': frappe.session.user, 'agent_module': module, 'state': 'done'},
                              fields=['name', 'answer', 'modified'], order_by='modified desc', limit_page_length=1)
        if rows:
            result.append({'module': module, 'turn': rows[0].name, 'answer': rows[0].answer, 'at': str(rows[0].modified)})
    return result


@frappe.whitelist()
def dashboard(module='control', view='chat'):
    require_user()
    check_module(module, control=True)
    if view == 'data' and module != 'control':
        if module not in MODULE_DOCS:
            frappe.throw('请选择业务模块的数据页。')
        approval_types = {'buying': ['Purchase Order'], 'selling': ['Sales Order'],
                          'stock': ['Stock Entry'], 'manufacturing': ['Work Order']}
        return {'modules': [module_board(module)], 'stages': [], 'reports': [], 'at': str(now_datetime()),
                'model_ready': runtime.configured(), 'cases': [], 'decisions': [],
                'approvals': records('Operations Approval', {'source_type': ['in', approval_types[module]],
                                    'workflow_state': ['in', ['待审批', '已驳回']]}, 6)}
    return {'reports': latest_reports(), 'modules': [module_board(module) for module in MODULE_DOCS], 'at': str(now_datetime()),
            'stages': [stage_board(key) for key in STAGE_DOCS] if module == 'buying' or module in STAGE_DOCS else [],
            'model_ready': runtime.configured(), 'cases': records('Supply Case', limit=6),
            'approvals': records('Operations Approval', {'workflow_state': ['in', ['待审批', '已驳回']]}, 6),
            'decisions': records('Supply Decision', {'workflow_state': ['in', ['待审批', '已驳回']]}, 6)}


@frappe.whitelist()
def chat_status():
    require_user()
    return {'model_ready': runtime.configured()}


def install():
    from suke_ops.setup import field
    if not frappe.db.exists('DocType', 'Agent Turn'):
        frappe.get_doc({'doctype': 'DocType', 'name': 'Agent Turn', 'module': 'Buying', 'custom': 1,
                        'autoname': 'hash', 'fields': [
                            field('conversation', '对话标识'), field('request_key', '请求标识', unique=1),
                            field('agent_module', '负责模块'), field('question', '问题', 'Long Text'),
                            field('state', '状态'), field('answer', '回答', 'Long Text'),
                            field('result', '工具记录', 'Code', options='JSON')],
                        # 普通 REST 只能读取自己的对话，写入限于下面的受控 API。
                        'permissions': [{'role': 'Desk User', 'read': 1, 'if_owner': 1}]}).insert()
    frappe.db.commit()


def owned_turn(name):
    require_user()
    doc = frappe.get_doc('Agent Turn', name)
    if doc.owner != frappe.session.user:
        frappe.throw('只能访问自己的对话。', frappe.PermissionError)
    return doc


@frappe.whitelist(methods=['POST'])
def start(question, module='control', conversation=None, request_id=None, scenario_id=None, scenario_updated=False, previous_turn=None, human_note=None):
    require_user()
    check_module(module, control=True)
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        frappe.throw('请输入 1–2000 字的问题。')
    if not runtime.configured():
        frappe.throw('尚未配置模型服务；面板和手动功能仍可使用。')
    scenario = None
    if scenario_id is not None:
        from suke_ops.live_discussion import evidence
        if module != 'control':
            frappe.throw('场景讨论仅支持总控。')
        scenario = evidence(int(scenario_id), scenario_updated in (True, 1, '1', 'true'))
    if previous_turn:
        prior = owned_turn(previous_turn)
        if prior.state != 'done' or module != 'control':
            frappe.throw('请等待原讨论结束后补充信息。')
        scenario = json.loads(prior.result or '{}').get('verified_evidence')
        if not scenario or not scenario.get('synthetic'):
            frappe.throw('此入口仅用于合成场景复核。')
        if not isinstance(human_note, str) or not human_note.strip() or len(human_note) > 1000:
            frappe.throw('请输入1–1000字补充信息。')
        notes = scenario.setdefault('human_sources', [])
        if len(notes) >= 10:
            frappe.throw('本案例已达到10条补充上限，请整理后重新建案。')
        note = human_note.strip()
        notes.append({'id': 'H' + str(len(notes) + 1), 'label': '人员补充（待核实）',
                      'text': note, 'reported_by': frappe.session.user, 'reported_at': str(now_datetime()),
                      'source_type': '登录用户补充', 'source_path': prior.name,
                      'verification': '尚未核实，不代表供应商确认或审批通过',
                      'version': hashlib.sha256(note.encode()).hexdigest()[:12]})
        scenario['previous_turn'] = prior.name
        scenario['verified_plan'] = {}
        scenario['calculation'] = {'status': '已有人员补充；旧计算已暂停，需重新核实输入。'}
        scenario['evidence_precedence'] = '原证据为历史基线；人员补充尚未核实，不自动覆盖原约束，不直接执行。'
    conversation = str(uuid.UUID(conversation)) if conversation else str(uuid.uuid4())
    request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    key = hashlib.sha256((frappe.session.user + ':' + request_id).encode()).hexdigest()
    # 稳定的账号行锁让多标签页同时发送也只能创建一个进行中任务。
    frappe.db.sql('select name from tabUser where name=%s for update', frappe.session.user)
    existing = frappe.db.get_value('Agent Turn', {'request_key': key}, 'name')
    if existing:
        previous = owned_turn(existing)
        return {'name': existing, 'conversation': previous.conversation}
    # 每账号仅一项进行中的分析，防止连续点击耗尽模型及队列资源。
    active = frappe.get_all('Agent Turn', filters={'owner': frappe.session.user, 'state': ['in', ['queued', 'running']]}, fields=['name', 'creation'])
    for row in active:
        if (now_datetime() - row.creation).total_seconds() < 960:
            frappe.throw('上一条分析尚未完成，请稍候。')
        frappe.db.set_value('Agent Turn', row.name, {'state': 'failed', 'answer': '分析超时，请重新发送。'})
    doc = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': conversation, 'request_key': key,
                         'agent_module': module, 'question': question.strip(), 'state': 'queued',
                         'result': json.dumps({'scenario': scenario}, ensure_ascii=False) if scenario else None}).insert(ignore_permissions=True)
    frappe.enqueue('suke_ops.hub.run_turn', queue='long', timeout=900, enqueue_after_commit=True,
                   job_id='agent-' + doc.name, name=doc.name)
    return {'name': doc.name, 'conversation': conversation}


@frappe.whitelist()
def poll(name):
    doc = owned_turn(name)
    return {'name': doc.name, 'state': doc.state, 'answer': doc.answer,
            **(json.loads(doc.result or '{}') if doc.state in ('done', 'running', 'failed') else {})}


@frappe.whitelist()
def history(conversation, module=None):
    require_user()
    conversation = str(uuid.UUID(conversation))
    filters = {'owner': frappe.session.user, 'conversation': conversation}
    if module is not None:
        check_module(module, control=True)
        filters['agent_module'] = module
    rows = frappe.get_list('Agent Turn', filters=filters,
                           fields=['name', 'question', 'answer', 'agent_module', 'state', 'result'],
                           order_by='creation desc, name desc', limit_page_length=50)
    # 一次按权限读取完整历史，保留已建单据、失败留痕和合成场景边界。
    return [{**json.loads(row.pop('result') or '{}'), **row} for row in reversed(rows)]


def read_record(module, doctype, name):
    if doctype not in AGENT_DOCS[module] + SHARED_DOCS:
        frappe.throw('单据不属于该模块的工具范围。', frappe.PermissionError)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission('read')
    for key, value in stage_scope(module, doctype).items():
        if doc.get(key) != value:
            frappe.throw('单据不属于该采购环节。', frappe.PermissionError)
    result = {field: doc.get(field) for field in fields_for(doctype)}
    result['doctype'] = doctype
    result['_provenance'] = {'doctype': doctype, 'name': doc.name, 'created_by': doc.owner,
                             'created_at': str(doc.creation), 'modified_by': doc.modified_by,
                             'modified_at': str(doc.modified), 'read_at': str(now_datetime()),
                             'note': '创建/修改账号不等于业务信息承诺人；未提供者不推定。'}
    for table in ('items', 'required_items', 'suppliers'):
        if doc.meta.has_field(table) and not cint(doc.meta.get_field(table).permlevel):
            item_fields = ['item_code', 'qty', 'stock_qty', 'rate', 'uom', 'stock_uom', 'conversion_factor', 'amount', 'warehouse', 'schedule_date',
                           'supplier', 'quote_status', 'email_sent', 'request_for_quotation', 'material_request', 'purchase_order', 'quality_inspection',
                           'delivery_date', 'received_qty', 'delivered_qty', 'required_qty', 'consumed_qty', 'source_warehouse']
            result[table] = [{key: row.get(key) for key in item_fields if row.meta.has_field(key)
                              and not cint(row.meta.get_field(key).permlevel)} for row in doc.get(table)[:30]]
            result[table + '_total'] = len(doc.get(table))
    return result


def draft_offer(module, action, source):
    definition = DRAFTS.get(action)
    if not definition or module not in draft_modules(action):
        frappe.throw('该模块不能调用此操作。', frappe.PermissionError)
    _, source_type, target_type, _ = definition
    doc = frappe.get_doc(source_type, source)
    doc.check_permission('read')
    if not frappe.has_permission(target_type, 'create'):
        frappe.throw('没有创建目标单据的权限。', frappe.PermissionError)
    if doc.docstatus != 1 or doc.get('status') in ('Closed', 'Cancelled', 'Stopped', 'Completed'):
        frappe.throw('源单据不是可继续履约的已提交单据。')
    if source_type == 'Material Request' and doc.material_request_type != 'Purchase':
        frappe.throw('只有采购类型物料需求单能进入采购流程。')
    if source_type == 'Supplier Quotation' and doc.get('valid_till') and str(doc.valid_till) < nowdate():
        frappe.throw('供应商报价已过有效期，请先取得有效报价。')
    # 上游序列号/批次映射会提前插入 Bundle；这些场景保留原生手动流程，
    # 不把有落库副作用的映射包装成只读草稿。
    items = {row.item_code for table in ('items', 'required_items') for row in (doc.get(table) or [])}
    if doc.get('production_item'):
        items.add(doc.production_item)
    for item in items:
        tracking = frappe.get_cached_value('Item', item, ['has_serial_no', 'has_batch_no'])
        if tracking and any(tracking):
            frappe.throw('包含序列号或批次物料，请从原始单据使用手动创建功能，避免提前生成批次记录。')
    return {'action': action, 'source': source, 'source_type': source_type, 'target_type': target_type,
            'modified': str(doc.modified), 'label': '从 ' + source + ' 准备' + LABELS[target_type] + '草稿'}


def action_tool():
    return runtime.schema('report_action', '结束前报告产出；未提供草稿必须说明具体原因、缺项和下一步；此工具不创建业务单据',
                          {'status': runtime.text_schema(['analysis_only', 'blocked', 'draft_ready', 'document_created']),
                           'reason': runtime.text_schema(), 'next_step': runtime.text_schema()},
                          ['status', 'reason', 'next_step'])


def module_tools(module):
    specs = [runtime.schema('read_module_board', '读取当前环节或模块的进度、指标和最近12笔单据', {}),
            runtime.schema('list_records', '列出指定类型最近12笔单据；不是全部记录',
                           {'doctype': runtime.text_schema(AGENT_DOCS[module] + SHARED_DOCS)}, ['doctype']),
            runtime.schema('read_record', '读取指定单据摘要及最多30行物料',
                           {'doctype': runtime.text_schema(AGENT_DOCS[module] + SHARED_DOCS), 'name': runtime.text_schema()}, ['doctype', 'name']),
            runtime.schema('compare_supply_case', '调用程序计算现存、锁定、待确认供货及销售/生产两方案',
                           {'name': runtime.text_schema()}, ['name'])]
    if module == 'buying':
        specs.append(runtime.schema('read_procurement_progress', '读取采购六环节的进度和专项Agent最新汇报', {}))
    actions = [key for key in DRAFTS if module in draft_modules(key)]
    if actions:
        specs.append(runtime.schema('offer_draft', '仅建议打开原生未保存草稿；用户点击后重新校验源版本和权限，不会自动保存或提交',
                           {'action': runtime.text_schema(actions),
                            'source': runtime.text_schema()}, ['action', 'source']))
    if module in demand.MODULES:
        specs.extend([runtime.schema('read_request_context', '按物料编码或名称查可采购物料、库存单位、公司和仓库候选；创建需求单前核实',
                                     {'search': runtime.text_schema()}, ['search']), demand.CREATE_TOOL])
    return [*specs, action_tool()]


def run_turn(name):
    # 从服务器记录恢复真实调用人，绝不接收模型指定的用户身份。
    frappe.db.sql('select name from `tabAgent Turn` where name=%s for update', name)
    turn = frappe.get_doc('Agent Turn', name)
    if turn.state != 'queued':
        frappe.db.commit()
        return
    frappe.set_user(turn.owner)
    frappe.db.set_value('Agent Turn', name, 'state', 'running')
    frappe.db.commit()
    trace, offers, budget, tool_budget = [], [], [18], [48]
    module_results = []
    action_results = {}
    documents = []
    board_cache = {}
    run_started = time.monotonic()
    try:
        require_user()
        stored = json.loads(turn.result or '{}')
        if stored.get('scenario'):
            from suke_ops.live_discussion import discuss
            def publish_event(state):
                state['mode'] = '真实模型讨论 / 合成证据 / 不执行'
                frappe.db.set_value('Agent Turn', name, 'result', json.dumps(state, ensure_ascii=False))
                frappe.db.commit()
            result = discuss(stored['scenario'], turn.question, lambda rows: None, on_event=publish_event)
            frappe.db.set_value('Agent Turn', name, {'state': 'done', 'answer': result['answer'],
                                'result': json.dumps(result, ensure_ascii=False)})
            frappe.db.commit()
            return
        previous = frappe.get_all('Agent Turn', filters={'owner': turn.owner, 'conversation': turn.conversation,
                                 'state': 'done', 'agent_module': turn.agent_module, 'creation': ['<', turn.creation]}, fields=['question', 'answer', 'result'],
                                  order_by='creation desc', limit_page_length=4)
        messages = [message for row in reversed(previous) if not json.loads(row.result or '{}').get('synthetic') for message in
                    ({'role': 'user', 'content': row.question}, {'role': 'assistant', 'content': row.answer})]

        def execute_tool(module, tool, args):
            if tool == 'report_action':
                if not args['reason'].strip() or not args['next_step'].strip():
                    raise RuntimeError('行动结果缺少具体原因或下一步。')
                available = offers if module == 'control' else [offer for offer in offers if module in draft_modules(offer['action'])]
                if args['status'] == 'draft_ready' and not available:
                    raise RuntimeError('本轮没有实际草稿入口，不能报告草稿已准备。')
                if args['status'] == 'document_created' and not documents:
                    raise RuntimeError('本轮没有实际物料需求单，不能报告单据已创建。')
                action_results[module] = dict(args)
                return {**args, 'documents': documents, 'saved_documents': sum(not row['reused'] for row in documents),
                        'draft_count': len(available)}
            if module == 'buying' and tool == 'read_procurement_progress':
                return {'stages': [stage_board(key) for key in STAGE_DOCS],
                        'reports': [row for row in latest_reports() if row['module'] in STAGE_DOCS]}
            if module == 'control' and tool == 'read_module_reports':
                for key in MODULE_DOCS:
                    if key not in board_cache:
                        board_cache[key] = {**module_board(key), 'read_at': str(now_datetime())}
                return {'reports': latest_reports(), 'current_boards': [board_cache[key] for key in MODULE_DOCS],
                        'note': '历史汇报可能过时，业务事实须按当前面板核实。'}
            if module == 'control' and tool == 'ask_module':
                return run_module_batch([args])[0]
            if module not in AGENT_DOCS:
                frappe.throw('未知模块。', frappe.PermissionError)
            if tool == 'read_request_context' and module in demand.MODULES:
                return demand.context(**args)
            if tool == 'create_material_request' and module in demand.MODULES:
                frappe.db.savepoint('agent_material_request')
                try:
                    document = demand.create(module, turn, **args, current_documents=documents)
                except (RuntimeError, frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError) as error:
                    frappe.db.rollback(save_point='agent_material_request')
                    reason = '没有读取主数据或创建物料需求单的权限。' if isinstance(error, frappe.PermissionError) else str(error)
                    return {'status': 'blocked', 'reason': reason, 'next_step': '请补齐或修正所列信息后重试；本次未创建物料需求单。'}
                if not any(row['name'] == document['name'] for row in documents):
                    documents.append(document)
                return document
            if tool == 'read_module_board':
                if module not in board_cache:
                    board_cache[module] = {**module_board(module), 'read_at': str(now_datetime())}
                return board_cache[module]
            if tool == 'list_records':
                if args['doctype'] not in AGENT_DOCS[module] + SHARED_DOCS:
                    frappe.throw('单据越权。', frappe.PermissionError)
                return {'rows': records(args['doctype'], stage_scope(module, args['doctype'])), 'limit': 12}
            if tool == 'read_record':
                return read_record(module, **args)
            if tool == 'compare_supply_case':
                from suke_ops.api import get_analysis
                result = get_analysis(args['name'])
                result.pop('agent', None)
                return result
            if tool == 'offer_draft':
                offer = draft_offer(module, **args)
                if offer not in offers:
                    offers.append(offer)
                return {**offer, 'state': '待用户打开，尚未创建单据'}
            frappe.throw('未授权工具。', frappe.PermissionError)

        events = []
        def audit(module, kind, summary, **extra):
            events.append({'id': len(events)+1, 'module': module, 'kind': kind,
                           'phase': '业务查询', 'at': str(now_datetime()), 'summary': summary, **extra})
            frappe.db.set_value('Agent Turn', name, 'result', json.dumps({'events': events, 'trace': trace, 'offers': offers, 'documents': documents, 'actions': action_results}, ensure_ascii=False, default=str))
            frappe.db.commit()
        def dispatch(module, tool, args):
            labels = {'ask_module': '委派部门核查', 'read_module_reports': '读取部门汇报', 'read_module_board': '核查业务看板', 'list_records': '查询单据清单', 'read_record': '核对单据详情', 'compare_supply_case': '计算缺料方案', 'offer_draft': '准备草稿入口'}
            reused = tool == 'read_module_board' and module in board_cache
            audit(module, 'query', '复用本轮已读取看板（保留原读取时间）' if reused else labels.get(tool, tool), tool=tool, query=args)
            started = time.monotonic()
            try:
                value = execute_tool(module, tool, args)
            except Exception:
                audit(module, 'failed', '查询失败或无权限，未取得可用依据。', tool=tool)
                raise
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            audit(module, 'evidence', '已取得查询结果，展开查看返回记录', tool=tool,
                  query=args, result_excerpt=encoded[:12000], truncated=len(encoded)>12000,
                  duration_ms=round((time.monotonic()-started)*1000), reused=reused)
            return value

        def run_module_batch(requests):
            from suke_ops.parallel_work import run_parallel
            jobs, prepared = [], {}
            remaining = len({args['module'] for args in requests} - {row['module'] for row in module_results})
            for args in requests:
                target = args['module']
                previous_result = next((row for row in module_results if row['module'] == target), None)
                if previous_result:
                    prepared[target] = previous_result
                    continue
                if target in prepared:
                    continue
                prepared[target] = None
                if tool_budget[0] < 1:
                    prepared[target] = {'module': target, 'status': 'failed', 'answer': '本轮查询预算不足，尚未核实。'}
                    module_results.append(prepared[target])
                    remaining -= 1
                    continue
                tool_budget[0] -= 1  # Program pre-read is a real read, not a free model tool.
                # 调用线程分配子预算，需求最多5轮，总控预留2轮；完成后归还剩余额度。
                quota = min(5 if target in demand.MODULES else 4, max(0, budget[0]-2) // max(1, remaining))
                tools_quota = min(10, tool_budget[0] // max(1, remaining))
                remaining -= 1
                budget[0] -= quota
                tool_budget[0] -= tools_quota
                evidence = dispatch(target, 'read_module_board', {})
                jobs.append({'module': target, 'question': args['question'], 'evidence': evidence,
                             'budget': [quota], 'tool_budget': [tools_quota], 'specs': module_tools(target)})
                audit(target, 'started', '正在核查；独立部门同时进行。')

            def child(job, call):
                started, child_trace = time.monotonic(), []
                try:
                    answer = runtime.respond(job['module'], job['question'] + '\n向总控用最多3条短句汇报关键发现、依据和待确认事项，不重复背景。',
                        [], job['specs'], lambda *args: call('tool', *args), child_trace,
                        budget=job['budget'], tool_budget=job['tool_budget'], initial_evidence=job['evidence'])
                    result = {'module': job['module'], 'status': 'done', 'answer': answer}
                except Exception:
                    result = {'module': job['module'], 'status': 'failed', 'answer': '本部门未完成核查，请重试；不得按已确认处理。'}
                result['duration_ms'] = round((time.monotonic()-started)*1000)
                return call('complete', job, result, child_trace)

            def service(kind, *args):
                if kind == 'tool':
                    return dispatch(*args)
                job, result, child_trace = args
                budget[0] += job['budget'][0]
                tool_budget[0] += job['tool_budget'][0]
                trace.extend(child_trace)
                module_results.append(result)
                prepared[result['module']] = result
                from suke_ops.live_discussion import short_reply
                audit(result['module'], 'reply' if result['status'] == 'done' else 'failed', short_reply(result['answer']),
                      result_excerpt=json.dumps(result, ensure_ascii=False), duration_ms=result['duration_ms'])
                return result

            run_parallel(jobs, child, service, max_workers=4)
            return [prepared[args['module']] for args in requests]

        def dispatch_many(module, calls):
            # 只并行连续的部门委派；后续结果报告必须等前面的建单或草稿完成。
            values, index = [], 0
            while index < len(calls):
                tool, args = calls[index]
                if module != 'control' or tool != 'ask_module':
                    values.append(dispatch(module, tool, args))
                    index += 1
                    continue
                delegated = []
                while index < len(calls) and calls[index][0] == 'ask_module':
                    args = calls[index][1]
                    audit(module, 'query', '委派部门并行核查', tool='ask_module', query=args)
                    delegated.append(args)
                    index += 1
                for args, value in zip(delegated, run_module_batch(delegated)):
                    values.append(value)
                    audit(module, 'evidence', '已收到部门反馈；按原部门记录追查依据。', tool='ask_module', query=args,
                          result_excerpt=json.dumps(value, ensure_ascii=False), truncated=False)
            return values

        specs = [runtime.schema('ask_module', '委派任务给指定业务 Agent；独立部门请在同一批发起，以便并行核查',
                                {'module': runtime.text_schema(AGENT_DOCS), 'question': runtime.text_schema()}, ['module', 'question'])] \
            if turn.agent_module == 'control' else module_tools(turn.agent_module)
        if turn.agent_module == 'control':
            specs.append(runtime.schema('read_module_reports', '读取各模块已上报分析与最新风险面板；研判上报时先调用', {}))
            specs.append(action_tool())
        try:
            tool_budget[0] -= 1
            initial = dispatch(turn.agent_module, 'read_module_reports' if turn.agent_module == 'control' else 'read_module_board', {})
            answer = runtime.respond(turn.agent_module, turn.question, messages, specs, dispatch, trace,
                                     budget=budget, tool_budget=tool_budget, initial_evidence=initial,
                                     batch_dispatch=dispatch_many)
        except RuntimeError:
            if not documents and not any(row['status'] == 'done' for row in module_results):
                raise
            answer = ('物料需求单已登记，模型后续说明未完成。' if documents else '汇总未完成，已取得的模块结果如下：') + '\n\n' + '\n\n'.join(
                runtime.MODULES[row['module']] + '：' + (row['answer'] if row['status'] == 'done' else '本轮未核实，请切换该模块重试。')
                for row in module_results)
        failed_modules = sorted({runtime.MODULES[row['module']] for row in module_results if row['status'] == 'failed'})
        if failed_modules:
            answer += '\n\n未核实模块：' + '、'.join(failed_modules) + '。可切换对应模块重试。'
        action = action_results.get(turn.agent_module) or {
            'status': 'blocked', 'reason': '本轮汇总未完成，未取得完整行动结果。',
            'next_step': '请切换相关模块继续核实；已有草稿入口仍需打开核对。'}
        if documents:
            action = {'status': 'document_created', 'reason': '采购需求已登记；下方为真实物料需求单，重复需求复用原单。',
                      'next_step': documents[0]['next_step']}
        labels = {'analysis_only': '仅完成分析', 'blocked': '待处理阻碍', 'draft_ready': '已提供草稿入口',
                  'document_created': '已登记物料需求单'}
        draft_label = '待保存的下游草稿入口' if documents else '可打开草稿入口'
        answer += ('\n\n**本轮行动结果：' + labels[action['status']] + '**\n'
                   + f"\n已保存业务单据：{sum(not row['reused'] for row in documents)} 张；{draft_label}：{len(offers)} 个。\n"
                   + '\n原因：' + action['reason'] + '\n\n下一步：' + action['next_step'])
        if documents:
            answer += '\n\n物料需求单：' + '、'.join(row['name'] + ('（复用已有单据）' if row['reused'] else '（已保存，未提交）') for row in documents)
        frappe.db.set_value('Agent Turn', name, {'state': 'done', 'answer': answer,
                            'result': json.dumps({'trace': trace, 'offers': offers, 'modules': module_results, 'events': events, 'action': action, 'documents': documents,
                                'timing': {'total_ms': round((time.monotonic()-run_started)*1000),
                                           'model_calls': sum(row.get('tool') == 'model_request' for row in trace)}}, ensure_ascii=False, default=str)})
    except Exception as error:
        frappe.db.rollback()
        saved = json.loads(frappe.db.get_value('Agent Turn', name, 'result') or '{}')
        # 审计可能已提交业务写入；按持久化结果恢复，避免误报未保存和重试重复建单。
        saved_documents = [row for row in saved.get('documents', [])
                           if frappe.db.exists(row['doctype'], row['name'])]
        if saved_documents:
            saved['documents'] = saved_documents
            saved['action'] = {'status': 'document_created', 'reason': '需求单已保存，后续分析未完成。',
                               'next_step': saved_documents[0]['next_step']}
            answer = '物料需求单已登记，后续分析未完成。请打开原单核对，无需重复申请。\n' + '、'.join(row['name'] for row in saved_documents)
            state = 'done'
        else:
            answer = ('本轮分析未完成：' + str(error) if type(error) is RuntimeError else
                      '本轮分析未完成：请检查业务权限或问题范围后重试。') + ' 业务单据未由 Agent 保存或提交。'
            state = 'failed'
        frappe.db.set_value('Agent Turn', name, {'state': state, 'answer': answer,
                            'result': json.dumps({**saved, 'error_type': type(error).__name__, 'trace': trace}, ensure_ascii=False)})
    frappe.db.commit()


@frappe.whitelist(methods=['POST'])
def open_draft(name, index):
    turn = owned_turn(name)
    if turn.state != 'done':
        frappe.throw('分析尚未完成。')
    offers = json.loads(turn.result or '{}').get('offers', [])
    index = cint(index)
    if index < 0 or index >= len(offers):
        frappe.throw('草稿入口无效。')
    offer = offers[index]
    module, _, _, method = DRAFTS[offer['action']]
    current = draft_offer(module, offer['action'], offer['source'])
    if current['modified'] != offer['modified']:
        frappe.throw('源单据已变化，请让 Agent 重新读取后准备草稿。')
    if offer['action'] == 'finish_production':
        wo = frappe.get_doc('Work Order', offer['source'])
        qty = flt(wo.qty) - flt(wo.produced_qty)
        if qty <= 0:
            frappe.throw('没有剩余完工数量。')
        doc = frappe.get_attr(method)(wo.name, 'Manufacture', qty=qty)
    else:
        doc = frappe.get_attr(method)(offer['source'])
    if isinstance(doc, dict):
        doc = frappe.get_doc(doc)
    doc.check_permission('create')
    # 只返回原生映射数据；由用户在表单检查必填项并保存，不写入 ERP 单据。
    doc.name = None
    doc.set('__islocal', 1)
    return doc.as_dict()


@frappe.whitelist()
def discussion_activity(module='control'):
    """Only the caller's recent runs; department workspaces share the same audit trail."""
    require_user()
    check_module(module, control=True)
    rows = frappe.get_list('Agent Turn', filters={'owner': frappe.session.user},
                           fields=['name', 'question', 'state', 'result', 'modified'],
                           order_by='modified desc', limit_page_length=8)
    activity = []
    for row in rows:
        data = json.loads(row.result or '{}')
        events = [e for e in data.get('events', []) if module == 'control' or e.get('module') == module]
        if not events and row.state not in ('queued', 'running'):
            continue
        ids = {sid for event in events for sid in event.get('source_ids', [])}
        activity.append({'name': row.name, 'question': row.question, 'state': row.state,
                         'events': events, 'sources': [r for r in data.get('sources', []) if r['id'] in ids],
                         'discussion': data.get('discussion', []), 'synthetic': data.get('synthetic', False)})
    return activity
