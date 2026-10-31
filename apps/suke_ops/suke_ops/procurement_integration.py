"""真实数据库闭环验收；构造数据仅存在于事务内，结束全部回滚。"""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import nowdate, getdate

from suke_ops import procurement_api as api, procurement as calc, approval, hub


def run():
    original = frappe.session.user
    frappe.set_user('Administrator')
    checks = []
    tracked = ['Material Request', 'Request for Quotation', 'Supplier Quotation', 'Purchase Order',
               'Purchase Receipt', 'Purchase Invoice', 'Payment Entry', 'Procurement Process',
               'Procurement Experience', 'Procurement Rule', 'User']
    baseline = {dt: frappe.db.count(dt) for dt in tracked}
    marker = uuid.uuid4().hex[:8]
    try:
        case = frappe.get_doc('Supply Case', 'SUP-CASE-00001')
        template = frappe.get_doc('Purchase Order', case.purchase_order)
        item = template.items[0]
        due = str(getdate(nowdate()) + timedelta(days=30))
        mr = frappe.get_doc({'doctype': 'Material Request', 'material_request_type': 'Purchase',
                              'company': template.company, 'schedule_date': due,
                              'items': [{'item_code': item.item_code, 'qty': 10, 'uom': item.uom,
                                         'warehouse': item.warehouse, 'schedule_date': due}]}).insert()
        mr.submit()
        proc = api.create(mr.name, '采购闭环事务验收-' + marker)
        assert api.create(mr.name) == proc
        assert api.overview(proc)['synthetic']
        checks.append('同需求重复建流程幂等；现有SUKE演示物料自动隔离')
        snap = api.overview(proc)
        api.confirm_stock(proc, snap['fingerprint'], '事务内核对：合成库存和在途，不用于正式采购')
        assert next(r for r in api.overview(proc)['stages'] if r['id'] == 'stock')['state'] == 'done'
        checks.append('库存核对绑定实时版本；逐物料不重复抵扣')
        rfq = frappe.get_doc(api.prepare_draft(proc, 'Material Request', mr.name, 'inquiry_from_request'))
        rfq.set('suppliers', [{'supplier': template.supplier}])
        rfq.insert()
        rfq.submit()
        api.attach(proc, 'Request for Quotation', rfq.name)
        quote = frappe.get_doc({'doctype': 'Supplier Quotation', 'supplier': template.supplier,
                               'company': template.company, 'currency': template.currency,
                               'transaction_date': nowdate(), 'valid_till': due,
                               'items': [{'item_code': item.item_code, 'qty': 10, 'rate': item.rate,
                                          'uom': item.uom, 'warehouse': item.warehouse,
                                          'request_for_quotation': rfq.name,
                                          'material_request': mr.name}]}).insert()
        quote.submit()
        api.discover(proc)
        assert any(r['name'] == quote.name for r in api.overview(proc)['documents'])
        checks.append('需求→询价→真实报价关联，后续单据由原生上游引用同步')
        po = frappe.get_doc(api.prepare_draft(proc, 'Supplier Quotation', quote.name, 'purchase_from_quotation'))
        for row in po.items:
            row.schedule_date = due
            row.warehouse = item.warehouse
            row.material_request = mr.name
            row.material_request_item = mr.items[0].name
        po.insert()
        api.attach(proc, 'Purchase Order', po.name)
        approval_name = approval.request('Purchase Order', po.name, '事务内采购闭环验收')
        approval.transition(approval_name, '提交审批')
        approval.transition(approval_name, '批准')
        api.attach(proc, 'Operations Approval', approval_name)
        po.reload()
        po.submit()
        checks.append('报价→采购草稿→人工审批→提交，使用原有审批和版本校验')
        try:
            api.review(proc, '不能把提交订单当作完成', 'Operations Approval', approval_name)
            raise AssertionError('审批被误当成实际结果')
        except frappe.ValidationError:
            frappe.clear_messages()
        receipt = frappe.get_doc(api.prepare_draft(proc, 'Purchase Order', po.name, 'receive_purchase'))
        for row in receipt.items:
            row.warehouse = item.warehouse
            row.qty = 5
        receipt.insert()
        receipt.submit()
        api.discover(proc)
        assert next(r for r in api.overview(proc)['stages'] if r['id'] == 'receipt')['state'] == 'partial'
        receipt_second = frappe.get_doc(api.prepare_draft(proc, 'Purchase Order', po.name, 'receive_purchase'))
        for row in receipt_second.items:
            row.warehouse = item.warehouse
        receipt_second.insert()
        receipt_second.submit()
        api.discover(proc)
        assert next(r for r in api.overview(proc)['stages'] if r['id'] == 'receipt')['state'] == 'done'
        try:
            api.review(proc, '尚未完成财务', 'Purchase Receipt', receipt.name)
            raise AssertionError('收货后未结清就被结案')
        except frappe.ValidationError:
            frappe.clear_messages()
        checks.append('原生收货映射和提交可用；仅收货或仅审批均不可结案')
        # 合并两次收货到一张发票，覆盖拆批收货后的结算。
        invoice = frappe.get_doc(api.prepare_draft(proc, 'Purchase Receipt', receipt.name, 'invoice_from_receipt'))
        second_invoice = frappe.get_doc(api.prepare_draft(proc, 'Purchase Receipt', receipt_second.name, 'invoice_from_receipt'))
        for line in second_invoice.items:
            invoice.append('items', {k:v for k,v in line.as_dict().items() if k not in ('name','parent','parenttype','parentfield','idx')})
        invoice.bill_no = 'PROC-TEST-' + marker
        invoice.insert()
        invoice.submit()
        api.discover(proc)
        financial = api.overview(proc)
        assert financial['reconciliation'] and next(r for r in financial['stages'] if r['id'] == 'finance')['state'] != 'done'
        checks.append('收货→采购发票原生映射可用；未付款的发票不标为财务完成')
        # 付款只打开未保存原生草稿，确保不会真实转账或自动提交。
        payment = api.prepare_draft(proc, 'Purchase Invoice', invoice.name, 'payment_from_invoice')
        assert payment['doctype'] == 'Payment Entry' and payment.get('__islocal')
        checks.append('采购发票→付款草稿，不保存、不提交、不实际支付')
        # 仅在回滚事务中模拟台账结清，原生Payment Entry不调用银行接口。
        payment_doc = frappe.get_doc(payment)
        payment_doc.reference_no = 'PROC-TEST-' + marker
        payment_doc.reference_date = nowdate()
        payment_doc.insert()
        payment_doc.submit()
        api.discover(proc)
        closed = api.review(proc, '事务内合成结案；同数量、币种、规格、交期和税费口径',
                            'Payment Entry', payment_doc.name, 300, 280, True)
        assert closed['realized_savings'] == 20
        assert next(r for r in api.overview(proc)['stages'] if r['id'] == 'review')['state'] == 'done'
        checks.append('回滚事务内模拟台账结清后才可复盘，并按可比成本计算收益；未连接银行')
        exp = api.record_experience(proc, 'comparison', '事务内经验', '核对相同规格、单位及交期',
                                     '先核对交期再比较单价', [{'field': 'quote_count', 'op': 'gte', 'value': 1}])
        assert api.record_experience(proc, 'comparison', '事务内经验', '核对相同规格、单位及交期',
                                     '先核对交期再比较单价', [{'field': 'quote_count', 'op': 'gte', 'value': 1}]) == exp
        api.verify_experience(exp, 'unknown', '仅核实判断出处，实际成效未知')
        assert not api.evolve(template.company)['candidates']
        checks.append('经验去重、未知结果不参与归纳、合成案例不污染正式规则')
        legacy = api.import_judgement(proc, 'Operations Approval', approval_name, 'approval')
        api.verify_experience(legacy, 'unknown', '历史理由出处可查；当时快照缺失，不参与自动归纳')
        other_mr = frappe.copy_doc(mr)
        other_mr.docstatus = 0
        other_mr.insert()
        other = api.create(other_mr.name, '旧经验检索验收')
        assert any(r['name'] == legacy for r in api.knowledge(other, 'approval')['experiences'])
        checks.append('旧审批判断经核实后可作为历史参考检索，但缺失旧快照时禁止自动学习')
        # 源权限和原生读取：基础账号不应通过新表绕过采购权限。
        user = 'procurement-' + marker + '@example.invalid'
        frappe.get_doc({'doctype': 'User', 'email': user, 'first_name': '采购事务权限验收',
                        'send_welcome_email': 0, 'user_type': 'System User',
                        'roles': [{'role': 'Desk User'}]}).insert()
        frappe.set_user(user)
        assert not api.listing()['processes']
        assert not frappe.has_permission('Procurement Process', 'read', doc=frappe.get_doc('Procurement Process', proc))
        try:
            assert not frappe.get_list('Procurement Experience', fields=['name', 'context_json'])
        except frappe.PermissionError:
            frappe.clear_messages()
        try:
            api.overview(proc)
            raise AssertionError('无业务权限者读取了采购流程')
        except frappe.PermissionError:
            frappe.clear_messages()
        frappe.set_user('Administrator')
        checks.append('基础账号无法从新API、原生读取或列表获得采购经验和快照')
        from frappe.model.document import Document
        rate = frappe.get_meta('Supplier Quotation Item').get_field('rate')
        prior_level = rate.permlevel
        try:
            rate.permlevel = 1
            with patch.object(Document, 'has_permlevel_access_to', return_value=False):
                try:
                    api.overview(proc)
                    raise AssertionError('来源高权限价格字段被采购快照绕过')
                except frappe.PermissionError:
                    frappe.clear_messages()
        finally:
            rate.permlevel = prior_level
        checks.append('来源价格字段提高权限后，工作台和冻结快照拒绝越权读取')
        # 样本读取用隔离的结构化夹具；归纳、版本、审核、检索与停用均走真数据库。
        examples = [{'name': f'fixture-{i}', 'process': f'fixture-{i}', 'stage': 'inquiry',
                     'verified': True, 'synthetic': False, 'outcome': 'positive', 'modified': 'v1',
                     'observed_at': str(getdate(nowdate()) - timedelta(days=10-i)) + 'T09:00:00',
                     'conditions': [{'field': 'quote_count', 'op': 'gte', 'value': 0}],
                     'context': {'quote_count': 0}, 'recommendation': '询价前统一规格、单位和交付条件'} for i in range(5)]
        with patch('suke_ops.procurement_api._experiences', return_value=examples), \
                patch('suke_ops.procurement_api._synthetic_source', return_value=False):
            candidate = api.evolve(template.company)['candidates'][0]
            assert frappe.get_doc('Procurement Rule', candidate).state == 'candidate'
            api.publish_rule(candidate, 'activate', '独立留出验证通过，事务内审核', due)
            assert frappe.get_doc('Procurement Rule', candidate).state == 'active'
            assert not api.knowledge(other, 'inquiry')['rules']  # 演示流程不会匹配正式规则。
            temporary = frappe.get_doc('Procurement Process', other)
            temporary.db_set('synthetic', 0, commit=False)  # 仅事务夹具，不对外开放修改标志。
            assert any(r['name'] == candidate for r in api.knowledge(other, 'inquiry')['rules'])
            examples.append({**examples[-1], 'name': 'negative-fixture', 'process': 'new-negative-case',
                             'outcome': 'negative', 'modified': 'v1'})
            assert not api.knowledge(other, 'inquiry')['rules']
            api.publish_rule(candidate, 'disable', '出现新反例，停用')
            assert frappe.get_doc('Procurement Rule', candidate).state == 'disabled'
        checks.append('结构化样本夹具验证候选→人工启用→新反例自动退出建议→停用，规则状态在真数据库持久化后回滚')
        with patch('suke_ops.procurement_api.knowledge', side_effect=RuntimeError('测试检索故障')):
            assert api.safe_advice(proc)['state'] == 'unavailable'
        before = hub.module_tools('buying')
        assert not any('knowledge' in r['name'] or 'procurement_process' in r['name'] for r in before)
        checks.append('知识检索故障正常降级；原有Agent工具集合保持不变')
        return {'passed': True, 'checks': checks, 'transaction': '全部测试单据与用户回滚，不保留测试业务记录'}
    finally:
        frappe.db.rollback()
        frappe.set_user('Administrator')
        after = {dt: frappe.db.count(dt) for dt in tracked}
        assert after == baseline, (baseline, after)
        frappe.set_user(original)
