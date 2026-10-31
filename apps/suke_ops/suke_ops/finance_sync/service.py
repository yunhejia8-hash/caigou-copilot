"""同步存储与权限；仅管理扩展快照，不调用任何原生财务控制器。"""
import ipaddress
import json
import re
import socket
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import frappe
from frappe.utils import cint, now_datetime

from . import core
from .connectors import connector_for
from .core import SyncError

_writing = ContextVar('erp_finance_internal_write', default=False)


def manager(user=None):
    user = user or frappe.session.user
    return user == 'Administrator' or 'System Manager' in frappe.get_roles(user)


def require_manager():
    from suke_ops.business_search import require_user
    require_user()
    if not manager():
        frappe.throw('只有系统管理员可以配置或同步企业数据', frappe.PermissionError)


def authorize(name, ai=False):
    from suke_ops.business_search import require_user
    require_user()
    doc = frappe.get_doc('ERP Connection', name)
    user = frappe.session.user
    allowed = next((r for r in doc.access_users if r.user == user), None)
    if not manager() and not allowed:
        frappe.throw('没有此账套的查询授权', frappe.PermissionError)
    frappe.get_doc('Company', doc.company).check_permission('read')
    if ai and (not doc.allow_ai or not allowed or not allowed.allow_ai):
        frappe.throw('此连接或用户未授权模型使用财务数据，请在企业数据页查询', frappe.PermissionError)
    return doc


def private_query(user=None):
    # 原生列表/导出不暴露快照；统一使用受控分页API，详情还需逐连接权限。
    return '1=0'


def document_permission(doc, ptype='read', user=None, **kwargs):
    if ptype != 'read' or (user and user != frappe.session.user):
        return False
    try:
        connection = authorize(doc.connection)
        # Frappe 16 将钩子的None视为拒绝；True后仍继续原生角色和User Permission检查。
        return doc.company == connection.company
    except (frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return False


def guard(doc, method=None):
    if not _writing.get():
        frappe.throw('外部快照和任务仅允许同步服务写入', frappe.PermissionError)


def validate_connection(doc, method=None):
    require_manager()
    core.source_zone(doc.source_timezone)
    if (doc.vendor not in ('kingdee', 'yonyou', 'sap') or doc.mode not in ('mock', 'live')
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', str(doc.source_org or ''))
            or not 1 <= cint(doc.page_size) <= 500 or not 15 <= cint(doc.stale_minutes) <= 10080):
        frappe.throw('连接参数无效；组织用编码，每页1–500条，过期阈值15–10080分钟')
    frappe.get_doc('Company', doc.company).check_permission('read')
    if doc.enabled and doc.vendor != 'kingdee':
        frappe.throw('用友和SAP仅预留扩展，尚不能启用')
    previous = doc.get_doc_before_save()
    identity = ('vendor', 'mode', 'company', 'account_id', 'source_org', 'source_timezone')
    completed = previous and any(r.cursor for r in previous.bindings)
    if completed and any(previous.get(f) != doc.get(f) for f in identity):
        frappe.throw('已有同步记录，不能改变来源身份；请新建独立连接')
    entities = set()
    old = {r.entity: r for r in previous.bindings} if previous else {}
    for b in doc.bindings:
        if b.entity not in core.ENTITIES or b.entity in entities:
            frappe.throw('同步对象无效或重复')
        entities.add(b.entity)
        if doc.mode == 'live' and b.enabled:
            if not b.confirmed:
                frappe.throw('真实接口需先核对各对象的字段和财务口径')
            core.validate_binding(b.entity, json.loads(b.mapping_json or '{}'))
        prior = old.get(b.entity)
        if prior and prior.cursor and prior.mapping_json != b.mapping_json:
            frappe.throw('已同步对象不能直接改变粒度和字段映射，请另建连接验证')
        if not _writing.get() and any(str(b.get(f) or '') != str((prior.get(f) if prior else None) or '')
                                                for f in ('cursor', 'last_success', 'last_error')):
            frappe.throw('同步进度只能由同步服务更新')
    if any(b.cursor and b.entity not in entities for b in old.values()):
        frappe.throw('已有同步进度的对象不能移除，可取消启用')
    users = set()
    for access in doc.access_users:
        if access.user in users or access.user == 'Guest':
            frappe.throw('授权用户重复或无效')
        users.add(access.user)
        if frappe.get_cached_value('User', access.user, 'user_type') != 'System User':
            frappe.throw('只能授权企业内部账号')


def save_internal(doc):
    token = _writing.set(True)
    try:
        return doc.save(ignore_permissions=True)
    finally:
        _writing.reset(token)


def live_sdk(connection):
    """双开关默认关闭；主机需由部署方加入允许列表，拒绝内网地址和凭据URL。"""
    if frappe.conf.get('erp_finance_live_enabled') is not True:
        raise SyncError('站点真实接口开关未启用；当前仅可使用模拟连接')
    url = urlparse(connection.server_url or '')
    hosts = frappe.conf.get('erp_finance_hosts') or []
    if (not isinstance(hosts, list) or url.scheme != 'https' or not url.hostname or url.hostname not in hosts
            or url.username or url.password or url.query or url.fragment or url.port not in (None, 443)):
        raise SyncError('API地址必须为部署侧允许的HTTPS主机')
    try:
        addresses = socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(r[4][0]).is_global for r in addresses):
            raise ValueError
    except (OSError, ValueError):
        raise SyncError('API主机解析失败或指向非公网地址') from None
    if not all((connection.account_id, connection.app_id, connection.app_user, connection.get_password('app_secret', raise_exception=False))):
        raise SyncError('真实连接缺少授权配置')
    try:
        from k3cloud_webapi_sdk.main import K3CloudApiSdk
    except ImportError:
        raise SyncError('尚未安装金蝶官方Python SDK；请在真实联调时固定并安装官方版本') from None
    try:
        sdk = K3CloudApiSdk(connection.server_url, timeout=30)
        sdk.InitConfig(connection.account_id, connection.app_user, connection.app_id,
                       connection.get_password('app_secret'), server_url=connection.server_url,
                       org_num=connection.source_org, connect_timeout=10, request_timeout=30)
        return sdk
    except Exception:
        raise SyncError('金蝶SDK初始化失败，请检查所安装版本和授权配置') from None


