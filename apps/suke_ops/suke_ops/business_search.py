"""业务目录与实时记录查询：所有范围、字段、分页和来源权限均由服务端决定。"""
from urllib.parse import urlencode

import frappe
from frappe.model import get_permitted_fields
from frappe.query_builder.functions import Count
from frappe.utils import cint, now_datetime

from suke_ops import catalog

SEARCH_FIELDS = ('title', 'item_name', 'supplier_name', 'customer_name', 'item_code', 'production_item',
                 'supplier', 'customer', 'warehouse', 'company', 'source_name', 'status', 'workflow_state')
SUMMARY_FIELDS = ('title', 'item_name', 'supplier_name', 'customer_name', 'item_code', 'production_item',
                  'warehouse', 'status', 'workflow_state')


def require_user():
    if (frappe.session.user == 'Guest' or not frappe.get_cached_value('User', frappe.session.user, 'enabled')
            or frappe.get_cached_value('User', frappe.session.user, 'user_type') != 'System User'):
        frappe.throw('请用企业内部账号登录。', frappe.PermissionError)


def available(entry):
    if entry['kind'] != 'page':
        return bool(frappe.has_permission(entry['doctype'], 'read'))
    types = entry.get('access_types') or catalog.MODULE_DOCS.get(entry['module'])
    if types is None:
        types = [dt for docs in catalog.MODULE_DOCS.values() for dt in docs] + catalog.SHARED_DOCS
    if not any(frappe.has_permission(dt, 'read') for dt in types):
        return False
    page = entry['route'].rsplit('/', 1)[-1]
    roles = frappe.get_all('Has Role', filters={'parenttype': 'Page', 'parent': page}, pluck='role')
    return not roles or bool(set(roles) & set(frappe.get_roles()))


def readable(doctype, name, seen=None):
    """协同记录还必须拥有来源单据读取权；摘要、计数与打开复用同一检查。"""
    seen = set() if seen is None else seen
    key = (doctype, name)
    if key in seen:
        return False
    seen.add(key)
    try:
        doc = frappe.get_doc(doctype, name)
        if not frappe.has_permission(doctype, 'read', doc=doc):
            return False
        links = []
        if doctype == 'Supply Case':
            links = [(dt, doc.get(field)) for dt, field in [('Company', 'company'), ('Item', 'item_code'),
                     ('Warehouse', 'warehouse'), ('Purchase Order', 'purchase_order'), ('Sales Order', 'priority_sales_order')]]
        elif doctype == 'Supply Decision':
            links = [('Supply Case', doc.supply_case)]
        elif doctype == 'Operations Approval':
            if doc.source_type not in ('Purchase Order', 'Sales Order', 'Work Order', 'Stock Entry'):
                return False
            links = [(doc.source_type, doc.source_name)]
        return all(readable(dt, source, seen.copy()) for dt, source in links if source)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return False


def shared_records(doctype, filters=None, fields=None):
    """小规模协同单据逐批核对来源；权限过滤发生在计数与分页之前。"""
    if not frappe.has_permission(doctype, 'read'):
        return []
    result, offset = [], 0
    while True:
        batch = frappe.get_list(doctype, filters=filters or {}, fields=fields or ['name'],
                                order_by='modified desc, name asc', limit_start=offset, limit_page_length=200)
        result.extend(row for row in batch if readable(doctype, row.name))
        if len(batch) < 200:
            return result
        offset += 200


def clean_text(value):
    value = str(value or '').strip()
    if len(value) > 120:
        frappe.throw('搜索内容最多120个字符。')
    return value


def matches(entry, q):
    text = ' '.join(str(entry.get(k, '')) for k in ('label', 'description', 'aliases', 'doctype'))
    text += ' ' + catalog.MODULE_NAMES[entry['module']]
    return all(token in text.casefold() for token in q.casefold().split())


@frappe.whitelist()
def directory(q='', module=''):
    require_user()
    q = clean_text(q)
    if module and module not in catalog.MODULE_NAMES:
        frappe.throw('无效业务模块。')
    entries = [e for e in catalog.entries() if available(e) and matches(e, q)
               and (not module or module in e.get('modules', [e['module']]))]
    # 不向客户端发送可自行拼接的筛选表达式，查询只接受目录ID。
    return {'entries': [{k: v for k, v in e.items() if k not in ('filters', 'access_types')} for e in entries],
            'modules': catalog.MODULE_NAMES, 'at': str(now_datetime())}


def get_entry(entry_id):
    entry = next((e for e in catalog.entries() if e['id'] == entry_id and e['kind'] != 'page'), None)
    if not entry or not available(entry):
        frappe.throw('该数据范围不可访问。', frappe.PermissionError)
    return entry


