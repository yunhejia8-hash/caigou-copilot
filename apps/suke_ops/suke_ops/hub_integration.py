"""真实数据库上的 Agent 权限、草稿、隔离及委派验证；模型响应在该测试中模拟。"""
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import frappe

from suke_ops import hub


def run():
    frappe.set_user('Administrator')
    checks = []
    user = 'hub-test@example.invalid'
    existing_user = frappe.db.exists('User', user)
    if not existing_user:
        frappe.get_doc({'doctype': 'User', 'email': user, 'first_name': 'Agent权限验收',
                        'send_welcome_email': 0, 'user_type': 'System User',
                        'roles': [{'role': 'Desk User'}]}).insert()
    frappe.db.commit()
    names = []
    try:
        data = hub.dashboard()
        assert len(data['modules']) == 4 and all(row['metrics'] for row in data['modules'])
        checks.append('四模块面板来自实际数据库，指标和原生入口齐全')
        from frappe.boot import get_module_sidebars
        from frappe.desk.doctype.dock.dock import resolve_app_dock
        sidebars = get_module_sidebars()
        assert sidebars['主页']['module'] == 'Supply Home'
        assert [row.get('link_to') for row in sidebars['主页']['items']] == ['operations-hub', 'operations-data']
        for name in ['Buying', 'Selling', 'Stock', 'Manufacturing']:
            slug = name.lower()
            assert [row.get('link_to') for row in sidebars[name]['items']] == ['operations-' + slug, 'operations-data-' + slug]
        assert next(row for row in resolve_app_dock('erpnext') if not row.get('hidden'))['link_to'] == '主页'
        checks.append('主页为独立模块且位于导航首位；业务模块仅保留工作台和本模块数据')
        assert len(hub.dashboard('control', 'data')['modules']) == 4
        for module in hub.MODULE_DOCS:
            with patch('suke_ops.hub.module_board', wraps=hub.module_board) as read_board:
                scoped = hub.dashboard(module, 'data')
                read_board.assert_called_once_with(module)
            assert [row['module'] for row in scoped['modules']] == [module]
            assert not scoped['cases'] and not scoped['decisions'] and not scoped['reports']
            expected = {'buying': 'Purchase Order', 'selling': 'Sales Order', 'stock': 'Stock Entry', 'manufacturing': 'Work Order'}[module]
            assert all(row['source_type'] == expected for row in scoped['approvals'])
        checks.append('数据总览提供四模块；模块数据API只读取本模块，看板和审批不混入其他模块')
        case = frappe.get_doc('Supply Case', 'SUP-CASE-00001')
        po = frappe.get_doc('Purchase Order', case.purchase_order)
        offer = hub.draft_offer('stock', 'receive_purchase', po.name)
        turn = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                              'agent_module': 'stock', 'question': '收货', 'state': 'done',
                              'result': json.dumps({'offers': [offer]})}).insert(ignore_permissions=True)
        names.append(turn.name)
        before = frappe.db.count('Purchase Receipt')
        draft = hub.open_draft(turn.name, 0)
        assert draft['doctype'] == 'Purchase Receipt' and draft['__islocal']
        assert frappe.db.count('Purchase Receipt') == before
        checks.append('Agent打开真实收货映射草稿，不自动插入或提交单据')
        assert hub.read_record('stock', 'Purchase Order', po.name)['name'] == po.name
        checks.append('库存Agent能读取待收货采购单，但只开放收货映射')
        original_get = frappe.get_cached_value
        def serial_item(doctype, name, field, *args, **kwargs):
            if doctype == 'Item' and field == ['has_serial_no', 'has_batch_no']:
                return [1, 0]
            return original_get(doctype, name, field, *args, **kwargs)
        bundles = frappe.db.count('Serial and Batch Bundle')
        with patch('frappe.get_cached_value', serial_item):
            try:
                hub.open_draft(turn.name, 0)
                raise AssertionError('序列号映射副作用未阻止')
            except frappe.ValidationError:
                pass
        assert frappe.db.count('Serial and Batch Bundle') == bundles
        checks.append('序列号和批次物料转原生手动路径，草稿入口不插入Bundle')
        offer['modified'] = 'old-version'
        turn.result = json.dumps({'offers': [offer]})
        turn.save(ignore_permissions=True)
        try:
            hub.open_draft(turn.name, 0)
            raise AssertionError('陈旧草稿未被拒绝')
        except frappe.ValidationError:
            pass
        checks.append('源单据版本变化后拒绝打开旧草稿')
        try:
            hub.draft_offer('selling', 'receive_purchase', po.name)
            raise AssertionError('跨模块工具越权未拒绝')
        except frappe.PermissionError:
            pass
        checks.append('销售Agent不能调用库存模块收货工具')
        frappe.db.commit()
        frappe.set_user(user)
        try:
            hub.poll(turn.name)
            raise AssertionError('其他用户会话泄漏')
        except frappe.PermissionError:
            pass
        assert not hub.history(turn.conversation)
        assert hub.module_board('buying')['metrics'][0]['value'] is None
        try:
            hub.draft_offer('stock', 'receive_purchase', po.name)
            raise AssertionError('无业务权限用户获得草稿')
        except frappe.PermissionError:
            pass
        checks.append('会话按用户隔离，无业务权限者不可查看订单或准备草稿')
        frappe.set_user('Administrator')
        turn2 = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                               'agent_module': 'control', 'question': '分别读取四模块', 'state': 'queued'}).insert(ignore_permissions=True)
        names.append(turn2.name)
        frappe.db.commit()

        def provider(system, messages, tools):
            # 总控真实走四次委派，每个模块走自己的面板工具。
            has_results = any(isinstance(message['content'], list) and any(block.get('type') == 'tool_result' for block in message['content']) for message in messages)
            if has_results:
                return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已根据实际工具整理结果，待负责人处理。'}]}
            if tools[0]['name'] == 'ask_module':
                return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': module, 'name': 'ask_module',
                         'input': {'module': module, 'question': '读取本模块面板'}} for module in hub.MODULE_DOCS]}
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'board', 'name': 'read_module_board', 'input': {}}]}

        with patch('suke_ops.agent_runtime.request_model', provider):
            hub.run_turn(turn2.name)
        result = hub.poll(turn2.name)
        assert result['state'] == 'done', result
        assert {row['agent'] for row in result['trace']} == {'control', *hub.MODULE_DOCS}
        assert len(result['trace']) == 8
        checks.append('总控实际委派四模块，四个独立Agent均调用各自业务工具（模型响应模拟）')
        repeated = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                                   'agent_module': 'control', 'question': '重复委派预算验证', 'state': 'queued'}).insert(ignore_permissions=True)
        names.append(repeated.name)
        frappe.db.commit()
        buying_requests = []
        def repeated_provider(system, messages, tools):
            if '你是采购 Agent' in system:
                buying_requests.append(1)
            output = provider(system, messages, tools)
            if tools[0]['name'] == 'ask_module' and output['stop_reason'] == 'tool_use':
                output['content'].append({'type': 'tool_use', 'id': 'buying-again', 'name': 'ask_module',
                                          'input': {'module': 'buying', 'question': '换个问法再查采购'}})
            return output
        with patch('suke_ops.agent_runtime.request_model', repeated_provider):
            hub.run_turn(repeated.name)
        assert hub.poll(repeated.name)['state'] == 'done'
        assert len(buying_requests) == 2, buying_requests
        checks.append('总控换问法重复委派同一模块时复用摘要，不额外消耗子模块模型预算')
        partial = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                                  'agent_module': 'control', 'question': '部分模块失败时保留结果', 'state': 'queued'}).insert(ignore_permissions=True)
        names.append(partial.name)
        frappe.db.commit()
        def partial_provider(system, messages, tools):
            if '你是销售 Agent' in system:
                raise RuntimeError('合成测试：销售服务暂不可用')
            if tools[0]['name'] == 'ask_module' and len(messages) > 1:
                raise RuntimeError('合成测试：汇总失败')
            return provider(system, messages, tools)
        with patch('suke_ops.agent_runtime.request_model', partial_provider):
            hub.run_turn(partial.name)
        partial_result = hub.poll(partial.name)
        assert partial_result['state'] == 'done', partial_result
        assert '销售：本轮未核实' in partial_result['answer']
        assert '采购：已根据实际工具' in partial_result['answer']
        assert any(row['tool'] == 'module_failed' for row in partial_result['trace'])
        checks.append('子模块失败不阻断其他模块，汇总失败保留已完成结论并标注未核实模块')
        report_turn = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                                     'agent_module': 'stock', 'question': '库存巡检', 'answer': '合成验收：库存问题需要总控协调', 'state': 'done'}).insert(ignore_permissions=True)
        names.append(report_turn.name)
        frappe.db.commit()
        assert any(row['turn'] == report_turn.name for row in hub.latest_reports())
        control_report = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': str(uuid.uuid4()),
                                        'agent_module': 'control', 'question': '研判模块上报', 'state': 'queued'}).insert(ignore_permissions=True)
        names.append(control_report.name)
        frappe.db.commit()
        def report_provider(system, messages, tools):
            if len(messages) == 1:
                return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'reports', 'name': 'read_module_reports', 'input': {}}]}
            payload = json.loads(messages[-1]['content'][0]['content'])
            assert any(row['turn'] == report_turn.name for row in payload['reports'])
            assert len(payload['current_boards']) == 4
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '已读取库存上报并核对四模块面板，建议负责人协调。'}]}
        with patch('suke_ops.agent_runtime.request_model', report_provider):
            hub.run_turn(control_report.name)
        assert hub.poll(control_report.name)['state'] == 'done'
        checks.append('总控通过真实工具读取模块上报及四模块当前面板，生成协调建议')
        board = hub.module_board('stock')
        assert any(section['doctype'] == 'Quality Inspection' for section in board['sections'])
        frappe.set_user(user)
        assert not any(row['turn'] == report_turn.name for row in hub.latest_reports())
        frappe.set_user('Administrator')
        checks.append('模块完成分析自动进入总控汇报，用户隔离生效，库存看板提供质检数据')
        stages = hub.dashboard('buying')['stages']
        assert len(stages) == 6 and {row['module'] for row in stages} == set(hub.STAGE_DOCS)
        assert next(row for row in stages if row['module'] == 'buying-order')['metrics'][1]['value'] == hub.module_board('buying')['metrics'][0]['value']
        assert next(row for row in stages if row['module'] == 'buying-order')['metrics'][3]['value'] == hub.module_board('buying')['metrics'][1]['value']
        assert all(row['columns'] and row['metrics'] for row in stages)
        checks.append('采购六环节提供真实进度与对应明细，采购待收货指标与模块概览一致')
        stage_conversation = str(uuid.uuid4())
        for module in hub.STAGE_DOCS:
            stage_turn = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': stage_conversation,
                                        'agent_module': module, 'question': '读取本环节当前进度', 'state': 'queued'}).insert(ignore_permissions=True)
            names.append(stage_turn.name)
            frappe.db.commit()
            def stage_provider(system, messages, tools):
                assert '你是' + hub.runtime.MODULES[module] + ' Agent' in system
                assert not any(tool['name'] in ('ask_module', 'offer_draft') for tool in tools)
                allowed = next(tool for tool in tools if tool['name'] == 'read_record')['input_schema']['properties']['doctype']['enum']
                assert set(allowed) == set(hub.STAGE_DOCS[module] + hub.SHARED_DOCS)
                assert 'Sales Order' not in allowed
                assert len(messages) == 1 or isinstance(messages[-1]['content'], list), '不应读取同会话其他专项的历史'
                if len(messages) == 1:
                    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'stage', 'name': 'read_module_board', 'input': {}}]}
                payload = json.loads(messages[-1]['content'][0]['content'])
                assert payload['module'] == module and payload['columns']
                return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': module + '已核对真实环节数据，待负责人决策。'}]}
            with patch('suke_ops.agent_runtime.request_model', stage_provider):
                hub.run_turn(stage_turn.name)
            assert hub.poll(stage_turn.name)['state'] == 'done'
            restored_stage = hub.history(stage_conversation, module)
            assert len(restored_stage) == 1 and restored_stage[0]['agent_module'] == module
        assert set(hub.STAGE_DOCS).issubset({row['module'] for row in hub.latest_reports()})
        checks.append('六专项Agent分别调用本环节工具、独立职责和历史，上报可被总控读取（模拟模型）')
        try:
            hub.read_record('buying-demand', 'Sales Order', 'not-allowed')
            raise AssertionError('专项越权未被阻止')
        except frappe.PermissionError:
            pass
        frappe.set_user(user)
        assert all(metric['value'] is None for board in hub.dashboard('buying')['stages'] for metric in board['metrics'])
        assert not any(row['module'] in hub.STAGE_DOCS for row in hub.latest_reports())
        frappe.set_user('Administrator')
        checks.append('采购专项工具越权被拒绝，无权限账号不泄漏统计或其他账号专项汇报')
        conversation = str(uuid.uuid4())
        for index in range(51):
            history_turn = frappe.get_doc({'doctype': 'Agent Turn', 'conversation': conversation,
                                           'agent_module': 'stock', 'question': str(index), 'state': 'done'}).insert(ignore_permissions=True)
            names.append(history_turn.name)
        restored = hub.history(conversation)
        assert len(restored) == 50 and restored[-1]['question'] == '50' and restored[0]['question'] == '1'
        checks.append('超过50轮的对话恢复最近50轮，保留最新问题')
        checks.append(check_concurrency(user))
        frappe.set_user('Guest')
        try:
            hub.dashboard()
            raise AssertionError('Guest访问面板')
        except frappe.PermissionError:
            pass
        checks.append('未登录用户不能访问面板或对话API')
    finally:
        frappe.set_user('Administrator')
        for name in names:
            frappe.delete_doc('Agent Turn', name, ignore_permissions=True)
        if not existing_user:
            frappe.delete_doc('User', user, ignore_permissions=True)
        frappe.db.commit()
    result = {'status': 'PASS', 'count': len(checks), 'checks': checks,
              'mode': '真实ERP权限、数据库与映射；模型响应模拟', 'at': str(frappe.utils.now_datetime())}
    Path(frappe.get_site_path('private', 'files', 'hub-verification.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def check_concurrency(user):
    """两个独立数据库连接同时发送，验证真正的事务锁而非模拟互斥。"""
    import threading
    site = frappe.local.site
    sites_path = frappe.local.sites_path
    conversation = str(uuid.uuid4())
    outcomes = []
    barrier = threading.Barrier(2)
    frappe.db.commit()

    def send():
        try:
            frappe.init(site=site, sites_path=sites_path)
            frappe.connect()
            frappe.set_user(user)
            barrier.wait(timeout=10)
            result = hub.start('并发合成验收', module='stock', conversation=conversation)
            frappe.db.commit()
            outcomes.append(('created', result['name']))
        except frappe.ValidationError:
            frappe.db.rollback()
            outcomes.append(('rejected', None))
        except Exception as error:
            frappe.db.rollback()
            outcomes.append((type(error).__name__, None))
        finally:
            frappe.destroy()

    # 只验证入队前的并发边界，不发送模型请求。
    with patch('frappe.enqueue'), patch('suke_ops.agent_runtime.configured', return_value=True):
        threads = [threading.Thread(target=send) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    try:
        assert sorted(row[0] for row in outcomes) == ['created', 'rejected'], outcomes
        assert not any(thread.is_alive() for thread in threads)
    finally:
        for name in frappe.get_all('Agent Turn', filters={'owner': user, 'conversation': conversation}, pluck='name'):
            frappe.delete_doc('Agent Turn', name, ignore_permissions=True)
        frappe.db.commit()
    return '同账号两个独立连接并发发送，只创建一个待处理任务'
