"""搜索与业务面板共用的目录、中文名称和筛选定义。"""
from frappe.utils import nowdate

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
LABELS.update({'Supply Case': '供需异常案例', 'Supply Decision': '供应协同决策', 'Operations Approval': '业务单据审批'})
MODULE_NAMES = {'control': '主页', 'buying': '采购', 'selling': '销售', 'stock': '库存', 'manufacturing': '生产', 'shared': '协同审批'}
STAGE_NAMES = {'buying-demand': '物料需求单', 'buying-inquiry': '询价', 'buying-comparison': '比价',
               'buying-approval': '采购审批', 'buying-order': '采购下单', 'buying-receipt': '采购入库'}
ALIASES = {'Material Request': '需求单 物料需求 采购申请', 'Request for Quotation': '询价 RFQ',
           'Purchase Receipt': '入库 收货 到货', 'Stock Entry': '入库 出库 调拨',
           'Supply Case': '缺料 延期 风险 异常', 'Operations Approval': '审批 待办',
           'Production Plan': '排产 生产计划', 'Bin': '现存 库存 缺料 仓位'}


def metric_specs(module):
    if module in STAGE_DOCS:
        dt, _, progress = stage_definition(module)
        rows = [(label, dt, {**stage_scope(module, dt), **filters}, '单') for label, filters in progress]
        if module == 'buying-receipt':
            rows.append(('质检不合格', 'Quality Inspection', {'docstatus': ['<', 2], 'inspection_type': 'Incoming', 'status': 'Rejected'}, '单'))
    elif module in ('buying', 'selling'):
        purchase = module == 'buying'
        dt = 'Purchase Order' if purchase else 'Sales Order'
        progress, due = ('per_received', 'schedule_date') if purchase else ('per_delivered', 'delivery_date')
        pending = {'docstatus': 1, 'status': ['not in', ['Closed', 'Cancelled']], progress: ['<', 100]}
        rows = [('待收货订单' if purchase else '待交付订单', dt, pending, '单'),
                ('已逾期订单', dt, {**pending, due: ['<', nowdate()]}, '单'), ('订单草稿', dt, {'docstatus': 0}, '单')]
    elif module == 'manufacturing':
        pending = {'docstatus': 1, 'status': ['not in', ['Completed', 'Stopped', 'Closed', 'Cancelled']]}
        rows = [('未完成工单', 'Work Order', pending, '单'), ('生产中', 'Work Order', {'docstatus': 1, 'status': 'In Process'}, '单'),
                ('待启动工单', 'Work Order', {'docstatus': 1, 'status': 'Not Started'}, '单')]
    else:
        rows = [('有现存量的物料仓位', 'Bin', {'actual_qty': ['>', 0]}, '项'),
                ('有效预留记录', 'Stock Reservation Entry', {'docstatus': 1, 'status': ['not in', ['Closed', 'Cancelled', 'Delivered', 'Consumed']]}, '条'),
                ('预计缺料仓位（未分交期）', 'Bin', {'projected_qty': ['<', 0]}, '项')]
    return [dict(id=f'query:{module}:{i}', label=label, doctype=dt, filters=filters, unit=unit)
            for i, (label, dt, filters, unit) in enumerate(rows)]


def data_url(module):
    return '/desk/主页/operations-data' if module == 'control' else f'/desk/{module}/operations-data-{module}'


def entries():
    """目录只记录定义；记录数量和内容按请求实时查询。"""
    result = []
    for module, label in MODULE_NAMES.items():
        if module == 'shared':
            continue
        work = '/desk/主页/operations-hub' if module == 'control' else f'/desk/{module}/operations-{module}'
        for kind, title, route in [('work', '总控主页' if module == 'control' else label + '工作台', work),
                                   ('data', '数据总览' if module == 'control' else label + '数据', data_url(module))]:
            result.append(dict(id=f'page:{module}:{kind}', label=title, module=module, kind='page', route=route,
                               description='进入业务工作台' if kind == 'work' else '查看业务指标和最近单据'))
    for stage, label in STAGE_NAMES.items():
        result.append(dict(id='page:' + stage, label=label + '工作台', module='buying', kind='page',
                           access_types=STAGE_DOCS[stage], route='/desk/buying/operations-' + stage, description='采购专项入口'))
    types = list(dict.fromkeys(dt for items in MODULE_DOCS.values() for dt in items)) + SHARED_DOCS
    for dt in types:
        modules = [m for m, docs in MODULE_DOCS.items() if dt in docs] or ['shared']
        result.append(dict(id='type:' + dt, label=LABELS[dt], doctype=dt, module=modules[0], modules=modules,
                           kind='type', filters={}, description='查询完整授权记录', aliases=ALIASES.get(dt, '')))
    for module in [*MODULE_DOCS, *STAGE_DOCS]:
        group = 'buying' if module in STAGE_DOCS else module
        for spec in metric_specs(module):
            result.append({**spec, 'module': group, 'kind': 'query',
                           'description': (STAGE_NAMES.get(module) or MODULE_NAMES[group]) + ' · 与面板相同条件',
                           'aliases': '缺料 风险' if module == 'stock' and spec['doctype'] == 'Bin' else ''})
        dt = STAGE_DOCS[module][0] if module in STAGE_DOCS else metric_specs(module)[0]['doctype']
        filters = {**stage_scope(module, dt), 'docstatus': ['<', 2]} if module in STAGE_DOCS else metric_specs(module)[0]['filters']
        if module == 'stock':
            filters = {'actual_qty': ['!=', 0]}
        result.append(dict(id=f'query:{module}:rows', label=(STAGE_NAMES.get(module) or MODULE_NAMES[group]) + '面板全部记录',
                           doctype=dt, filters=filters, module=group, kind='query', description='与面板主表相同条件'))
    result.extend([
        dict(id='query:buying-receipt:quality', label='采购入库质检记录', doctype='Quality Inspection', module='buying', kind='query',
             filters={'inspection_type': 'Incoming', 'docstatus': ['<', 2]}, description='与采购入库质检明细相同条件'),
        dict(id='query:buying-inquiry:quotations', label='已收到的供应商报价', doctype='Supplier Quotation', module='buying', kind='query',
             filters={'docstatus': ['<', 2]}, description='与询价工作台报价明细相同条件')])
    return result

def stage_scope(module, doctype):
    if module in STAGE_DOCS:
        if doctype == 'Material Request':
            return {'material_request_type': 'Purchase'}
        if doctype == 'Operations Approval':
            return {'source_type': 'Purchase Order'}
    return {}


def stage_definition(module):
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
    return definitions[module]
