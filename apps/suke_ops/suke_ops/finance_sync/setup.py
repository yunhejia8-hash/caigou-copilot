"""只增量安装外部数据定义，不改原生账务或已有连接配置。"""
import frappe


def install():
    from suke_ops.setup import field as f
    if frappe.session.user != 'Administrator' and 'System Manager' not in frappe.get_roles():
        frappe.throw('仅系统管理员可安装企业数据连接', frappe.PermissionError)
    definitions = {
        'ERP Data Access': [f('user', '授权用户', 'Link', options='User', reqd=1),
                            f('allow_ai', '允许此用户通过Agent查询', 'Check', default=0)],
        'ERP Sync Binding': [f('entity', '同步实体', 'Select', options='supplier\nitem\npurchase_order\npayable\npayment', reqd=1),
                             f('enabled', '启用', 'Check', default=1),
                             f('confirmed', '已核对真实租户字段与财务口径', 'Check', default=0),
                             f('mapping_json', '表单与字段映射', 'Code', options='JSON'),
                             f('cursor', '已完成UTC窗口', read_only=1),
                             f('last_success', '最近成功时间', 'Datetime', read_only=1),
                             f('last_error', '最近错误', 'Small Text', read_only=1)],
        'ERP Connection': [f('title', '连接名称', reqd=1, in_list_view=1),
                           f('vendor', '厂商', 'Select', options='kingdee\nyonyou\nsap', default='kingdee', reqd=1),
                           f('mode', '模式', 'Select', options='mock\nlive', default='mock', reqd=1, in_list_view=1),
                           f('enabled', '启用连接', 'Check', default=0),
                           f('auto_sync', '每15分钟自动同步', 'Check', default=0),
                           f('allow_ai', '允许授权用户将查询结果用于模型分析', 'Check', default=0),
                           f('company', '本地公司', 'Link', options='Company', reqd=1, in_list_view=1),
                           f('account_id', '来源数据中心／账套ID', reqd=1),
                           f('source_org', '来源组织编码', reqd=1),
                           f('source_timezone', '来源UTC偏移', default='+08:00', reqd=1),
                           f('server_url', '官方API服务地址'), f('app_user', '集成用户'), f('app_id', '应用ID'),
                           f('app_secret', '应用密钥', 'Password'),
                           f('access_users', '可查询用户（默认不授权）', 'Table', options='ERP Data Access'),
                           f('bindings', '同步对象与字段', 'Table', options='ERP Sync Binding'),
                           f('stale_minutes', '数据过期阈值（分钟）', 'Int', default=60),
                           f('page_size', '每页数量', 'Int', default=200)],
        'External ERP Snapshot': [f('connection', '来源连接', 'Link', options='ERP Connection', reqd=1),
                                  f('company', '本地公司', 'Link', options='Company', reqd=1),
                                  f('entity', '实体', reqd=1), f('source_id', '来源内码', reqd=1),
                                  f('line_id', '来源明细内码'), f('number', '来源单号', in_list_view=1),
                                  f('source_org', '来源组织'), f('source_modified', '来源更新时间UTC'),
                                  f('state', '源状态', in_list_view=1), f('synthetic', '合成数据', 'Check'),
                                  f('supplier_code', '来源供应商'), f('item_code', '来源物料'),
                                  f('currency', '来源币种'), f('content_hash', '内容指纹'),
                                  f('synced_at', '读取时间', 'Datetime'),
                                  f('payload', '标准只读数据', 'Code', options='JSON')],
        'ERP Sync Run': [f('connection', '来源连接', 'Link', options='ERP Connection', reqd=1),
                         f('state', '执行状态', 'Select', options='queued\nrunning\nsuccess\npartial\nfailed\nskipped', in_list_view=1),
                         f('requested_by', '申请人', 'Link', options='User'),
                         f('full_scan', '全量重扫', 'Check'), f('started_at', '开始时间', 'Datetime'),
                         f('finished_at', '结束时间', 'Datetime'),
                         f('result_json', '各对象结果（不含源响应）', 'Code', options='JSON')],
    }
    for name, fields in definitions.items():
        child = name in ('ERP Data Access', 'ERP Sync Binding')
        if name in ('External ERP Snapshot', 'ERP Sync Run'):
            for field in fields:
                field['read_only'] = 1
        if not frappe.db.exists('DocType', name):
            perms = [] if child else [{'role': 'System Manager', 'read': 1,
                                      'write': int(name == 'ERP Connection'), 'create': int(name == 'ERP Connection')}]
            if name == 'External ERP Snapshot':
                perms.append({'role': 'Desk User', 'read': 1})
            frappe.get_doc({'doctype': 'DocType', 'name': name, 'module': 'Supply Home', 'custom': 1,
                            'istable': int(child), 'autoname': 'hash', 'fields': fields, 'permissions': perms}).insert()
        else:
            doc = frappe.get_doc('DocType', name)
            existing = {r.fieldname for r in doc.fields}
            additions = [field for field in fields if field['fieldname'] not in existing]
            for field in additions:
                doc.append('fields', field)
            if additions:
                doc.save()
    frappe.reload_doc('supply_home', 'page', 'erp_connections')
    frappe.clear_cache()
    return {'installed': True, 'page': '/desk/主页/erp-connections',
            'live_enabled': frappe.conf.get('erp_finance_live_enabled') is True}
