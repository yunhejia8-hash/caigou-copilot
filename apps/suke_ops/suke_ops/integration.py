"""真实 ERP 生命周期验收；测试业务变更在事务内验证后回滚，保留隔离基础案例。"""
import json
from pathlib import Path
from unittest.mock import patch
import frappe
from suke_ops import api, approval
from suke_ops.seed import create_case


def run():
    frappe.set_user('Administrator')
    name = create_case('TEST')
    case = frappe.get_doc('Supply Case', name)
    checks = []
    frappe.db.savepoint('suke_verification')
    def expect_rejected(fn, description):
        try:
            fn()
        except frappe.ValidationError:
            checks.append(description)
        else:
            raise AssertionError(description + '未拦截')
    try:
        initial = api.get_analysis(name)
        assert len(initial['context']['demands']) == 3
        assert sum(r['qty'] for r in initial['context']['demands']) == 720
        assert initial['plans'][0]['shortage_qty'] == 400
        checks.append('销售直接需求与工单BOM需求合并，成品销售不重复；可能先发200不计可用')
        refused = api.negotiate(name, ceiling=22, max_rounds=2)
        assert refused['status'] == 'needs_human'
        assert api.get_analysis(name)['plans'][0]['shortage_qty'] == 400
        checks.append('议价达不到价格上限时停止并转人工，不增加供货')
        deal = api.negotiate(name)
        assert sum(r['qty'] for r in deal['schedule']) == 600
        quote = frappe.get_doc('Supplier Quotation', deal['supplier_quotation'])
        assert quote.docstatus == 0 and quote.net_total == 13360
        checks.append('议价结果写回真实供应商报价草稿，600件合计13360')
        analysis = api.get_analysis(name)
        assert [p['shortage_qty'] for p in analysis['plans']] == [200, 200]
        checks.append('模拟议价两轮确认200+400，原采购总量仍600')
        decision = api.propose(name, 'production_first', analysis['fingerprint'], '负责人验收选择生产优先，接受销售200件缺口')
        assert decision == api.propose(name, 'production_first', analysis['fingerprint'], '重复点击')
        expect_rejected(lambda: api.execute(decision), '未批准方案禁止执行；重复选择不重复建单')
        api.transition(decision, '提交审批')
        api.transition(decision, '驳回', '请明确销售缺口责任人')
        api.transition(decision, '修正重提', '修正：采购负责催交，销售负责人确认剩余交期')
        api.transition(decision, '批准')
        checks.append('真实Workflow提交、驳回、修正重提、批准全链路')
        previous = frappe.session.user
        frappe.session.user = 'synthetic-planner@example.invalid'
        try:
            with patch('frappe.get_roles', return_value=['Supply Planner']):
                try:
                    api.execute(decision)
                except frappe.PermissionError:
                    checks.append('非负责人无法执行批准方案')
                else:
                    raise AssertionError('权限未拦截')
        finally:
            frappe.session.user = previous
        case.reload()
        case.blocked_qty = 1
        case.save()
        expect_rejected(lambda: api.execute(decision), '审批后库存状态改变则拒绝执行过期方案')
        case.blocked_qty = 0
        case.save()
        execution = api.execute(decision)
        assert api.execute(decision) == execution
        assert len(execution['reservations']) == 1
        reserve = frappe.get_doc('Stock Reservation Entry', execution['reservations'][0])
        assert reserve.reserved_qty == 200 and reserve.voucher_type == 'Work Order'
        assert sum(r['qty'] for r in api.get_analysis(name)['context']['reservations']) == 320
        checks.append('执行新增工单预留200，保留原销售锁定120；重试不重复预留或建待办')
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        receipt = make_purchase_receipt(case.purchase_order)
        for row in receipt.items:
            row.qty = 200
        receipt.insert()
        receipt.submit()
        after = api.get_analysis(name)
        assert after['context']['actual_qty'] == 520
        assert sum(r['qty'] for r in after['context']['supplies'] if r['confirmed']) == 400
        checks.append('实际分批收货200后现存520，采购剩余400，已收数量不再计为在途')
        wo_name = next(r['document'] for r in after['context']['demands'] if r['kind'] == 'production')
        wo = frappe.get_doc('Work Order', wo_name)
        from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
        entry = frappe.get_doc(make_stock_entry(wo_name, 'Manufacture', qty=150))
        entry.insert()
        entry.submit()
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        delivery = make_delivery_note(wo.sales_order)
        delivery.insert()
        delivery.submit()
        wo.reload()
        assert wo.produced_qty == 150
        finished_so = frappe.get_doc('Sales Order', wo.sales_order)
        assert finished_so.items[0].delivered_qty == 150
        assert frappe.db.get_value('Bin', {'item_code': case.item_code, 'warehouse': case.warehouse}, 'actual_qty') == 220
        checks.append('真实生产领用300、完工150、成品销售发货150；连接器剩220')
        # 四个业务模块都走同一个审批模型；审批前不能提交原单。
        po = frappe.get_doc('Purchase Order', case.purchase_order)
        so = frappe.get_doc('Sales Order', case.priority_sales_order)
        definitions = [
            {'doctype': 'Purchase Order', 'company': case.company, 'supplier': po.supplier, 'schedule_date': po.schedule_date,
             'items': [{'item_code': case.item_code, 'qty': 1, 'rate': 22, 'warehouse': case.warehouse, 'schedule_date': po.items[0].schedule_date}]},
            {'doctype': 'Sales Order', 'company': case.company, 'customer': so.customer, 'delivery_date': so.delivery_date,
             'items': [{'item_code': case.item_code, 'qty': 1, 'rate': 30, 'warehouse': case.warehouse, 'delivery_date': so.items[0].delivery_date}]},
            {'doctype': 'Work Order', 'company': case.company, 'production_item': wo.production_item, 'bom_no': wo.bom_no, 'qty': 1,
             'planned_start_date': wo.planned_start_date, 'source_warehouse': case.warehouse, 'fg_warehouse': case.warehouse,
             'wip_warehouse': case.warehouse, 'skip_transfer': 1, 'use_multi_level_bom': 1, 'reserve_stock': 0},
            {'doctype': 'Stock Entry', 'company': case.company, 'stock_entry_type': 'Material Receipt',
             'items': [{'item_code': case.item_code, 'qty': 1, 't_warehouse': case.warehouse, 'basic_rate': 22}]},
        ]
        for values in definitions:
            source = frappe.get_doc(values).insert()
            request = approval.request(source.doctype, source.name, '四模块统一审批验收')
            approval.transition(request, '提交审批')
            expect_rejected(lambda: source.submit(), source.doctype + '待审批阻止提交')
            source.reload()
            approval.transition(request, '驳回', '补充业务说明')
            approval.transition(request, '修正重提', '已补充业务说明及责任人')
            approval.transition(request, '批准')
            source.reload()
            if source.doctype == 'Purchase Order':
                source.items[0].rate = 23
                source.save()
                expect_rejected(lambda: source.submit(), '采购批准后改价须重新审批')
                source.reload()
                revised = approval.request(source.doctype, source.name, '价格变更，重新申请审批')
                assert revised != request
                approval.transition(revised, '提交审批')
                approval.transition(revised, '批准')
                source.reload()
            source.submit()
            assert source.docstatus == 1
            checks.append(source.doctype + '批准后原生提交成功')
        from frappe.desk.doctype.dock.dock import resolve_app_dock
        dock = resolve_app_dock('erpnext')
        assert {r.get('link_to') for r in dock if not r.get('hidden')} == {'主页', 'Buying', 'Selling', 'Stock', 'Manufacturing'}, dock
        checks.append('解析后的ERPNext主导航保留独立主页与四业务模块')
    finally:
        frappe.db.rollback(save_point='suke_verification')
    result = {'status': 'PASS', 'checks': checks, 'count': len(checks), 'test_case': name,
              'mode': '实际ERP生命周期；测试变更完成断言后回滚，保留TEST基础案例', 'at': str(frappe.utils.now_datetime())}
    Path(frappe.get_site_path('private', 'files', 'suke-verification.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
