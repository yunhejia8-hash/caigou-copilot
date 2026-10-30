"""普通履约、结算和被裁剪模块相关手动路径的真实数据库回归。"""
import json
from pathlib import Path

import frappe
from frappe.utils import nowdate


def run():
    frappe.set_user('Administrator')
    company = frappe.get_doc('Company', frappe.db.get_single_value('Global Defaults', 'default_company'))
    warehouse = company.default_warehouse
    customer = frappe.db.get_value('Customer', {'customer_name': 'SUKE-FLOW-演示客户'}, 'name')
    supplier = frappe.db.get_value('Supplier', {'supplier_name': 'SUKE-FLOW-演示供应商'}, 'name')
    assert customer and supplier, '先运行核心案例初始化合成客户和供应商'
    checks = []
    frappe.db.savepoint('simple_verification')

    def submit(doc):
        doc = frappe.get_doc(doc) if isinstance(doc, dict) else doc
        doc.insert()
        doc.submit()
        return doc

    def balance(code):
        return frappe.db.get_value('Bin', {'item_code': code, 'warehouse': warehouse}, 'actual_qty') or 0

    try:
        code = 'SUKE-SIMPLE-ROLLBACK'
        item = frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '普通履约合成验收物料',
                              'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                              'stock_uom': 'Nos', 'is_stock_item': 1, 'is_sales_item': 1, 'is_purchase_item': 1,
                              'item_defaults': [{'company': company.name, 'default_warehouse': warehouse}]}).insert()
        submit({'doctype': 'Stock Entry', 'company': company.name, 'stock_entry_type': 'Material Receipt',
                'items': [{'item_code': code, 'qty': 40, 't_warehouse': warehouse, 'basic_rate': 22}]})
        so = submit({'doctype': 'Sales Order', 'company': company.name, 'customer': customer, 'delivery_date': nowdate(),
                     'items': [{'item_code': code, 'qty': 100, 'rate': 30, 'warehouse': warehouse, 'delivery_date': nowdate()}]})
        assert balance(code) == 40
        mr = submit({'doctype': 'Material Request', 'company': company.name, 'material_request_type': 'Purchase',
                     'schedule_date': nowdate(), 'items': [{'item_code': code, 'qty': 60, 'warehouse': warehouse, 'schedule_date': nowdate()}]})
        from erpnext.stock.doctype.material_request.material_request import make_purchase_order
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        po = make_purchase_order(mr.name, args={'supplier': supplier})
        po.supplier = supplier
        for row in po.items:
            row.rate = 22
        submit(po)
        receipt = submit(make_purchase_receipt(po.name))
        assert balance(code) == 100
        delivery = submit(make_delivery_note(so.name))
        assert balance(code) == 0
        so.reload()
        assert so.per_delivered == 100
        checks.append('销售100缺60→采购申请60→采购60→收货60→发货100，库存归零')

        from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
        from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
        purchase_invoice = submit(make_purchase_invoice(receipt.name))
        sales_invoice = submit(make_sales_invoice(delivery.name))
        assert purchase_invoice.grand_total == 1320 and sales_invoice.grand_total == 3000
        checks.append('采购收货及销售发货映射账单并提交，保留原生账务记账')

        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        cash = frappe.db.get_value('Account', {'company': company.name, 'account_type': 'Cash', 'is_group': 0}, 'name')
        assert cash
        for invoice in [purchase_invoice, sales_invoice]:
            payment = get_payment_entry(invoice.doctype, invoice.name, bank_account=cash)
            payment.reference_no = 'SYNTHETIC-VERIFICATION'
            payment.reference_date = nowdate()
            submit(payment)
            invoice.reload()
            assert invoice.outstanding_amount == 0
        checks.append('采购付款与销售收款完成，账单未结余额归零')

        submit({'doctype': 'Stock Entry', 'company': company.name, 'stock_entry_type': 'Material Receipt',
                'items': [{'item_code': code, 'qty': 10, 't_warehouse': warehouse, 'basic_rate': 22}]})
        spot = submit({'doctype': 'Sales Order', 'company': company.name, 'customer': customer, 'delivery_date': nowdate(),
                       'items': [{'item_code': code, 'qty': 5, 'rate': 30, 'warehouse': warehouse, 'delivery_date': nowdate()}]})
        submit(make_delivery_note(spot.name))
        assert balance(code) == 5
        checks.append('现货10直接交付5，无须新增采购，余量5')

        from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_available_serial_nos, get_auto_batch_nos
        args = frappe._dict(item_code=code, warehouse=warehouse, qty=1, company=company.name)
        assert get_available_serial_nos(args) == []
        assert get_auto_batch_nos(args) == []
        checks.append('可用序列号及批次查询不依赖已删除的POS表')
        from erpnext.stock.doctype.pick_list.pick_list import update_child_item
        target = frappe._dict()
        update_child_item(frappe._dict(item_code=code, item_group=item.item_group), target,
                          frappe._dict(company=company.name, project=None))
        checks.append('拣货映射成本中心不查询已删除的Project表')
        from erpnext.focus_boundary import REMOVED
        assert not any(frappe.db.exists('DocType', name) for name in REMOVED)
        checks.append('新站点不存在所有已裁剪单据元数据')
    finally:
        frappe.db.rollback(save_point='simple_verification')
    result = {'status': 'PASS', 'checks': checks, 'count': len(checks), 'at': str(frappe.utils.now_datetime()),
              'mode': '真实ERP单据生命周期；测试业务变更事务回滚'}
    Path(frappe.get_site_path('private', 'files', 'simple-verification.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
