"""PR 协作功能的真实数据库回归；只使用隔离验收站点与合成记录。"""
import json
import uuid
from unittest.mock import patch

import frappe
from suke_ops import hub


def run():
    frappe.set_user('Administrator')
    checks, names = [], []
    doctypes = ['Purchase Order', 'Purchase Receipt', 'Material Request', 'Sales Order', 'Stock Entry', 'Work Order']
    before = {dt: frappe.db.count(dt) for dt in doctypes}

    def start(**kwargs):
        with patch('suke_ops.agent_runtime.configured', return_value=True), patch('frappe.enqueue'):
            result = hub.start('隔离验收：核对合成证据', request_id=str(uuid.uuid4()), **kwargs)
        names.append(result['name'])
        frappe.db.commit()
        return result['name']

    def provider(system, messages, tools):
        if '本轮程序已按当前账号权限读取以下依据' in str(messages):
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '合成预读证据已核对，仍需审批。'}]}
        if not any(isinstance(m['content'], list) and any(b.get('type') == 'tool_result' for b in m['content']) for m in messages):
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'evidence', 'name': 'read_evidence', 'input': {}}]}
        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '合成证据已核对，尚需负责人审批，不执行。'}]}

    try:
        case = frappe.get_doc('Supply Case', 'SUP-CASE-00001')
        order = hub.read_record('buying', 'Purchase Order', case.purchase_order)
        assert order['_provenance']['name'] == case.purchase_order
        assert order['_provenance']['created_by'] and order['_provenance']['read_at']
        checks.append('订单查询返回实际单号、创建修改账号与读取时间')

        name = start(module='control', scenario_id=0, scenario_updated=True)
        with patch('suke_ops.agent_runtime.request_model', provider):
            hub.run_turn(name)
        result = hub.poll(name)
        assert result['state'] == 'done' and result['synthetic'] and not result['partial'], result
        assert len(result['discussion']) == 10 and len(result['events']) == 40
        assert result['verified_evidence']['verified_plan']
        checks.append('真实任务持久化四部门两轮与总控共十步、四十个查询事件及合成标识')

        followup = start(module='control', previous_turn=name, human_note='合成测试：原先提前批仍待再次确认。')
        scenario = json.loads(frappe.get_doc('Agent Turn', followup).result)['scenario']
        assert scenario['human_sources'][0]['reported_by'] == 'Administrator'
        assert scenario['human_sources'][0]['source_path'] == name
        assert not scenario['verified_plan']
        with patch('suke_ops.agent_runtime.request_model', provider):
            hub.run_turn(followup)
        assert hub.poll(name)['verified_evidence']['verified_plan']
        assert not hub.poll(followup)['verified_evidence']['verified_plan']
        checks.append('补充信息生成新轮次并记录人员时间，暂停旧方案且原讨论保持不变')

        failed = start(module='buying')
        responses = iter([
            {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'order', 'name': 'read_record',
                'input': {'doctype': 'Purchase Order', 'name': case.purchase_order}}]},
            RuntimeError('合成模型响应失败')])
        def failing_provider(*args):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        with patch('suke_ops.agent_runtime.request_model', failing_provider):
            hub.run_turn(failed)
        failed_result = hub.poll(failed)
        assert failed_result['state'] == 'failed'
        order_events = [event for event in failed_result['events'] if event.get('tool') == 'read_record']
        assert [event['kind'] for event in order_events] == ['query', 'evidence']
        assert json.loads(order_events[1]['result_excerpt'])['_provenance']['name'] == case.purchase_order
        restored = next(row for row in hub.history(frappe.get_doc('Agent Turn', failed).conversation) if row['name'] == failed)
        assert restored['events'] == failed_result['events']
        checks.append('真实订单查询后模型失败仍保留查询过程及原单来源')

        frappe.set_user('Guest')
        for method in [lambda: hub.poll(name), lambda: hub.discussion_activity(),
                       lambda: hub.start('测试', previous_turn=name, human_note='越权补充')]:
            try:
                method()
                raise AssertionError('访客权限未拒绝')
            except frappe.PermissionError:
                pass
        frappe.set_user('Administrator')
        assert {dt: frappe.db.count(dt) for dt in doctypes} == before
        checks.append('访客无法查看或补充讨论；采购销售库存生产业务单据数量保持不变')
        return {'passed': len(checks), 'checks': checks, 'model': '模拟，Frappe与数据库真实'}
    finally:
        frappe.set_user('Administrator')
        for name in names:
            if frappe.db.exists('Agent Turn', name):
                frappe.delete_doc('Agent Turn', name, ignore_permissions=True)
        frappe.db.commit()
