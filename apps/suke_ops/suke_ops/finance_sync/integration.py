"""仅在finance-*隔离站点执行，全部为合成数据；保留记录方便复查。"""
import json
import uuid
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from . import api, core, service
from .connectors import MockConnector


def run():
    if not frappe.local.site.startswith('finance-'):
        raise RuntimeError('只允许在finance-*独立验收站点执行，不在现有业务站点运行')
    original = frappe.session.user
    frappe.set_user('Administrator')
    checks = []
    marker = 'finance-' + uuid.uuid4().hex[:8]
    companies = frappe.get_all('Company', pluck='name', limit_page_length=1)
    if not companies:
        from erpnext.focus_boundary import initialize_demo
        initialize_demo()
        companies = frappe.get_all('Company', pluck='name', limit_page_length=1)
    company = companies[0]
    baseline = {dt: frappe.db.count(dt) for dt in ('GL Entry', 'Stock Ledger Entry', 'Purchase Invoice', 'Payment Entry')}

    def denied(call):
        try:
            call()
        except frappe.PermissionError:
            frappe.clear_messages()
            return
        raise AssertionError('应拒绝的权限操作被放行')

    def connection(entity=None, page_size=2):
        name = api.create_demo(company)['name']
        doc = frappe.get_doc('ERP Connection', name)
        doc.title = marker
        doc.page_size = page_size
        if entity:
            doc.set('bindings', [b for b in doc.bindings if b.entity == entity])
        doc.save()
        frappe.db.commit()
        return name

    try:
        main = connection()
        first = service.run_connection(main)
        assert first['state'] == 'success', first
        assert frappe.db.count('External ERP Snapshot', {'connection': main}) == 15
        for _ in range(2):
            assert service.run_connection(main, full=True)['state'] == 'success'
        assert frappe.db.count('External ERP Snapshot', {'connection': main}) == 15
        rows = api.records(main, 'payable', page_length=1)
        assert rows['total'] == 3 and len(rows['rows']) == 1 and rows['synthetic']
        assert rows['summary_by_currency']['CNY']['outstanding'] == '0.30'
        assert rows['summary_by_currency']['CNY']['amount'] == '0.30'
        assert rows['summary_by_currency']['CNY']['count'] == 2
        assert rows['verified_current'] and rows['summary_complete']
        assert api.records(main, 'payable', q='NO-MATCH')['total'] == 0
        assert len(api.records(main, 'payable', offset=2, page_length=1)['rows']) == 1
        checks.append('五对象落库、跨页查询、三次全量幂等、取消排除和0.10+0.20精确汇总')

        class Changed(MockConnector):
            def fetch(self, entity, since, until, offset, limit):
                batch = super().fetch(entity, since, until, offset, limit)
                for row in batch:
                    if row['source_id'] == '1':
                        row.update(amount='0.40', outstanding='0.40', modified_at='2026-10-30T01:00:00+00:00')
                return batch
        factory = service.make_connector
        with patch.object(service, 'make_connector', side_effect=lambda d, b: Changed('100') if b.entity == 'payable' else factory(d, b)):
            assert service.run_connection(main, full=True)['state'] == 'success'
        assert api.records(main)['summary_by_currency']['CNY']['outstanding'] == '0.60'
        replay = service.run_connection(main, full=True)
        assert next(r for r in replay['results'] if r['entity'] == 'payable')['older'] == 1
        assert api.records(main)['summary_by_currency']['CNY']['outstanding'] == '0.60'
        checks.append('源修改更新现有快照，旧版本重放不能覆盖较新金额')

        failed = connection('payable', page_size=1)
        class Interrupted(MockConnector):
            def fetch(self, entity, since, until, offset, limit):
                if offset:
                    raise core.SyncError('模拟接口中断，安全错误')
                return super().fetch(entity, since, until, offset, limit)
        with patch.object(service, 'make_connector', return_value=Interrupted('100')):
            failure = service.run_connection(failed)
        assert failure['state'] == 'failed', failure
        assert frappe.db.count('External ERP Snapshot', {'connection': failed}) == 0
        b = frappe.get_doc('ERP Connection', failed).bindings[0]
        assert not b.cursor and b.last_error
        assert not api.records(failed, 'payable')['summary_complete']
        assert service.run_connection(failed)['state'] == 'success'
        assert frappe.db.count('External ERP Snapshot', {'connection': failed}) == 3
        checks.append('第二页失败回滚第一页与游标，保留错误；重试成功且不丢单')

        # 供应商对象可独立成功，应付失败不抹掉其他对象已完成的进度。
        partial = connection()
        original_factory = service.make_connector
        def fail_payable(d, b):
            return Interrupted('100') if b.entity == 'payable' else original_factory(d, b)
        with patch.object(service, 'make_connector', side_effect=fail_payable):
            result = service.run_connection(partial)
        assert result['state'] == 'partial', result
        assert not frappe.get_doc('ERP Connection', partial).bindings[3].cursor
        assert frappe.db.count('External ERP Snapshot', {'connection': partial, 'entity': 'supplier'}) == 3
        checks.append('实体独立事务，部分成功和失败状态可追溯')

        snapshot = rows['rows'][0]['name']
        doc = frappe.get_doc('External ERP Snapshot', snapshot)
        doc.payload = '{}'
        doc.flags.erp_sync_write = True
        denied(lambda: doc.save(ignore_permissions=True))
        frappe.db.rollback()
        assert 'amount' in json.loads(frappe.get_doc('External ERP Snapshot', snapshot).payload)
        checks.append('原生保存即使伪造flags也不能篡改同步快照')

        users = []
        for suffix in ('allowed', 'denied'):
            email = marker + '-' + suffix + '@example.invalid'
            frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': '合成财务权限验收',
                            'send_welcome_email': 0, 'user_type': 'System User',
                            'roles': [{'role': 'Purchase User'}]}).insert()
            users.append(email)
        d = frappe.get_doc('ERP Connection', main)
        d.append('access_users', {'user': users[0], 'allow_ai': 0})
        d.save()
        frappe.db.commit()
        frappe.set_user(users[0])
        assert api.records(main)['total'] == 3
        assert api.detail(snapshot)['connection'] == main
        assert len(api.connections()['connections']) == 1
        assert not frappe.get_list('External ERP Snapshot', fields=['name'])
        denied(lambda: api.records(failed))
        denied(lambda: api.sync_now(main))
        denied(lambda: api.agent_call('read_external_finance', {'connection': main, 'entity': 'payable'}))
        assert not api.agent_call('list_external_connections', {})['connections']
        frappe.set_user(users[1])
        assert not api.connections()['connections']
        denied(lambda: api.records(main))
        denied(lambda: api.detail(snapshot))
        frappe.set_user('Guest')
        denied(api.connections)
        denied(lambda: api.records(main))
        checks.append('显式账套授权约束列表、详情、计数、分页和普通查询；Guest及未授权用户被拒绝')

        frappe.set_user('Administrator')
        d = frappe.get_doc('ERP Connection', main)
        d.allow_ai = 1
        d.save()
        frappe.db.commit()
        frappe.set_user(users[0])
        denied(lambda: api.agent_call('read_external_finance', {'connection': main, 'entity': 'payable'}))
        frappe.set_user('Administrator')
        d = frappe.get_doc('ERP Connection', main)
        d.access_users[0].allow_ai = 1
        d.save()
        frappe.db.commit()
        frappe.set_user(users[0])
        assert api.agent_call('read_external_finance', {'connection': main, 'entity': 'payable'})['synthetic']
        assert len(api.agent_call('list_external_connections', {})['connections']) == 1
        checks.append('页面权限与模型权限分离，连接及用户两项模型授权均须通过')

        frappe.set_user('Administrator')
        d = frappe.get_doc('ERP Connection', main)
        # 用服务端受控写模拟时效变化，不修改来源业务记录。
        d.bindings[3].last_success = now_datetime().replace(year=2025)
        service.save_internal(d)
        frappe.db.commit()
        assert api.records(main)['stale'] and not api.records(main)['verified_current']
        checks.append('过期快照保留来源记录但不宣称当前已核实')

        live = frappe.get_doc('ERP Connection', main)
        try:
            service.live_sdk(live)
        except core.SyncError:
            pass
        else:
            raise AssertionError('默认关闭的真实接口被访问')
        with patch.dict(frappe.conf, {'erp_finance_live_enabled': 'false'}):
            try:
                service.live_sdk(live)
            except core.SyncError:
                pass
            else:
                raise AssertionError('字符串false不得启用真实接口')
        live.source_org = 'OTHER'
        try:
            live.save()
        except frappe.ValidationError:
            frappe.clear_messages()
            frappe.db.rollback()
        else:
            raise AssertionError('已有快照的来源身份不得更换')
        checks.append('已同步来源身份不可篡改，字符串false不能绕过真实接口关闭开关')
        from suke_ops import agent_runtime, hub
        assert 'read_external_finance' in {r['name'] for r in hub.module_tools('buying')}
        assert 'read_external_finance' not in {r['name'] for r in hub.module_tools('selling')}
        assert 'read_external_finance' in {r['name'] for r in api.agent_tools(agent_runtime)}
        assert frappe.db.exists('Page', 'erp-connections')
        checks.append('真实SDK默认不可调用；采购工具已注册、销售工具不越权，页面已安装')
        assert {dt: frappe.db.count(dt) for dt in baseline} == baseline
        checks.append('同步前后原生总账、库存账、采购发票、付款单数量均不变')
        return {'status': 'PASS', 'checks': checks, 'count': len(checks),
                'connection': main, 'site': frappe.local.site, 'synthetic_only': True,
                'real_kingdee_contacted': False, 'original_ledger_counts': baseline}
    finally:
        frappe.db.rollback()
        frappe.set_user(original)