def make_connector(connection, binding):
    sdk = live_sdk(connection) if connection.mode == 'live' else None
    mapping = json.loads(binding.mapping_json or '{}')
    return connector_for(connection.vendor, connection.mode, connection.source_org,
                         connection.source_timezone, sdk=sdk, binding=mapping)


def upsert(connection, row):
    key = core.fingerprint([connection.name, connection.account_id, connection.source_org,
                            row['entity'], row['source_id'], row['line_id']])
    content_hash = core.fingerprint(row)
    current = frappe.get_doc('External ERP Snapshot', key) if frappe.db.exists('External ERP Snapshot', key) else None
    if current and current.source_modified > row['modified_at']:
        return 'older'
    if current and current.content_hash == content_hash:
        return 'unchanged'
    doc = current or frappe.new_doc('External ERP Snapshot')
    doc.update({'connection': connection.name, 'company': connection.company, 'entity': row['entity'],
                'source_id': row['source_id'], 'line_id': row['line_id'], 'number': row['number'],
                'source_org': connection.source_org, 'source_modified': row['modified_at'],
                'state': row['state'], 'synthetic': int(connection.mode == 'mock'),
                'supplier_code': row['supplier_code'], 'item_code': row['item_code'], 'currency': row['currency'],
                'content_hash': content_hash, 'synced_at': now_datetime(), 'payload': core.dump(row)})
    if not current:
        doc.name = key
        doc.flags.name_set = True
    save_internal(doc)
    return 'updated' if current else 'inserted'


def create_run(connection, full=False):
    doc = frappe.get_doc({'doctype': 'ERP Sync Run', 'connection': connection, 'state': 'queued',
                         'requested_by': frappe.session.user, 'full_scan': int(bool(full))})
    return save_internal(doc)


