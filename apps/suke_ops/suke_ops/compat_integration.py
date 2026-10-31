"""隔离站点真实提交回归：建单后意外失败不得丢单或重复建单。"""
import uuid
from datetime import timedelta
from unittest.mock import patch
import frappe
from frappe.utils import add_days, nowdate
from suke_ops import hub


def run():
    frappe.set_user('Administrator')
    company = frappe.get_doc('Company', frappe.db.get_single_value('Global Defaults', 'default_company'))
    code = 'PR-COMPAT-' + uuid.uuid4().hex[:8]
    frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': '合成兼容回归',
                    'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                    'stock_uom': 'Nos', 'is_stock_item': 1, 'is_purchase_item': 1}).insert()
    frappe.db.commit()
    payload = {'company': company.name, 'schedule_date': add_days(nowdate(), 7),
               'items': [{'item_code': code, 'qty': 3, 'uom': 'Nos', 'warehouse': company.default_warehouse}]}
    conversation = str(uuid.uuid4())
    def call(name, args):
        return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': name, 'name': name, 'input': args}]}
    def execute(outputs, module='buying-demand', conversation_id=None, model=None):
        with patch('suke_ops.agent_runtime.configured', return_value=True), patch('frappe.enqueue'):
            turn = hub.start('合成需求登记', module=module, conversation=conversation_id or conversation)
        frappe.db.commit()
        iterator = iter(outputs)
        def provider(*args):
            output = next(iterator)
            if isinstance(output, Exception):
                raise output
            return output
        with patch('suke_ops.agent_runtime.request_model', model or provider):
            hub.run_turn(turn['name'])
        return hub.poll(turn['name'])
    before = frappe.db.count('Material Request')
    first = execute([call('create_material_request', payload), ValueError('合成后续异常')])
    assert frappe.db.count('Material Request') == before + 1, first
    assert first['state'] == 'done' and first['documents'], first
    name = first['documents'][0]['name']
    assert name in first['answer'] and '未由 Agent 保存' not in first['answer']
    second = execute([call('create_material_request', payload),
                      call('report_action', {'status': 'document_created', 'reason': '已复用', 'next_step': '核对需求单'}),
                      {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已复用原需求单'}]}])
    assert second['state'] == 'done' and second['documents'][0]['name'] == name, second
    assert second['documents'][0]['reused']
    assert frappe.db.count('Material Request') == before + 1
    # 同批结果报告依赖前面的子部门真实建单，不能为并行而颠倒执行顺序。
    report = call('report_action', {'status': 'document_created', 'reason': '需求单已登记', 'next_step': '打开核对'})
    combined = call('ask_module', {'module': 'buying-demand', 'question': '按合成需求登记物料需求单'})
    combined['content'].extend(report['content'])
    final = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '需求已登记，请核对。'}]}
    parent_outputs = iter([combined, final])
    child_outputs = iter([call('read_request_context', {'search': code}),
                          call('create_material_request', payload), report, final])
    def delegated_provider(system, *args):
        return next(parent_outputs if '你是总控' in system else child_outputs)
    delegated = execute([], module='control', conversation_id=str(uuid.uuid4()), model=delegated_provider)
    assert delegated['state'] == 'done' and len(delegated['documents']) == 1, delegated
    assert delegated['documents'][0]['name'] != name and frappe.db.count('Material Request') == before + 2
    assert delegated['action']['status'] == 'document_created'
    assert all(row['status'] == 'done' for row in delegated['modules']), delegated
    events = delegated['events']
    created_at = next(event['id'] for event in events if event.get('tool') == 'create_material_request' and event['kind'] == 'evidence')
    reported_at = next(event['id'] for event in events if event['module'] == 'control' and event.get('tool') == 'report_action' and event['kind'] == 'evidence')
    assert created_at < reported_at
    # 模拟建单审计已提交、进程中断，旧任务超时后重试也必须复用原单。
    crash_conversation = str(uuid.uuid4())
    crashed = execute([call('create_material_request', payload), ValueError('模拟进程结束前状态')],
                      conversation_id=crash_conversation)
    crash_name = crashed['documents'][0]['name']
    frappe.db.set_value('Agent Turn', crashed['name'], {'state': 'running',
                        'creation': frappe.utils.now_datetime() - timedelta(minutes=20)})
    frappe.db.commit()
    count_before_retry = frappe.db.count('Material Request')
    recovered = execute([call('create_material_request', payload), report, final], conversation_id=crash_conversation)
    assert frappe.db.get_value('Agent Turn', crashed['name'], 'state') == 'failed'
    assert recovered['documents'][0]['name'] == crash_name and recovered['documents'][0]['reused'], recovered
    assert frappe.db.count('Material Request') == count_before_retry
    return {'status': 'PASS', 'count': 4, 'checks': ['真实提交后意外异常仍恢复已保存单号', '同会话重试复用已保存需求单，不重复建单', '同批委派先真实建单再报告结果，保留工具依赖顺序', '模拟进程中断后的过期任务重试仍复用已保存需求单'], 'commits': '真实数据库提交，未模拟commit', 'synthetic': True}
