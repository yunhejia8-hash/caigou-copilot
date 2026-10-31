"""企业数据页与Agent共用查询服务；权限先于搜索、分页和精确汇总。"""
import json
from datetime import timedelta
from decimal import Decimal, localcontext

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from . import core, service


@frappe.whitelist()
def connections():
    from suke_ops.business_search import require_user
    require_user()
    if not frappe.db.exists('DocType', 'ERP Connection'):
        return {'installed': False, 'connections': [], 'can_manage': service.manager()}
    result = []
    for name in frappe.get_all('ERP Connection', pluck='name', order_by='creation asc'):
        try:
            d = service.authorize(name)
        except frappe.PermissionError:
            frappe.clear_messages()
            continue
        result.append({'name': d.name, 'title': d.title, 'company': d.company, 'vendor': d.vendor,
                       'mode': d.mode, 'synthetic': d.mode == 'mock', 'enabled': bool(d.enabled),
                       'auto_sync': bool(d.auto_sync), 'allow_ai': bool(d.allow_ai),
                       'account_id': d.account_id, 'source_org': d.source_org,
                       'source_timezone': d.source_timezone,
                       'entities': [{'entity': b.entity, 'label': core.ENTITIES[b.entity], 'enabled': bool(b.enabled),
                                     'cursor': b.cursor, 'last_success': str(b.last_success or ''),
                                     'last_error': b.last_error} for b in d.bindings]})
    return {'installed': True, 'connections': result, 'can_manage': service.manager(), 'entities': core.ENTITIES}


def query(connection, entity='payable', q='', offset=0, page_length=20, ai=False):
    d = service.authorize(connection, ai=ai)
    if entity not in core.ENTITIES or not isinstance(q, str) or len(q) > 100:
        frappe.throw('无效实体或搜索词（最多100字）')
    # 与同步配置行互斥，确保本次分页、计数和汇总来自同一已提交实体版本。
    frappe.db.sql('SELECT name FROM `tabERP Connection` WHERE name=%s LOCK IN SHARE MODE', connection)
    offset, page_length = cint(offset), cint(page_length)
    if offset < 0 or offset > 100000 or not 1 <= page_length <= 100:
        frappe.throw('无效分页范围')
    binding = next((b for b in d.bindings if b.entity == entity), None)
    if not binding:
        frappe.throw('连接未配置该同步对象')
    filters = {'connection': d.name, 'company': d.company, 'source_org': d.source_org, 'entity': entity}
    # 仅显式授权后用get_all读取镜像；原生列表被权限钩子关闭。
    or_filters = [[f, 'like', '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%']
                  for f in ('number', 'source_id', 'supplier_code', 'item_code')] if q else None
    columns = ['name', 'number', 'source_id', 'line_id', 'source_modified', 'state', 'synced_at', 'payload']
    rows = frappe.get_all('External ERP Snapshot', filters=filters, or_filters=or_filters, fields=columns,
                          order_by='source_modified desc, name asc', limit_start=offset, limit_page_length=page_length)
    counts = frappe.get_all('External ERP Snapshot', filters=filters, or_filters=or_filters,
                            fields=[{'COUNT': 'name', 'as': 'total'}])
    total = cint(counts[0].total) if counts else 0
    stale = not binding.last_success or now_datetime() - get_datetime(binding.last_success) > timedelta(minutes=d.stale_minutes)
    summary, summary_complete = {}, bool(binding.last_success)
    if entity in ('payable', 'payment', 'purchase_order'):
        # ponytail: 首版精确扫描最多一万条匹配记录；超量时明确停止汇总，后续改为DECIMAL数据库聚合。
        if total > 10000:
            summary_complete = False
        else:
            start = 0
            while True:
                batch = frappe.get_all('External ERP Snapshot', filters=filters, or_filters=or_filters,
                                       fields=['state', 'payload'], order_by='name asc', limit_start=start, limit_page_length=500)
                with localcontext() as precision:
                    # 输入最多30位有效数，加一万条汇总仍保留精度。
                    precision.prec = 40
                    for record in batch:
                        if record.state != 'active':
                            continue
                        payload = json.loads(record.payload)
                        currency = payload['currency']
                        bucket = summary.setdefault(currency, {'amount': Decimal(0), 'outstanding': Decimal(0),
                                                                'count': 0, 'outstanding_known': True})
                        bucket['count'] += 1
                        bucket['amount'] += Decimal(payload['amount'])
                        if payload['outstanding'] is None:
                            bucket['outstanding_known'] = False
                        else:
                            bucket['outstanding'] += Decimal(payload['outstanding'])
                if len(batch) < 500:
                    break
                start += 500
    for currency, bucket in summary.items():
        bucket['amount'] = format(bucket['amount'], 'f')
        bucket['outstanding'] = format(bucket['outstanding'], 'f') if bucket['outstanding_known'] else None
    return {'connection': d.name, 'title': d.title, 'company': d.company, 'entity': entity,
            'source_org': d.source_org, 'source_timezone': d.source_timezone, 'synthetic': d.mode == 'mock',
            'rows': [{'name': row.name, **json.loads(row.payload), 'synced_at': str(row.synced_at)} for row in rows],
            'total': total, 'offset': offset, 'page_length': page_length,
            'summary_by_currency': summary, 'summary_complete': summary_complete,
            'summary_note': '仅统计匹配范围内有效记录；按来源币种分组，不进行汇率换算。超过一万条时请缩小查询范围。',
            'last_success': str(binding.last_success or ''), 'source_window_until': binding.cursor,
            'last_error': binding.last_error, 'stale': bool(stale),
            'verified_current': bool(binding.last_success and not stale and not binding.last_error and d.enabled),
            'note': '只读外部快照，不代表已在本系统记账或执行付款。合成数据不可作为真实业务依据。'}