def run_connection(connection, run=None, full=False):
    """每个实体事务提交；失败实体回滚，下一次从原窗口重试。"""
    require_manager()
    lock = 'erp-finance-' + core.fingerprint(connection)[:48]
    run_doc = frappe.get_doc('ERP Sync Run', run) if run else create_run(connection, full)
    if run_doc.connection != connection:
        frappe.throw('任务与连接不匹配')
    if run_doc.state != 'queued':
        return {'run': run_doc.name, 'state': run_doc.state}
    acquired = frappe.db.sql('SELECT GET_LOCK(%s, 0)', lock)[0][0]
    if not acquired:
        run_doc.state = 'skipped'
        run_doc.result_json = core.dump({'reason': '同一连接已有同步在执行'})
        save_internal(run_doc)
        frappe.db.commit()
        return {'state': 'skipped', 'run': run_doc.name}
    results, state = [], 'failed'
    try:
        run_doc.state, run_doc.started_at = 'running', now_datetime()
        save_internal(run_doc)
        frappe.db.commit()
        connection_doc = frappe.get_doc('ERP Connection', connection)
        if not connection_doc.enabled:
            raise SyncError('连接已停用')
        # 留出两分钟源端写入延迟；时间统一存UTC，查询时转回配置的源偏移。
        until = core.timestamp(datetime.now(timezone.utc) - timedelta(minutes=2))
        bindings = [b.entity for b in connection_doc.bindings if b.enabled]
        if not bindings:
            raise SyncError('没有启用的同步对象')
        for entity in bindings:
            try:
                # 锁住配置直到本实体提交，禁止同步中途更换授权、范围或字段。
                frappe.db.sql('SELECT name FROM `tabERP Connection` WHERE name=%s FOR UPDATE', connection)
                connection_doc = frappe.get_doc('ERP Connection', connection)
                if not connection_doc.enabled:
                    raise SyncError('连接已停用')
                b = next(r for r in connection_doc.bindings if r.entity == entity)
                if not b.enabled:
                    raise SyncError('同步对象已停用')
                if connection_doc.mode == 'live':
                    if not b.confirmed:
                        raise SyncError('真实字段与口径尚未核对')
                    core.validate_binding(entity, json.loads(b.mapping_json or '{}'))
                since = None if full or not b.cursor else core.timestamp(datetime.fromisoformat(b.cursor) - timedelta(minutes=10))
                counts = dict(inserted=0, updated=0, unchanged=0, older=0)
                def write(row):
                    counts[upsert(connection_doc, row)] += 1
                result = core.synchronize(make_connector(connection_doc, b), entity, connection_doc.source_org,
                                          connection_doc.source_timezone, since, until, write,
                                          page_size=cint(connection_doc.page_size))
                b.cursor, b.last_success, b.last_error = result['checkpoint'], now_datetime(), None
                save_internal(connection_doc)
                result = {'entity': entity, 'state': 'success', **result, **counts}
                frappe.db.commit()
            except Exception as error:
                frappe.db.rollback()
                # 仅保留本模块受控错误；任何厂商/DB异常都不落原始字符串或trace。
                message = str(error) if isinstance(error, core.SyncError) else '同步失败，请核对字段、权限或网络后重试'
                connection_doc = frappe.get_doc('ERP Connection', connection)
                b = next(r for r in connection_doc.bindings if r.entity == entity)
                b.last_error = message[:300]
                save_internal(connection_doc)
                result = {'entity': entity, 'state': 'failed', 'error': message[:300]}
                frappe.db.commit()
            results.append(result)
            run_doc = frappe.get_doc('ERP Sync Run', run_doc.name)
            run_doc.result_json = core.dump(results)
            save_internal(run_doc)
            frappe.db.commit()
        state = 'success' if all(r['state'] == 'success' for r in results) else (
            'partial' if any(r['state'] == 'success' for r in results) else 'failed')
    except Exception:
        frappe.db.rollback()
        state = 'failed'
        results.append({'state': 'failed', 'error': '连接不可用，请检查是否启用、字段配置和官方SDK'})
    finally:
        run_doc = frappe.get_doc('ERP Sync Run', run_doc.name)
        run_doc.state, run_doc.finished_at, run_doc.result_json = state, now_datetime(), core.dump(results)
        save_internal(run_doc)
        frappe.db.commit()
        frappe.db.sql('SELECT RELEASE_LOCK(%s)', lock)
    return {'run': run_doc.name, 'state': state, 'results': results}


def scheduled():
    if not frappe.db.exists('DocType', 'ERP Connection'):
        return
    for name in frappe.get_all('ERP Connection', filters={'enabled': 1, 'auto_sync': 1}, pluck='name'):
        enqueue(name)


def enqueue(connection, full=False):
    require_manager()
    doc = authorize(connection)
    if not doc.enabled:
        frappe.throw('请先启用连接')
    run = create_run(connection, full)
    frappe.enqueue('suke_ops.finance_sync.service.run_connection', connection=connection, run=run.name,
                   full=bool(full), queue='long', timeout=900, enqueue_after_commit=True)
    return {'run': run.name, 'state': 'queued', 'synthetic': doc.mode == 'mock'}
