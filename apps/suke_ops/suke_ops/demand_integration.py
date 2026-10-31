"""需求登记到采购审批的真实数据库验证；模型响应模拟，业务变更全部回滚。"""
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.utils import add_days, nowdate

from suke_ops import approval, demand, hub


def run():
    frappe.set_user('Administrator')
    checks = []
    frappe.db.savepoint('demand_verification')
    try:
        company = frappe.get_doc('Company', frappe.db.get_single_value('Global Defaults', 'default_company'))
        code = 'SUKE-DEMAND-TEST-' + uuid.uuid4().hex[:8]
        frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '需求登记合成验收物料',
                        'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                        'stock_uom': 'Nos', 'is_stock_item': 1, 'is_purchase_item': 1}).insert()
        payload = {'company': company.name, 'schedule_date': add_days(nowdate(), 7),
                   'items': [{'item_code': code, 'qty': 17, 'uom': 'Nos', 'warehouse': company.default_warehouse}]}
        context = demand.context(code)
        assert [row.name for row in context['items']] == [code]
        assert any(row.name == company.default_warehouse for row in context['warehouses'])
        checks.append('物料候选、库存单位、默认公司和仓库来自真实主数据')

        def call(tool, args):
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': tool, 'name': tool, 'input': args}]}

        def turn(conversation=None, module='buying-demand'):
            return frappe.get_doc({'doctype': 'Agent Turn', 'conversation': conversation or str(uuid.uuid4()),
                                   'agent_module': module, 'question': f'采购17 Nos的{code}，下周需要，请登记需求单。',
                                   'state': 'queued'}).insert(ignore_permissions=True)

        for wording in ('采购17个手机配件，请登记', f'采购17 Nos的{code}-OTHER，请登记'):
            unselected=turn()
            unselected.question=wording
            unselected.answer=f'助手建议选择{code}，这不代表用户确认'
            unselected.save(ignore_permissions=True)
            before_unselected=frappe.db.count('Material Request')
            try:
                demand.create('buying-demand',unselected,**payload)
                raise AssertionError('用户未选定物料却创建了需求单')
            except RuntimeError as error:
                assert '用户选定' in str(error),str(error)
            assert frappe.db.count('Material Request')==before_unselected
        checks.append('泛称、相似编码和助手推荐不能代替用户选料；服务端拒绝猜测建单')

        final = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '需求已登记，请核对提交后进入询价。'}]}
        report = call('report_action', {'status': 'document_created', 'reason': '已保存物料需求单草稿。',
                                       'next_step': '打开核对后提交并询价。'})

        def execute(probe, outputs):
            iterator = iter(outputs)
            def provider(*args):
                output = next(iterator)
                if isinstance(output, Exception):
                    raise output
                return output
            # run_turn 的业务逻辑不变，只将事务提交推迟到测试末尾回滚。
            with patch.object(frappe.db, 'commit'), patch('suke_ops.agent_runtime.request_model', provider):
                hub.run_turn(probe.name)
            return hub.poll(probe.name)

        before = frappe.db.count('Material Request')
        probe = turn()
        result = execute(probe, [call('read_request_context', {'search': code}),
                                call('create_material_request', payload), report, final])
        assert result['state'] == 'done', result
        assert frappe.db.count('Material Request') == before + 1
        created = result['documents'][0]
        mr = frappe.get_doc('Material Request', created['name'])
        assert mr.docstatus == 0 and mr.material_request_type == 'Purchase'
        assert mr.items[0].item_code == code and mr.items[0].qty == 17
        assert str(mr.items[0].schedule_date) == payload['schedule_date']
        assert created['name'] in result['answer'] and '已保存业务单据：1 张' in result['answer']
        assert hub.history(probe.conversation, 'buying-demand')[0]['documents'] == result['documents']
        checks.append('用户需求经过真实Agent调用链生成已保存需求单、真实单号和刷新可恢复的打开入口')

        repeated = execute(turn(probe.conversation), [call('create_material_request', payload), report, final])
        assert repeated['documents'][0]['name'] == mr.name and repeated['documents'][0]['reused']
        assert frappe.db.count('Material Request') == before + 1
        checks.append('同会话重复登记相同需求复用已有单据，不重复采购')

        recovery = execute(turn(), [call('create_material_request', payload), RuntimeError('合成模型响应失败')])
        assert recovery['state'] == 'done' and recovery['documents']
        assert frappe.db.exists('Material Request', recovery['documents'][0]['name'])
        assert '模型后续说明未完成' in recovery['answer']
        checks.append('建单后模型失败仍保留真实需求单和打开入口，明确后续说明未完成')

        parent = turn(module='control')
        child_outputs = iter([call('read_request_context', {'search': code}), call('create_material_request', payload), report, final])
        parent_outputs = iter([call('ask_module', {'module': 'buying-demand', 'question': parent.question}), report, final])
        with patch.object(frappe.db, 'commit'), patch('suke_ops.agent_runtime.request_model',
                lambda system, *args: next(parent_outputs if '你是总控' in system else child_outputs)):
            hub.run_turn(parent.name)
        parent_result = hub.poll(parent.name)
        assert parent_result['state'] == 'done' and len(parent_result['documents']) == 1, parent_result
        checks.append('总控可委派需求Agent创建真实需求单，单号和入口回传总控')

        count = frappe.db.count('Material Request')
        for change, reason in [({'schedule_date': ''}, '有效需求日期'),
                               ({'schedule_date': add_days(nowdate(), -1)}, '早于今天'),
                               ({'items': [{**payload['items'][0], 'uom': 'UNKNOWN-UNIT'}]}, '单位'),
                               ({'items': [{**payload['items'][0], 'qty': 0}]}, '参数')]:
            try:
                demand.create('buying-demand', probe, **{**payload, **change})
                raise AssertionError('无效需求未拒绝')
            except RuntimeError as error:
                assert reason in str(error), str(error)
        try:
            demand.create('selling', probe, **payload)
            raise AssertionError('跨模块创建未拒绝')
        except frappe.PermissionError:
            pass
        original_permission = frappe.has_permission
        with patch('frappe.has_permission', lambda dt, ptype='read', *a, **kw:
                   False if dt == 'Material Request' and ptype == 'create' else original_permission(dt, ptype, *a, **kw)):
            try:
                demand.create('buying-demand', probe, **payload)
                raise AssertionError('无创建权限未拒绝')
            except frappe.PermissionError:
                pass
        assert frappe.db.count('Material Request') == count
        checks.append('缺日期、过去日期、错误单位、无效数量、越权模块及无创建权限均拒绝且不落单')

        blocked = execute(turn(), [call('create_material_request', {**payload, 'schedule_date': ''}),
            call('report_action', {'status': 'blocked', 'reason': '缺少有效需求日期。', 'next_step': '请补充需求日期。'}), final])
        assert blocked['state'] == 'done' and not blocked['documents'] and '需求日期' in blocked['answer']
        assert frappe.db.count('Material Request') == count
        checks.append('建单校验失败作为明确缺项返回对话，不伪报成功')

        mr.submit()
        inquiry_offer = hub.draft_offer('buying-inquiry', 'inquiry_from_request', mr.name)
        offer_turn = turn()
        offer_turn.state, offer_turn.result = 'done', json.dumps({'offers': [inquiry_offer]})
        offer_turn.save(ignore_permissions=True)
        inquiry = hub.open_draft(offer_turn.name, 0)
        assert inquiry['doctype'] == 'Request for Quotation' and inquiry['__islocal']
        assert inquiry['items'][0]['material_request'] == mr.name
        checks.append('已提交需求单可转原生询价草稿，物料行保留需求来源')

        from erpnext.stock.doctype.material_request.material_request import make_supplier_quotation
        supplier = frappe.db.get_value('Supplier', {'supplier_name': 'SUKE-FLOW-演示供应商'}, 'name')
        assert supplier
        quotation = make_supplier_quotation(mr.name)
        quotation.supplier = supplier
        quotation.valid_till = add_days(nowdate(), 10)
        for row in quotation.items:
            row.rate = 12
        quotation.insert().submit()
        purchase_offer = hub.draft_offer('buying-comparison', 'purchase_from_quotation', quotation.name)
        offer_turn.result = json.dumps({'offers': [purchase_offer]})
        offer_turn.save(ignore_permissions=True)
        purchase = frappe.get_doc(hub.open_draft(offer_turn.name, 0))
        assert purchase.doctype == 'Purchase Order' and purchase.items[0].material_request == mr.name
        assert purchase.items[0].supplier_quotation == quotation.name
        purchase.insert()
        approval_name = approval.request('Purchase Order', purchase.name, '合成比价后采购审批验收')
        approval.transition(approval_name, '提交审批')
        try:
            approval.guard_submit(purchase)
            raise AssertionError('待审批采购单未被阻止提交')
        except frappe.ValidationError:
            pass
        approval.transition(approval_name, '批准')
        purchase.submit()
        assert purchase.docstatus == 1
        checks.append('选定报价映射采购草稿并保留需求和报价来源；进入审批后批准前禁止提交，批准后生成正式采购订单')
    finally:
        frappe.db.rollback(save_point='demand_verification')
    result = {'status': 'PASS', 'count': len(checks), 'checks': checks,
              'mode': '真实ERP单据和权限，模拟模型响应；合成业务与对话全部事务回滚', 'at': str(frappe.utils.now_datetime())}
    Path(frappe.get_site_path('private', 'files', 'demand-verification.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def run_live():
    """仅向已配置模型发送合成需求及最小主数据，验证模型能自主完成真实建单。"""
    frappe.set_user('Administrator')
    frappe.db.savepoint('live_demand_verification')
    try:
        company = frappe.get_doc('Company', frappe.db.get_single_value('Global Defaults', 'default_company'))
        code = 'SUKE-LIVE-DEMAND-' + uuid.uuid4().hex[:8]
        frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '合成验收连接器',
                        'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                        'stock_uom': 'Nos', 'is_stock_item': 1, 'is_purchase_item': 1}).insert()
        due = add_days(nowdate(), 7)
        probe = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
            'agent_module': 'control', 'state': 'queued',
            'question': f'需要采购物料 {code}，数量17 Nos，需求日期{due}，公司{company.name}，仓库{company.default_warehouse}。'
                        '请直接创建物料需求单草稿，给出真实单号和询价比价审批的下一步。'}).insert(ignore_permissions=True)
        minimal_context = {'as_of': nowdate(), 'default_company': company.name,
            'companies': [{'name': company.name, 'default_warehouse': company.default_warehouse}],
            'warehouses': [{'name': company.default_warehouse, 'company': company.name}],
            'items': [{'name': code, 'item_name': '合成验收连接器', 'stock_uom': 'Nos'}]}
        with patch.object(frappe.db, 'commit'), patch('suke_ops.hub.latest_reports', return_value=[]), \
                patch('suke_ops.hub.records', return_value=[]), \
                patch('suke_ops.hub.module_board', side_effect=lambda module: {'module': module, 'as_of': nowdate(), 'metrics': []}), \
                patch('suke_ops.demand.context', return_value=minimal_context):
            hub.run_turn(probe.name)
        result = hub.poll(probe.name)
        assert result['state'] == 'done' and result.get('documents'), result.get('answer')
        actual = frappe.get_doc('Material Request', result['documents'][0]['name'])
        assert actual.docstatus == 0 and actual.items[0].item_code == code and actual.items[0].qty == 17
        evidence = {'status': 'PASS', 'mode': '真实模型、生产工具集合及总控委派建单链；读取候选与看板使用最小合成数据；所有测试业务回滚',
                    'tools': [{'agent': row['agent'], 'tool': row['tool']} for row in result['trace']], 'answer': result['answer'],
                    'material_request': actual.name, 'qty': actual.items[0].qty, 'docstatus': actual.docstatus,
                    'at': str(frappe.utils.now_datetime())}
    finally:
        # run_turn 在异常分支可能已回滚整个事务；此处只需保证测试数据不提交。
        frappe.db.rollback()
    Path(frappe.get_site_path('private', 'files', 'demand-live-verification.json')).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return evidence
