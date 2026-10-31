"""可重复安装：新增协同单据、原生审批工作流和站点导航定制。"""
import json
from pathlib import Path
import frappe


def field(name, label, kind='Data', **kwargs):
    if 'default' in kwargs:
        kwargs['default'] = str(kwargs['default'])
    return dict(fieldname=name, label=label, fieldtype=kind, **kwargs)


def install():
    frappe.set_user('Administrator')
    backup = Path(frappe.get_site_path('private', 'files', 'suke-navigation-before.json'))
    if not backup.exists():
        data = {dt: [frappe.get_doc(dt, n).as_dict() for n in frappe.get_all(dt, pluck='name')] for dt in ['Dock', 'Custom Sidebar', 'Workflow']}
        backup.write_text(frappe.as_json(data), encoding='utf-8')
    for role in ['Supply Planner', 'Supply Approver']:
        if not frappe.db.exists('Role', role):
            frappe.get_doc({'doctype': 'Role', 'role_name': role, 'desk_access': 1}).insert()
    definitions = {
        'Supply Case': [
            field('title', '异常主题', reqd=1, in_list_view=1),
            field('is_simulation', '合成演示数据', 'Check', default=1, read_only=1),
            field('company', '公司', 'Link', options='Company', reqd=1),
            field('item_code', '物料', 'Link', options='Item', reqd=1, in_list_view=1),
            field('warehouse', '仓库', 'Link', options='Warehouse', reqd=1),
            field('purchase_order', '关联采购订单', 'Link', options='Purchase Order', reqd=0),
            field('priority_sales_order', '重点客户订单', 'Link', options='Sales Order'),
            field('event_time', '事件发生时间', 'Datetime', reqd=1),
            field('event_message', '供应商通知', 'Small Text'),
            field('possible_early_qty', '待确认先发数量', 'Float', default=0),
            field('early_arrival', '提前批次候选到仓时间', reqd=0),
            field('minimum_batch', '生产最小开工件数', 'Int', default=50),
            field('blocked_qty', '冻结／待检数量', 'Float', default=0),
            field('dashboard_section', '供需与决策', 'Section Break'),
            field('dashboard', '实时方案', 'HTML'),
            field('audit_section', '技术审计记录', 'Section Break', collapsible=1),
            field('supplier_result', '当前模拟供货承诺', 'Code', options='JSON', read_only=1),
            field('negotiation_history', '模拟议价记录', 'Code', options='JSON', read_only=1),
            field('agent_result', 'Agent分析及工具调用', 'Code', options='JSON', read_only=1),
        ],
        'Supply Decision': [
            field('supply_case', '关联异常', 'Link', options='Supply Case', reqd=1, in_list_view=1),
            field('selected_plan', '选择方案', 'Select', options='sales_first\nproduction_first', reqd=1, in_list_view=1, read_only=1),
            field('reason', '选择／驳回／修正依据', 'Small Text', reqd=1),
            field('workflow_state', '审批状态', 'Link', options='Workflow State', read_only=1, in_list_view=1),
            field('snapshot_hash', '依据版本', read_only=1),
            field('request_key', '防重标识', unique=1, read_only=1),
            field('plan_json', '批准范围与分配明细', 'Code', options='JSON', read_only=1),
            field('execution_result', '执行结果', 'Code', options='JSON', read_only=1),
        ]}
    case_fields = definitions['Supply Case']
    definitions['Supply Case'] = case_fields[:2] + case_fields[13:15] + [field('context_section', '业务背景与参数', 'Section Break', collapsible=1)] + case_fields[2:13] + case_fields[15:]
    for name, fields in definitions.items():
        if not frappe.db.exists('DocType', name):
            frappe.get_doc({'doctype': 'DocType', 'name': name, 'module': 'Buying', 'custom': 1,
                            'autoname': 'format:SUP-' + ('CASE' if name == 'Supply Case' else 'DEC') + '-{#####}',
                            'track_changes': 1, 'fields': fields,
                            'permissions': [{'role': r, 'read': 1, 'write': 1, 'create': 1, 'report': 1} for r in ['System Manager', 'Supply Planner', 'Supply Approver']]}).insert()
        elif name == 'Supply Case':
            meta = frappe.get_doc('DocType', name)
            existing_fields = {f.fieldname: f for f in meta.fields}
            ordered = []
            for definition in fields:
                row = existing_fields.get(definition['fieldname'])
                if row:
                    row.update(definition)
                else:
                    row = meta.append('fields', definition)
                ordered.append(row)
            meta.set('fields', ordered)
            meta.save()
    for state in ['草稿', '待审批', '已驳回', '已批准', '已执行']:
        if not frappe.db.exists('Workflow State', state):
            frappe.get_doc({'doctype': 'Workflow State', 'workflow_state_name': state, 'style': 'Success' if state == '已执行' else 'Warning' if state == '待审批' else 'Primary'}).insert()
    transitions = [('草稿', '提交审批', '待审批', 'Supply Planner'), ('待审批', '批准', '已批准', 'Supply Approver'),
                   ('待审批', '驳回', '已驳回', 'Supply Approver'), ('已驳回', '修正重提', '待审批', 'Supply Planner'),
                   ('已批准', '执行', '已执行', 'Supply Approver')]
    for _, action, _, _ in transitions:
        if not frappe.db.exists('Workflow Action Master', action):
            frappe.get_doc({'doctype': 'Workflow Action Master', 'workflow_action_name': action}).insert()
    if not frappe.db.exists('Workflow', '供应协同方案审批'):
        frappe.get_doc({'doctype': 'Workflow', 'workflow_name': '供应协同方案审批', 'document_type': 'Supply Decision',
                        'is_active': 1, 'workflow_state_field': 'workflow_state', 'send_email_alert': 0,
                        'states': [{'state': s, 'doc_status': '0', 'allow_edit': 'Supply Approver' if s in ('待审批', '已批准', '已执行') else 'Supply Planner'} for s in ['草稿', '待审批', '已驳回', '已批准', '已执行']],
                        'transitions': [{'state': a, 'action': b, 'next_state': c, 'allowed': d, 'allow_self_approval': 1} for a, b, c, d in transitions]}).insert()
    # 管理员可用于演示；实际角色仍由原生 Workflow 和 API 双重检查。
    user = frappe.get_doc('User', 'Administrator')
    existing = {r.role for r in user.roles}
    for role in ['Supply Planner', 'Supply Approver']:
        if role not in existing:
            user.append('roles', {'role': role})
    user.save()
    from suke_ops.approval import install as install_approval
    install_approval()
    from suke_ops.hub import install as install_hub
    install_hub()
    frappe.reload_doc('buying', 'page', 'operations_hub')
    customize_navigation()
    repair_existing_reservations()
    frappe.clear_cache()
    frappe.db.commit()
    return {'installed': True, 'navigation_backup': str(backup)}


