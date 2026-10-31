"""增量安装采购闭环；保留站点已有字段、权限和配置值。"""
import frappe


def install():
    from suke_ops.setup import field as f
    if frappe.session.user != 'Administrator' and 'System Manager' not in frappe.get_roles():
        frappe.throw('仅系统管理员可安装采购闭环', frappe.PermissionError)
    roles = ['System Manager', 'Purchase Manager', 'Purchase User', 'Supply Planner', 'Supply Approver']
    stages = 'demand\nstock\ninquiry\ncomparison\napproval\norder\nreceipt\nfinance\nreview'
    types = 'Material Request\nRequest for Quotation\nSupplier Quotation\nPurchase Order\nPurchase Receipt\nQuality Inspection\nPurchase Invoice\nPayment Entry\nOperations Approval\nSupply Case\nSupply Decision'
    definitions = {
        'Procurement Document': [f('document_type', '单据类型', 'Select', options=types, reqd=1),
                                 f('document_name', '单号', 'Dynamic Link', options='document_type', reqd=1)],
        'Procurement Process': [f('title', '采购主题', reqd=1, in_list_view=1),
                               f('company', '公司', 'Link', options='Company', reqd=1, in_list_view=1),
                               f('source_key', '需求防重标识', unique=1, read_only=1),
                               f('synthetic', '合成演示', 'Check', read_only=1),
                               f('documents', '关联单据', 'Table', options='Procurement Document'),
                               f('stock_review', '库存核对记录', 'Code', options='JSON', read_only=1),
                               f('review_json', '实际结果复盘', 'Code', options='JSON', read_only=1)],
        'Procurement Experience': [f('title', '经验主题', reqd=1, in_list_view=1),
                                  f('company', '公司', 'Link', options='Company', reqd=1),
                                  f('procurement_process', '采购流程', 'Link', options='Procurement Process', reqd=1),
                                  f('stage', '采购环节', 'Select', options=stages, reqd=1),
                                  f('kind', '经验类型', 'Select', options='case\nexpert\npolicy', reqd=1),
                                  f('situation', '场景及判断依据', 'Long Text', reqd=1),
                                  f('recommendation', '建议行动及例外', 'Small Text', reqd=1),
                                  f('conditions_json', '适用条件', 'Code', options='JSON', reqd=1),
                                  f('context_json', '判断时点快照', 'Code', options='JSON', read_only=1),
                                  f('sources_json', '来源及版本', 'Code', options='JSON', read_only=1),
                                  f('source_key', '来源防重标识', unique=1, read_only=1),
                                  f('synthetic', '合成演示', 'Check', read_only=1),
                                  f('verified', '已核实', 'Check', read_only=1, in_list_view=1),
                                  f('outcome', '核实结果', 'Select', options='unknown\npositive\nnegative', read_only=1),
                                  f('observed_at', '结果核实时间', 'Datetime', read_only=1),
                                  f('verification_json', '核实记录', 'Code', options='JSON', read_only=1)],
        'Procurement Rule': [f('title', '规则主题', reqd=1, in_list_view=1),
                             f('company', '公司', 'Link', options='Company', reqd=1),
                             f('stage', '采购环节', 'Select', options=stages, reqd=1),
                             f('family', '规则系列', read_only=1), f('version', '版本', 'Int', read_only=1),
                             f('state', '状态', 'Select', options='candidate\nactive\ndisabled', read_only=1, in_list_view=1),
                             f('conditions_json', '适用条件', 'Code', options='JSON', read_only=1),
                             f('recommendation', '建议行动及例外', 'Small Text', read_only=1),
                             f('training_json', '归纳样本', 'Code', options='JSON', read_only=1),
                             f('evaluation_json', '留出验证及样本版本', 'Code', options='JSON', read_only=1),
                             f('publication_json', '发布或停用记录', 'Code', options='JSON', read_only=1),
                             f('valid_until', '有效至', 'Date', read_only=1),
                             f('source_key', '候选防重标识', unique=1, read_only=1)],
        'Procurement Settings': [f('enabled', '启用采购闭环', 'Check', default=1),
                                 f('knowledge_enabled', '启用流程内经验参考', 'Check', default=1),
                                 f('mandatory_approval', '正式采购单必须协同审批', 'Check', default=0)],
    }
    for name, fields in definitions.items():
        child = name == 'Procurement Document'
        settings = name == 'Procurement Settings'
        if not frappe.db.exists('DocType', name):
            permissions = [] if child else ([{'role': 'System Manager', 'read': 1, 'write': 1, 'create': 1}]
                                            if settings else [{'role': r, 'read': 1, 'report': 1} for r in roles])
            frappe.get_doc({'doctype': 'DocType', 'name': name, 'module': 'Buying', 'custom': 1,
                            'istable': int(child), 'issingle': int(settings), 'track_changes': int(not child),
                            'autoname': 'hash', 'fields': fields, 'permissions': permissions}).insert()
        else:
            meta = frappe.get_doc('DocType', name)
            existing = {r.fieldname for r in meta.fields}
            renamed = False
            if name == 'Procurement Experience' and 'process' in existing:
                # 首次联调发现process是Frappe Meta的方法名；仅修复本扩展的失败安装字段。
                for row in meta.fields:
                    if row.fieldname == 'process':
                        row.fieldname = 'procurement_process'
                        renamed = True
                existing.discard('process')
                existing.add('procurement_process')
            for row in fields:
                if row['fieldname'] not in existing:
                    meta.append('fields', row)
            if renamed or set(r.fieldname for r in meta.fields) != existing:
                meta.save()
    frappe.reload_doc('buying', 'page', 'procurement_workspace')
    frappe.clear_cache()
    return {'installed': True, 'page': '/desk/buying/procurement-workspace',
            'note': '安装不重写已有配置，强制审批由管理员显式开启'}