@frappe.whitelist()
def records(connection, entity='payable', q='', offset=0, page_length=20):
    return query(connection, entity, q, offset, page_length)


@frappe.whitelist()
def detail(name):
    doc = frappe.get_doc('External ERP Snapshot', name)
    doc.check_permission('read')
    service.authorize(doc.connection)
    return {'name': doc.name, 'connection': doc.connection, 'company': doc.company,
            'synthetic': bool(doc.synthetic), 'synced_at': str(doc.synced_at), **json.loads(doc.payload)}


@frappe.whitelist()
def runs(connection):
    service.require_manager()
    service.authorize(connection)
    return frappe.get_all('ERP Sync Run', filters={'connection': connection},
                          fields=['name', 'state', 'started_at', 'finished_at', 'requested_by', 'result_json'],
                          order_by='creation desc', limit_page_length=20)


@frappe.whitelist(methods=['POST'])
def sync_now(connection, full_scan=0):
    if str(full_scan) not in ('0', '1'):
        frappe.throw('全量重扫参数只能为0或1')
    return service.enqueue(connection, full=bool(cint(full_scan)))


@frappe.whitelist(methods=['POST'])
def create_demo(company):
    service.require_manager()
    frappe.get_doc('Company', company).check_permission('read')
    # 不创建公司或原生业务单据；模拟数据仅在用户随后发起同步时生成。
    doc = frappe.get_doc({'doctype': 'ERP Connection', 'title': '金蝶云星空 · 合成演示', 'vendor': 'kingdee',
                         'mode': 'mock', 'enabled': 1, 'auto_sync': 0, 'allow_ai': 0,
                         'company': company, 'account_id': 'SYNTHETIC-ONLY', 'source_org': '100',
                         'source_timezone': '+08:00', 'page_size': 200, 'stale_minutes': 60,
                         'bindings': [{'entity': e, 'enabled': 1} for e in core.ENTITIES]})
    doc.insert()
    return {'name': doc.name, 'synthetic': True}


def agent_tools(runtime):
    return [runtime.schema('list_external_connections', '列出本账号获准使用的外部ERP连接；合成数据不是真实事实', {}),
            runtime.schema('read_external_finance', '按连接查询外部ERP只读快照与按币种精确汇总；需额外模型授权，必须报告来源、同步时间及过期/错误',
                           {'connection': runtime.text_schema(), 'entity': runtime.text_schema(core.ENTITIES),
                            'q': runtime.text_schema()}, ['connection', 'entity'])]


def agent_call(tool, args):
    if tool == 'read_external_finance':
        return query(args['connection'], args['entity'], args.get('q', ''), ai=True)
    if tool != 'list_external_connections':
        frappe.throw('未授权外部数据工具', frappe.PermissionError)
    # 不把未授权模型使用的账套名称、公司或连接计数送给模型。
    rows = []
    for d in connections()['connections']:
        try:
            service.authorize(d['name'], ai=True)
        except frappe.PermissionError:
            frappe.clear_messages()
            continue
        rows.append(d)
    return {'connections': rows, 'note': '只列获准模型使用的连接；空列表不代表企业无应付数据。'}