def query_rows(entry, q, start, page_length):
    dt = entry['doctype']
    meta = frappe.get_meta(dt)
    permitted = set(get_permitted_fields(dt, permission_type='read'))
    search_fields = ['name'] + [f for f in SEARCH_FIELDS if meta.has_field(f) and f in permitted]
    fields = ['name', 'modified'] + [f for f in SUMMARY_FIELDS if meta.has_field(f) and f in permitted]
    pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    or_filters = [[dt, f, 'like', pattern] for f in search_fields] if q else []
    # 对物料明细建立父单查询关联，仍由父单权限过滤，不返回子表原文。
    if q:
        levels = set(meta.get_permlevel_access('read'))
        for table in meta.get_table_fields():
            if table.fieldname not in ('items', 'required_items'):
                continue
            if frappe.session.user != 'Administrator' and table.permlevel not in levels:
                continue
            child_meta = frappe.get_meta(table.options)
            child_permitted = set(get_permitted_fields(table.options, parenttype=dt, permission_type='read'))
            or_filters.extend([table.options, f, 'like', pattern] for f in ('item_code', 'item_name')
                              if child_meta.has_field(f) and f in child_permitted)
    kwargs = dict(filters=entry.get('filters', {}), or_filters=or_filters)
    if dt in catalog.SHARED_DOCS:
        rows, offset = [], 0
        while True:
            batch = frappe.get_list(dt, fields=fields, order_by='modified desc, name asc',
                                    limit_start=offset, limit_page_length=200, **kwargs)
            rows.extend(r for r in batch if readable(dt, r.name))
            if len(batch) < 200:
                break
            offset += 200
        total = len(rows)
        rows = rows[start:start + page_length]
    else:
        total = cint(frappe.get_list(dt, fields=[Count(frappe.qb.DocType(dt).name).distinct().as_('total')], **kwargs)[0].total)
        rows = frappe.get_list(dt, fields=fields, distinct=True, order_by='modified desc, name asc',
                               limit_start=start, limit_page_length=page_length, **kwargs)
    return {'entry': entry['id'], 'label': entry['label'], 'doctype': dt, 'total': total, 'start': start,
            'page_length': page_length, 'has_more': start + len(rows) < total,
            'search_fields': [meta.get_field(f).label if meta.get_field(f) else '单号' for f in search_fields],
            'rows': [{'name': row.name, 'doctype': dt, 'label': catalog.LABELS[dt],
                      'title': next((row.get(f) for f in SUMMARY_FIELDS[:6] if row.get(f)), row.name),
                      'status': row.get('workflow_state') or row.get('status') or '',
                      'modified': str(row.modified)} for row in rows]}


@frappe.whitelist()
def records(q='', entry='', start=0, page_length=20, module=''):
    require_user()
    q = clean_text(q)
    if module and module not in catalog.MODULE_NAMES:
        frappe.throw('无效业务模块。')
    start, page_length = max(0, cint(start)), min(50, max(1, cint(page_length)))
    if entry:
        groups = [query_rows(get_entry(entry), q, start, page_length)]
    elif q:
        groups = [query_rows(e, q, 0, 3) for e in catalog.entries() if e['kind'] == 'type' and available(e)
                  and (not module or module in e.get('modules', [e['module']]))]
        groups = [g for g in groups if g['total']]
    else:
        groups = []
    return {'groups': groups, 'at': str(now_datetime()), 'scope': '当前账号可见的完整记录；不受面板最近12笔限制'}


@frappe.whitelist()
def open_record(doctype, name):
    require_user()
    get_entry('type:' + doctype)
    if not readable(doctype, name):
        frappe.throw('该记录不可访问或已不存在。', frappe.PermissionError)
    return ['Form', doctype, name]


def search_url(q='', entry=''):
    return '/desk/主页/operations-data?' + urlencode({'business_search': '1', 'q': q, 'entry': entry})


def awesomebar(txt):
    require_user()
    q = clean_text(txt)
    esc = lambda value: frappe.utils.escape_html(str(value))
    results = [dict(label='业务搜索与目录：' + esc(q), description='查找功能、常用查询和完整授权记录', route=search_url(q), index=110)]
    for e in directory(q)['entries'][:8]:
        results.append(dict(label=esc(e['label']), description=esc(catalog.MODULE_NAMES[e['module']] + ' · ' + e['description']),
                            route=e.get('route') or search_url(entry=e['id']), index=105))
    for group in records(q)['groups'][:5]:
        for row in group['rows'][:1]:
            # 通过业务搜索重新校验后打开，协同记录不直接暴露未经来源检查的表单入口。
            results.append(dict(label=esc(row['label'] + ' ' + row['name']), description=esc(row['title']),
                                route=search_url(row['name'], 'type:' + row['doctype']), index=102))
    return results
