"""创建隔离合成案例，调用标准ERP单据生命周期。"""
import json
from datetime import datetime, timedelta
import frappe
from frappe.utils import nowdate, getdate


def create_case(tag='MAIN', company_name=None):
    frappe.set_user('Administrator')
    title = '600件连接器延期 · ' + ('负责人决策现场' if tag == 'MAIN' else '验收-' + tag)
    existing = frappe.db.exists('Supply Case', {'title': title})
    if existing:
        return existing
    company_name = company_name or frappe.db.get_single_value('Global Defaults', 'default_company')
    if not company_name:
        frappe.throw('请先初始化公司与仓库，再创建合成演示。')
    company = frappe.get_doc('Company', company_name)
    warehouse = company.default_warehouse
    today = getdate(nowdate())
    tomorrow = today + timedelta(days=1)
    late = tomorrow + timedelta(days=3)
    raw = 'SUKE-CONN-' + tag
    fg = 'SUKE-DEVICE-' + tag
    group = frappe.db.get_value('Item Group', {'is_group': 0}, 'name')
    for code, name in [(raw, '连接器'), (fg, '双连接器组件')]:
        if not frappe.db.exists('Item', code):
            frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '合成演示-' + name + '-' + tag,
                            'item_group': group, 'stock_uom': 'Nos', 'is_stock_item': 1, 'is_purchase_item': 1,
                            'is_sales_item': 1, 'valuation_rate': 22 if code == raw else 44,
                            'item_defaults': [{'company': company.name, 'default_warehouse': warehouse}]}).insert()
    customer = frappe.db.get_value('Customer', {'customer_name': 'SUKE-FLOW-演示客户'}, 'name')
    supplier = frappe.db.get_value('Supplier', {'supplier_name': 'SUKE-FLOW-演示供应商'}, 'name')
    if not customer:
        customer = frappe.get_doc({'doctype': 'Customer', 'customer_name': 'SUKE-FLOW-演示客户',
                                   'customer_type': 'Company',
                                   'customer_group': frappe.db.get_value('Customer Group', {'is_group': 0}, 'name'),
                                   'territory': frappe.db.get_value('Territory', {'is_group': 0}, 'name')}).insert().name
    if not supplier:
        supplier = frappe.get_doc({'doctype': 'Supplier', 'supplier_name': 'SUKE-FLOW-演示供应商',
                                   'supplier_group': frappe.db.get_value('Supplier Group', {'is_group': 0}, 'name'),
                                   'supplier_type': 'Company'}).insert().name
    def submit(values):
        doc = frappe.get_doc(values)
        doc.insert()
        doc.submit()
        return doc
    opening = submit({'doctype': 'Stock Entry', 'company': company.name, 'stock_entry_type': 'Material Receipt',
                      'items': [{'item_code': raw, 'qty': 320, 't_warehouse': warehouse, 'basic_rate': 22}]})
    sales = []
    for item, qty in [(raw, 300), (raw, 120), (fg, 150)]:
        sales.append(submit({'doctype': 'Sales Order', 'company': company.name, 'customer': customer,
                             'currency': company.default_currency, 'delivery_date': str(tomorrow), 'reserve_stock': 0,
                             'items': [{'item_code': item, 'qty': qty, 'rate': 30 if item == raw else 65,
                                        'warehouse': warehouse, 'delivery_date': str(tomorrow)}]}))
    bom = submit({'doctype': 'BOM', 'company': company.name, 'item': fg, 'quantity': 1,
                  'is_active': 1, 'is_default': 1, 'with_operations': 0, 'currency': company.default_currency,
                  'items': [{'item_code': raw, 'qty': 2, 'uom': 'Nos', 'rate': 22}]})
    wo = submit({'doctype': 'Work Order', 'company': company.name, 'production_item': fg,
                 'bom_no': bom.name, 'qty': 150, 'sales_order': sales[2].name, 'sales_order_item': sales[2].items[0].name,
                 'planned_start_date': str(tomorrow) + ' 08:00:00', 'planned_end_date': str(tomorrow) + ' 16:00:00',
                 'fg_warehouse': warehouse, 'wip_warehouse': warehouse, 'source_warehouse': warehouse,
                 'skip_transfer': 1, 'use_multi_level_bom': 1, 'reserve_stock': 0})
    settings = frappe.get_single('Stock Settings')
    if not settings.enable_stock_reservation:
        settings.enable_stock_reservation = 1
        settings.save()
    locked = submit({'doctype': 'Stock Reservation Entry', 'company': company.name, 'item_code': raw,
                     'warehouse': warehouse, 'stock_uom': 'Nos', 'voucher_type': 'Sales Order',
                     'voucher_no': sales[1].name, 'voucher_detail_no': sales[1].items[0].name,
                     'voucher_qty': 120, 'available_qty': 320, 'reserved_qty': 120, 'reservation_based_on': 'Qty'})
    mr = submit({'doctype': 'Material Request', 'company': company.name, 'material_request_type': 'Purchase',
                 'schedule_date': str(tomorrow), 'items': [{'item_code': raw, 'qty': 600, 'warehouse': warehouse, 'schedule_date': str(tomorrow)}]})
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order
    po = make_purchase_order(mr.name, args={'supplier': supplier})
    po.supplier = supplier
    for row in po.items:
        row.rate = 22
        row.schedule_date = tomorrow
    po.insert()
    po.submit()
    for row in po.items:
        row.schedule_date = late
    po.save()
    po.add_comment('Comment', f'合成事件：上午9点通知，原定{tomorrow}到仓600件延至{late}，可能先发200件；尚未确认到仓。')
    case = frappe.get_doc({'doctype': 'Supply Case', 'title': title, 'company': company.name, 'item_code': raw,
                          'warehouse': warehouse, 'purchase_order': po.name, 'priority_sales_order': sales[0].name,
                          'event_time': str(today) + ' 09:00:00', 'event_message': '原定明天到的600件连接器要晚三天，但可能先发200件。',
                          'possible_early_qty': 200, 'early_arrival': str(tomorrow) + 'T07:00:00+08:00',
                          'minimum_batch': 50, 'blocked_qty': 0, 'is_simulation': 1}).insert()
    case.add_comment('Comment', '合成数据：现存320件，已有120件锁定；直接销售300件，生产150件组件消耗300件连接器。生产关联的成品销售不重复计为连接器需求。')
    frappe.db.commit()
    return case.name


def create_all():
    return {'main': create_case('MAIN'), 'verification': create_case('VERIFY')}