def repair_existing_reservations():
    # 仅补齐本扩展已执行方案所创建的工单预留标志，保留原库存和单据。
    for decision in frappe.get_all('Supply Decision', filters={'workflow_state': '已执行'}, fields=['name', 'execution_result']):
        result = json.loads(decision.execution_result or '{}')
        for name in result.get('reservations', []):
            sre = frappe.get_doc('Stock Reservation Entry', name)
            if sre.voucher_type == 'Work Order' and sre.docstatus == 1:
                wo = frappe.get_doc('Work Order', sre.voucher_no)
                if not wo.reserve_stock:
                    wo.db_set('reserve_stock', 1)
                    frappe.clear_document_cache('Work Order', wo.name)
                    wo.add_comment('Comment', '按协同方案 ' + decision.name + ' 补齐原生库存预留标志。')


def customize_navigation():
    from frappe.desk.doctype.dock.dock import resolve_app_dock, save_site_dock
    from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_site_sidebar_layer, save_site_sidebar
    keep = ['Buying', 'Selling', 'Stock', 'Manufacturing']
    rows = resolve_app_dock('erpnext', upto='site', gated=False)
    for row in rows:
        row['hidden'] = int(row.get('link_to') not in keep)
    rows.sort(key=lambda r: keep.index(r.get('link_to')) if r.get('link_to') in keep else 99)
    save_site_dock('erpnext', rows)
    # 已有个人排列也按用户明确要求隐藏其它业务入口；原数据已备份。
    for layer in frappe.get_all('Dock', filters={'app': 'erpnext', 'standard': 0, 'user': ['!=', '']}, fields=['name']):
        doc = frappe.get_doc('Dock', layer.name)
        for row in doc.items:
            row.hidden = int(row.link_to not in keep)
        doc.save()
    menus = {
        'Buying': [('Supply Case', '异常决策 Agent'), ('Supply Decision', '跨模块审批'), ('Material Request', '采购申请'), ('Request for Quotation', '询价'),
                   ('Supplier Quotation', '供应商报价'), ('Purchase Order', '采购订单'), ('Purchase Receipt', '收货与退货'), ('Quality Inspection', '到货质检'),
                   ('Purchase Invoice', '采购账单'), ('Payment Entry', '收付款记录'), ('Supplier', '供应商'), ('Item', '物料'), ('Contact', '联系人'), ('Address', '地址')],
        'Selling': [('Supply Case', '交付风险与方案'), ('Supply Decision', '跨模块审批'), ('Customer', '客户'), ('Quotation', '销售报价'),
                    ('Sales Order', '销售订单'), ('Delivery Note', '发货与退货'), ('Sales Invoice', '销售账单'), ('Payment Entry', '收付款记录')],
        'Stock': [('Supply Case', '缺料与分配方案'), ('Supply Decision', '跨模块审批'), ('Item', '物料'), ('Warehouse', '仓库'), ('Stock Reservation Entry', '库存预留与释放'),
                  ('Stock Entry', '出入库与调拨'), ('Stock Reconciliation', '盘点'), ('Batch', '批次'), ('Quality Inspection', '质量检验')],
        'Manufacturing': [('Supply Case', '缺料影响与方案'), ('Supply Decision', '跨模块审批'), ('BOM', '物料清单'), ('Production Plan', '生产计划'),
                          ('Work Order', '生产工单'), ('Stock Entry', '领退料与完工入库'), ('Stock Reservation Entry', '生产库存预留'), ('Job Card', '工序执行')],
    }
    reports = {'Buying': ['Supplier Quotation Comparison', 'Procurement Tracker'], 'Selling': ['Sales Order Analysis'],
               'Stock': ['Stock Balance', 'Stock Ledger', 'Stock Projected Qty', 'Item Shortage Report'], 'Manufacturing': ['Production Planning Report']}
    for module, links in menus.items():
        links.insert(2, ('Operations Approval', '业务单据审批'))
        base = get_site_sidebar_layer(module)
        desired = [{'type': 'Link', 'label': '对话与数据面板', 'link_type': 'Page', 'link_to': 'operations-hub',
                    'added': 1, 'hidden': 0, 'open_in_new_tab': 0, 'icon': 'message-circle'}]
        for target, label in links:
            matching = next((r for r in base if r.get('link_type') == 'DocType' and r.get('link_to') == target), None)
            desired.append({**matching, 'label': label, 'hidden': 0, 'child': 0, 'indent': 0, 'open_in_new_tab': 0} if matching else
                           {'type': 'Link', 'label': label, 'link_type': 'DocType', 'link_to': target, 'added': 1, 'hidden': 0, 'open_in_new_tab': 0})
        for target in reports.get(module, []):
            if not frappe.db.exists('Report', target):
                continue
            matching = next((r for r in base if r.get('link_type') == 'Report' and r.get('link_to') == target), None)
            desired.append({**matching, 'hidden': 0, 'child': 0, 'indent': 0} if matching else
                           {'type': 'Link', 'label': target, 'link_type': 'Report', 'link_to': target, 'added': 1, 'hidden': 0})
        ids = {(r.get('link_type'), r.get('link_to')) for r in desired}
        desired += [{**r, 'hidden': 1} for r in base if (r.get('link_type'), r.get('link_to')) not in ids]
        save_site_sidebar(module, items=desired)
