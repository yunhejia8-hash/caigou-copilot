"""Registered local practice data using native ERP documents and approvals.

No public seed endpoint, external communication or model call. The caller owns
the transaction; install creates metadata and may perform framework DDL.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
import re

import frappe
from frappe.utils import getdate, nowdate, now_datetime

SITE = 'focus.local'
DOCTYPE = 'Supply Practice Dataset'
VERSION = 1


def _tag(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{0,23}', value):
        frappe.throw('虚拟数据批次只能使用大写字母、数字和短横线，最多24字符')
    return value


def _admin():
    if frappe.local.site != SITE or frappe.session.user != 'Administrator':
        frappe.throw('仅本地 focus.local 管理员可安装或初始化虚拟实操数据', frappe.PermissionError)


def install():
    """Install only this registry. Never run setup/seed or change global settings."""
    if frappe.local.site != SITE:
        return {'installed': False, 'skipped': True, 'reason': 'local_practice_only'}
    _admin()
    definitions = [
        {'fieldname': 'dataset_key', 'label': '虚拟批次编号', 'fieldtype': 'Data', 'unique': 1, 'reqd': 1},
        {'fieldname': 'tag', 'label': '批次标签', 'fieldtype': 'Data', 'reqd': 1},
        {'fieldname': 'active', 'label': '允许本地虚拟实操', 'fieldtype': 'Check', 'default': '1'},
        {'fieldname': 'company', 'label': '隔离虚拟公司', 'fieldtype': 'Link', 'options': 'Company', 'reqd': 1},
        {'fieldname': 'supply_case', 'label': '注册异常', 'fieldtype': 'Link', 'options': 'Supply Case',
         'unique': 1, 'reqd': 1},
        {'fieldname': 'manifest_json', 'label': '服务器注册来源清单', 'fieldtype': 'Code', 'options': 'JSON', 'reqd': 1},
    ]
    for field in definitions:
        field['read_only'] = 1
    if not frappe.db.exists('DocType', DOCTYPE):
        frappe.get_doc({'doctype': 'DocType', 'name': DOCTYPE, 'module': 'Buying', 'custom': 1,
                        'autoname': 'field:dataset_key', 'track_changes': 1, 'fields': definitions,
                        'permissions': [{'role': role, 'read': 1} for role in
                                        ('System Manager', 'Supply Planner', 'Supply Approver')]}).insert()
    else:
        doc = frappe.get_doc('DocType', DOCTYPE)
        existing = {row.fieldname: row for row in doc.fields}
        changed = False
        for field in definitions:
            if field['fieldname'] not in existing:
                doc.append('fields', field)
                changed = True
        if changed:
            doc.save()
    return {'installed': True, 'doctype': DOCTYPE, 'seeded': False}


def _read(doctype, name):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission('read')
    return doc


def _outbound_guard():
    """Do not trigger enabled site notifications/webhooks from practice writes."""
    affected = ['Company', 'Warehouse', 'Item', 'Customer', 'Supplier', 'Stock Entry',
                'Sales Order', 'BOM', 'Work Order', 'Stock Reservation Entry', 'Material Request',
                'Purchase Order', 'Operations Approval', 'Supply Case', DOCTYPE]
    for doctype, link in [('Notification', 'document_type'), ('Webhook', 'webhook_doctype')]:
        if frappe.db.exists('DocType', doctype):
            meta = frappe.get_meta(doctype)
            if not meta.has_field('enabled') or not meta.has_field(link):
                frappe.throw('无法验证站点外发规则，暂不能初始化虚拟实操')
            if frappe.db.exists(doctype, {'enabled': 1, link: ['in', affected]}):
                frappe.throw('站点存在适用于本次单据的通知或Webhook，请先由管理员确认外发边界；未创建虚拟数据')


def context(case):
    """Only a server registry can grant local practice scope; labels cannot."""
    if frappe.local.site != SITE or not frappe.db.exists('DocType', DOCTYPE):
        return None
    name = frappe.db.get_value(DOCTYPE, {'supply_case': case.name, 'active': 1}, 'name')
    if not name:
        return None
    case.check_permission('read')
    return _registered_context(case, _read(DOCTYPE, name))


def _registered_context(case, record):
    """Validate an already-read registry, including a locking/current read."""
    if record.active != 1:
        return None
    try:
        manifest = json.loads(record.manifest_json)
        tag = _tag(record.tag)
        expected_key = 'SUPPLY-PRACTICE-' + tag
        valid = (isinstance(manifest, dict) and record.dataset_key == expected_key
                 and record.active == 1 and manifest['dataset_id'] == expected_key
                 and manifest['tag'] == tag and manifest['site'] == SITE and manifest['version'] == VERSION
                 and manifest['company'] == record.company == case.company
                 and manifest['case_name'] == record.supply_case == case.name
                 and manifest['raw_item'] == case.item_code
                 and manifest['stock_warehouse'] == case.warehouse and case.is_simulation == 1
                 and case.purchase_order in {row['name'] for row in manifest['purchase_orders']}
                 and case.priority_sales_order in {row['name'] for row in manifest['sales_orders']}
                 and set(manifest['item_codes']) and set(manifest['warehouses'])
                 and manifest['raw_item'] in manifest['item_codes']
                 and manifest['stock_warehouse'] in manifest['warehouses']
                 and manifest['quality_warehouse'] in manifest['warehouses']
                 and manifest['quality_warehouse'] != manifest['stock_warehouse']
                 and isinstance(manifest['label'], str) and isinstance(manifest['source_lines'], list))
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        frappe.throw('虚拟实操登记与当前异常不一致，已停止审批和执行')
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return {'dataset_id': record.dataset_key, 'registered': True, 'label': manifest['label'],
            'site': SITE, 'version': str(VERSION) + '-' + revision, 'company': record.company,
            'item_codes': list(manifest['item_codes']), 'warehouses': list(manifest['warehouses']),
            'case_name': case.name, 'manifest': deepcopy(manifest), 'scope': 'isolated_erp'}


def current_case(tag='USER'):
    """Read the newest USER/USER-* batch, or one exact non-USER batch."""
    tag = _tag(tag)
    if frappe.local.site != SITE or not frappe.db.exists('DocType', DOCTYPE):
        return None
    filters = {'active': 1}
    options = {'or_filters': [{'tag': 'USER'}, {'tag': ['like', 'USER-%']}]} if tag == 'USER' else {}
    if tag != 'USER':
        filters['tag'] = tag
    rows = frappe.get_list(DOCTYPE, filters=filters, fields=['supply_case'],
                           order_by='creation desc', limit_page_length=1, **options)
    if not rows:
        return None
    case = _read('Supply Case', rows[0].supply_case)
    return case.name if context(case) else None


def lock_case(case):
    """Hold registry identity through a caller-owned business transaction."""
    if frappe.local.site != SITE or not frappe.db.exists('DocType', DOCTYPE):
        return None
    case.check_permission('read')
    # Include inactive rows: a concurrent revocation cannot disappear from this
    # lock and leave a stale active manifest authorizing a later business write.
    rows = frappe.db.sql('select * from `tabSupply Practice Dataset` where supply_case=%s for update',
                         case.name, as_dict=True)
    if not rows:
        return None
    if len(rows) != 1:
        frappe.throw('虚拟实操登记不唯一，不能继续写入')
    # Validate this current read directly. A later ordinary SELECT can otherwise
    # return an older active/manifest snapshot under repeatable-read isolation.
    record = frappe.get_doc({**rows[0], 'doctype': DOCTYPE})
    record.check_permission('read')
    return _registered_context(case, record)


def _source_map(manifest):
    sources = {}
    for row in manifest['source_lines']:
        key = (row['doctype'], row['document'], row['line'])
        if key in sources or row['doctype'] not in ('Sales Order', 'Work Order'):
            frappe.throw('虚拟实操来源清单无效')
        sources[key] = row
    return sources


def _check_source(row, registered, info):
    if row is None or row.item_code != registered['item_code']:
        frappe.throw('虚拟实操来源行已改变或不属于注册物料')
    warehouse = row.get('source_warehouse') if registered['doctype'] == 'Work Order' else row.get('warehouse')
    if warehouse != registered['warehouse'] or warehouse not in info['warehouses']:
        frappe.throw('虚拟实操来源行不属于注册仓库')


def validate_snapshot(case, ctx):
    info = context(case)
    if info is None:
        return None
    if ctx.get('item_code') != case.item_code or ctx.get('warehouse') != case.warehouse:
        frappe.throw('当前快照超出虚拟实操物料或仓库')
    sources = _source_map(info['manifest'])
    parents = {}

    def source(doctype, name, line):
        registered = sources.get((doctype, name, line))
        if not registered or registered['item_code'] != case.item_code or registered['warehouse'] != case.warehouse:
            frappe.throw('当前需求或预留包含未注册单据，已停止虚拟实操')
        key = (doctype, name)
        if key not in parents:
            parents[key] = _read(doctype, name)
        parent = parents[key]
        if parent.company != info['company']:
            frappe.throw('当前需求或预留不属于注册虚拟公司')
        table = parent.required_items if doctype == 'Work Order' else parent.items
        expected = {item['line']: item for item in sources.values()
                    if item['doctype'] == doctype and item['document'] == name}
        if {item.name for item in table} != set(expected):
            frappe.throw('虚拟来源单据包含新增或缺失的未注册行')
        for item in table:
            _check_source(item, expected[item.name], info)
        if doctype == 'Work Order':
            original = next(item for item in info['manifest']['work_orders'] if item['name'] == name)
            if (parent.sales_order != original['sales_order']
                    or parent.sales_order_item != original['sales_order_item']):
                frappe.throw('工单已指向未注册销售来源')
        _check_source(next((item for item in table if item.name == line), None), registered, info)

    for demand in ctx.get('demands', []):
        source(demand.get('document_type'), demand.get('document'), demand.get('id'))
    for row in ctx.get('reservations', []):
        reservation = _read('Stock Reservation Entry', row['id'])
        if (reservation.company != info['company'] or reservation.item_code != case.item_code
                or reservation.warehouse != case.warehouse or reservation.voucher_no != row.get('document')
                or reservation.voucher_detail_no != row.get('demand_id')
                or (row.get('document_type') and row['document_type'] != reservation.voucher_type)):
            frappe.throw('库存预留超出注册虚拟范围')
        source(reservation.voucher_type, reservation.voucher_no, reservation.voucher_detail_no)
    return info


def check_order(case, po):
    info = context(case)
    if info is None:
        frappe.throw('该虚拟实操登记已失效，不能继续提交采购单')
    po.check_permission('read')
    record = next((row for row in info['manifest']['purchase_orders'] if row['name'] == po.name), None)
    if (not record or po.company != info['company'] or po.name != case.purchase_order
            or po.supplier != info['manifest']['parties']['supplier']):
        frappe.throw('采购单未注册到本批虚拟实操')
    expected = {row['line']: row for row in record['items']}
    if {row.name for row in po.items} != set(expected) or not expected:
        frappe.throw('采购单包含新增或缺失的未注册行')
    for row in po.items:
        original = expected[row.name]
        if (row.item_code != original['item_code'] or row.warehouse != original['warehouse']
                or row.item_code not in info['item_codes'] or row.warehouse not in info['warehouses']
                or row.get('material_request') != original.get('material_request')
                or row.get('material_request_item') != original.get('material_request_item')):
            frappe.throw('采购行已超出注册虚拟物料、仓库或需求来源')
    return info


def _number(value):
    number = float(value or 0)
    if not math.isfinite(number) or number < 0:
        frappe.throw('虚拟库存或需求数量无效，请先核对单据')
    return number


def summary(case):
    """Only registered local records; live quantities never reset to seed values."""
    info = context(case)
    if info is None:
        return None
    from suke_ops.api import snapshot
    ctx = snapshot(case, scope='on_hand_only')
    validate_snapshot(case, ctx)
    manifest = info['manifest']
    quality = _read('Warehouse', manifest['quality_warehouse'])
    if quality.company != info['company']:
        frappe.throw('待检仓不属于本批虚拟公司')
    from suke_ops.api import _scope_read
    _scope_read(quality)
    if not frappe.has_permission('Bin', 'read'):
        frappe.throw('没有读取待检隔离仓库存的权限', frappe.PermissionError)
    held = 0
    for row in frappe.get_all('Bin', filters={'item_code': case.item_code, 'warehouse': quality.name}, fields=['name']):
        bin_doc = _read('Bin', row.name)
        _scope_read(bin_doc)
        held += _number(bin_doc.actual_qty)
    on_hand = _number(ctx['actual_qty'])
    locked = sum(_number(row['qty']) for row in ctx['reservations'])
    blocked = _number(ctx.get('blocked_qty', 0))
    free = max(0, on_hand - locked - blocked)
    risky = {row['name'] for row in manifest['work_orders']}
    demand = sum(_number(row['qty']) for row in ctx['demands'] if row['document'] in risky)
    unit = ctx['stock_uom']
    values = [('合格仓现存', on_hand), ('合格仓已预留', locked), ('当前自由量', free),
              ('待检隔离仓', held), ('合计在库', on_hand + held), ('A/B未领用需求', demand)]
    sources = [{'doctype': 'Supply Case', 'name': case.name, 'label': '本批虚拟异常'},
               {'doctype': 'Warehouse', 'name': quality.name, 'label': '待检隔离仓（未放行）'}]
    for key, doctype, label in [('purchase_orders', 'Purchase Order', '原采购订单'),
                               ('sales_orders', 'Sales Order', '销售来源'),
                               ('work_orders', 'Work Order', '关联生产工单'),
                               ('stock_entries', 'Stock Entry', '初始虚拟入库'),
                               ('initial_reservations', 'Stock Reservation Entry', '原有受保护预留')]:
        sources.extend({'doctype': doctype, 'name': row['name'], 'label': row.get('label') or label}
                       for row in manifest[key])
    sources.append({'doctype': 'Operations Approval', 'name': manifest['purchase_orders'][0]['approval'],
                    'label': '原600件采购审批'})
    return {key: info[key] for key in ('dataset_id', 'registered', 'label', 'site', 'version', 'scope')} | {
        'metrics': [{'label': label, 'value': value, 'unit': unit} for label, value in values],
        'sources': sources, 'as_of': str(now_datetime()),
        'note': '全部为本地注册虚拟数据；待检量取独立隔离仓台账，不代表品质放行。待确认200不计现存；未联系外部人员。'}


def seed(tag='USER'):
    """CLI-only native ERP seed, no commit; repeat returns the registered batch."""
    _admin()
    tag = _tag(tag)
    if not frappe.db.exists('DocType', DOCTYPE):
        frappe.throw('请先单独运行 supply_practice.install 安装登记表')
    key = 'SUPPLY-PRACTICE-' + tag
    existing = frappe.db.get_value(DOCTYPE, {'dataset_key': key}, 'supply_case')
    if existing:
        registered = context(_read('Supply Case', existing))
        if registered is None:
            frappe.throw('该批次已停用；不会覆盖原数据，请使用新的批次标签')
        return registered
    if not frappe.db.get_single_value('Stock Settings', 'enable_stock_reservation'):
        frappe.throw('当前未启用原生库存预留；请由管理员确认设置，本初始化不会修改全站设置')
    for name in ('Supply Case', 'Supply Decision', 'Operations Approval'):
        if not frappe.db.exists('DocType', name):
            frappe.throw('采购协同基础单据尚未安装：' + name)
    _outbound_guard()
    company_name = '采购协同虚拟实操-' + tag
    prefix = 'SUKE-PRACTICE-' + tag
    raw, fg_a, fg_b = prefix + '-CONN', prefix + '-FG-A', prefix + '-FG-B'
    parties = {'A': prefix + '-客户A', 'B': prefix + '-客户B', 'C': prefix + '-客户C',
               'supplier': prefix + '-供应商'}
    for doctype, name in [('Company', company_name), *[('Item', item) for item in (raw, fg_a, fg_b)],
                          *[('Customer', name) for role, name in parties.items() if role != 'supplier'],
                          ('Supplier', parties['supplier'])]:
        if frappe.db.exists(doctype, name):
            frappe.throw('该批次存在未登记的同名数据，不能复用或覆盖；请检查初始化记录或使用新批次')
    today = getdate(nowdate())
    tomorrow, late, c_due = today + timedelta(days=1), today + timedelta(days=4), today + timedelta(days=5)
    savepoint = 'supply_practice_seed'
    frappe.db.savepoint(savepoint)
    try:
        company = frappe.get_doc({'doctype': 'Company', 'company_name': company_name,
            'abbr': 'SP' + hashlib.sha256(tag.encode()).hexdigest()[:6].upper(),
            'default_currency': 'CNY', 'country': 'China', 'enable_perpetual_inventory': 1,
            'create_chart_of_accounts_based_on': 'Standard Template', 'chart_of_accounts': 'Standard'}).insert()
        parent = frappe.db.get_value('Warehouse', {'company': company.name, 'is_group': 1}, 'name')
        warehouses = {}
        for role, label in [('stock', '合格物料'), ('quality', '待检隔离'), ('wip', '生产在制'), ('finished', '成品')]:
            warehouses[role] = frappe.get_doc({'doctype': 'Warehouse', 'warehouse_name': prefix + '-' + label,
                'company': company.name, 'parent_warehouse': parent, 'is_group': 0}).insert().name
        group = frappe.db.get_value('Item Group', {'is_group': 0}, 'name')
        for code, label in [(raw, '连接器'), (fg_a, 'A客户控制组件'), (fg_b, 'B客户控制组件')]:
            frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '虚拟实操-' + label,
                'item_group': group, 'stock_uom': 'Nos', 'is_stock_item': 1, 'is_purchase_item': 1,
                'is_sales_item': 1, 'valuation_rate': 22 if code == raw else 40,
                'item_defaults': [{'company': company.name, 'default_warehouse': warehouses['stock'] if code == raw else warehouses['finished']}]}).insert()
        for role in ('A', 'B', 'C'):
            parties[role] = frappe.get_doc({'doctype': 'Customer', 'customer_name': parties[role],
                'customer_type': 'Company', 'customer_group': frappe.db.get_value('Customer Group', {'is_group': 0}, 'name'),
                'territory': frappe.db.get_value('Territory', {'is_group': 0}, 'name')}).insert().name
        parties['supplier'] = frappe.get_doc({'doctype': 'Supplier', 'supplier_name': parties['supplier'],
            'supplier_type': 'Company', 'supplier_group': frappe.db.get_value('Supplier Group', {'is_group': 0}, 'name')}).insert().name

        def submit(values):
            document = frappe.get_doc(values) if isinstance(values, dict) else values
            document.insert()
            document.submit()
            return document

        openings = []
        for role, qty in [('stock', 620), ('quality', 160)]:
            openings.append(submit({'doctype': 'Stock Entry', 'company': company.name,
                'stock_entry_type': 'Material Receipt', 'remarks': '注册虚拟实操初始库存；待检隔离不代表质量放行。',
                'items': [{'item_code': raw, 'qty': qty, 't_warehouse': warehouses[role], 'basic_rate': 22}]}))
        sales = {}
        for role, item, qty in [('A', fg_a, 240), ('B', fg_b, 320), ('C', raw, 420)]:
            due = c_due if role == 'C' else tomorrow
            sales[role] = submit({'doctype': 'Sales Order', 'company': company.name, 'customer': parties[role],
                'currency': 'CNY', 'delivery_date': str(due), 'reserve_stock': 0,
                'items': [{'item_code': item, 'qty': qty, 'rate': 30 if item == raw else 65,
                           'warehouse': warehouses['stock'] if item == raw else warehouses['finished'], 'delivery_date': str(due)}]})
        work, boms = {}, {}
        for role, item, qty, hour in [('A', fg_a, 240, 13), ('B', fg_b, 320, 9)]:
            boms[role] = submit({'doctype': 'BOM', 'company': company.name, 'item': item, 'quantity': 1,
                'is_active': 1, 'is_default': 1, 'with_operations': 0, 'currency': 'CNY',
                'items': [{'item_code': raw, 'qty': 1, 'uom': 'Nos', 'rate': 22}]})
            work[role] = submit({'doctype': 'Work Order', 'company': company.name, 'production_item': item,
                'bom_no': boms[role].name, 'qty': qty, 'sales_order': sales[role].name,
                'sales_order_item': sales[role].items[0].name, 'planned_start_date': f'{tomorrow} {hour:02d}:00:00',
                'planned_end_date': f'{tomorrow} 17:00:00', 'source_warehouse': warehouses['stock'],
                'wip_warehouse': warehouses['wip'], 'fg_warehouse': warehouses['finished'],
                'skip_transfer': 1, 'use_multi_level_bom': 1, 'reserve_stock': 0})
        from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import get_available_qty_to_reserve
        protected = submit({'doctype': 'Stock Reservation Entry', 'company': company.name, 'item_code': raw,
            'warehouse': warehouses['stock'], 'stock_uom': 'Nos', 'voucher_type': 'Sales Order',
            'voucher_no': sales['C'].name, 'voucher_detail_no': sales['C'].items[0].name,
            'voucher_qty': sales['C'].items[0].stock_qty,
            'available_qty': get_available_qty_to_reserve(raw, warehouses['stock']),
            'reserved_qty': 420, 'reservation_based_on': 'Qty'})
        request = submit({'doctype': 'Material Request', 'company': company.name, 'material_request_type': 'Purchase',
            'schedule_date': str(tomorrow), 'items': [{'item_code': raw, 'qty': 600, 'warehouse': warehouses['stock'],
                                                     'schedule_date': str(tomorrow)}]})
        from erpnext.stock.doctype.material_request.material_request import make_purchase_order
        from suke_ops import approval
        po = make_purchase_order(request.name, args={'supplier': parties['supplier']})
        po.supplier = parties['supplier']
        po.schedule_date = late
        for row in po.items:
            row.rate, row.schedule_date = 22, late
        po.insert()
        approval_name = approval.request('Purchase Order', po.name, '初始化注册虚拟实操：原600件采购已审批下单，不产生外部采购承诺。')
        approval.transition(approval_name, '提交审批')
        approval.transition(approval_name, '批准')
        po.reload()
        po.submit()
        po.add_comment('Comment', f'虚拟供应商通知：原承诺{tomorrow}到货600件，延期至{late}；可能先发200件，未确认到仓，未收货。')
        case = frappe.get_doc({'doctype': 'Supply Case', 'title': '虚拟实操 · 600件连接器延期 · ' + tag,
            'company': company.name, 'item_code': raw, 'warehouse': warehouses['stock'], 'purchase_order': po.name,
            'priority_sales_order': sales['A'].name, 'event_time': f'{today} 09:00:00',
            'event_message': '虚拟供应商通知：原定明天到的600件连接器晚三天，可能先发200件；尚未确认到仓。',
            'possible_early_qty': 200, 'early_arrival': f'{tomorrow}T08:00:00+08:00',
            'minimum_batch': 100, 'blocked_qty': 0, 'is_simulation': 1}).insert()
        source_lines = []
        for document in sales.values():
            source_lines.extend({'doctype': 'Sales Order', 'document': document.name, 'line': row.name,
                                 'item_code': row.item_code, 'warehouse': row.warehouse} for row in document.items)
        for document in work.values():
            source_lines.extend({'doctype': 'Work Order', 'document': document.name, 'line': row.name,
                                 'item_code': row.item_code, 'warehouse': row.source_warehouse} for row in document.required_items)
        manifest = {'dataset_id': key, 'tag': tag, 'version': VERSION, 'site': SITE,
            'label': '本地虚拟数据实操 · ' + tag, 'company': company.name, 'case_name': case.name,
            'raw_item': raw, 'item_codes': [raw, fg_a, fg_b], 'warehouses': list(warehouses.values()),
            'stock_warehouse': warehouses['stock'], 'quality_warehouse': warehouses['quality'],
            'warehouse_roles': warehouses, 'parties': parties, 'source_lines': source_lines,
            'sales_orders': [{'name': doc.name, 'role': role, 'label': '客户' + role + '销售单',
                              'items': [{'line': row.name, 'item_code': row.item_code, 'warehouse': row.warehouse} for row in doc.items]}
                             for role, doc in sales.items()],
            'work_orders': [{'name': doc.name, 'role': role, 'label': '客户' + role + '生产工单',
                             'sales_order': doc.sales_order, 'sales_order_item': doc.sales_order_item,
                             'bom': doc.bom_no, 'initial_connector_qty': 240 if role == 'A' else 320}
                            for role, doc in work.items()],
            'purchase_orders': [{'name': po.name, 'approval': approval_name,
                'items': [{'line': row.name, 'item_code': row.item_code, 'warehouse': row.warehouse,
                           'material_request': row.material_request, 'material_request_item': row.material_request_item} for row in po.items]}],
            'material_request': request.name,
            'stock_entries': [{'name': doc.name, 'warehouse': doc.items[0].t_warehouse,
                               'label': '合格仓初始620' if index == 0 else '待检仓初始160'} for index, doc in enumerate(openings)],
            'initial_reservations': [{'name': protected.name, 'document': sales['C'].name,
                                      'line': sales['C'].items[0].name, 'qty': 420}],
            'seeded_at': str(now_datetime()), 'seeded_by': frappe.session.user,
            'initial': {'usable_on_hand': 620, 'quarantine': 160, 'total_on_hand': 780,
                        'protected_reserved': 420, 'free': 200, 'at_risk_demand': 560,
                        'purchase_qty': 600, 'unconfirmed_early_qty': 200},
            'disclosure': '全部为注册虚拟数据，使用原生ERP工作流；未联系供应商、发送邮件或实际付款。'}
        frappe.get_doc({'doctype': DOCTYPE, 'dataset_key': key, 'tag': tag, 'active': 1,
            'company': company.name, 'supply_case': case.name,
            'manifest_json': json.dumps(manifest, ensure_ascii=False)}).insert()
        case.add_comment('Comment', '注册虚拟批次 ' + key + '。在库780=合格仓620+隔离待检160；C预留420保留，当前自由200。A/B成品销售仅由关联工单计连接器需求560；允许最小100件拆批。')
        return context(case)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
