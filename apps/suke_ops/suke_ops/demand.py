"""把用户采购需求保存为真实物料需求单草稿；不提交、不审批、不选择供应商。"""
import hashlib
import json
import re
from datetime import date

import frappe
from frappe.utils import nowdate

from suke_ops import agent_runtime as runtime


MODULES = ('buying', 'buying-demand')
ITEM_SCHEMA = {'type': 'object', 'additionalProperties': False,
               'properties': {'item_code': runtime.text_schema(),
                              'qty': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1000000000},
                              'uom': runtime.text_schema(), 'warehouse': runtime.text_schema()},
               'required': ['item_code', 'qty', 'uom', 'warehouse']}
CREATE_TOOL = runtime.schema('create_material_request',
    '用户提出采购需求时，信息齐全即保存采购类型物料需求单草稿并返回真实单号；不提交、不审批。'
    '必须先核实物料和单位；需求日期和数量来自用户，不可编造。同会话同内容重复请求复用已有单据。',
    {'company': runtime.text_schema(), 'schedule_date': runtime.text_schema(),
     'items': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': ITEM_SCHEMA}},
    ['company', 'schedule_date', 'items'])


def context(search):
    """候选项均通过当前用户权限过滤；多义物料名称须由用户选择。"""
    if not isinstance(search, str) or len(search) > 100:
        raise RuntimeError('请用不超过100字的物料编码或名称查询。')
    companies = frappe.get_list('Company', fields=['name', 'default_warehouse'], limit_page_length=20)
    default = frappe.defaults.get_user_default('Company') or frappe.db.get_single_value('Global Defaults', 'default_company')
    items = frappe.get_list('Item', filters={'disabled': 0, 'is_purchase_item': 1, 'has_variants': 0},
                            or_filters={'item_code': ['like', '%' + search + '%'],
                                        'item_name': ['like', '%' + search + '%']},
                            fields=['name', 'item_name', 'stock_uom'], limit_page_length=20)
    warehouses = frappe.get_list('Warehouse', filters={'is_group': 0, 'disabled': 0},
                                fields=['name', 'company'], limit_page_length=40)
    return {'as_of': nowdate(), 'companies': companies,
            'default_company': default if default in [row.name for row in companies] else None,
            'items': items, 'warehouses': warehouses,
            'note': '候选项有数量上限；名称不唯一时追问，数量和需求日期不得推测。默认按库存单位登记。'}


def selected_by_user(item, unique_name, questions):
    """从新到旧判断有效选择；改选、否定和待选不能被更早的选择覆盖。"""
    code = r'(?<![A-Za-z0-9_-])' + re.escape(item.name) + r'(?![A-Za-z0-9_-])'
    target = code + ('|' + re.escape(item.item_name) if unique_name else '')
    for question in questions:
        if re.search(r'(?:只|仅)(?:做)?(?:分析|核算|查询|比较)|(?:不要|不|暂不|无需|别)(?:再)?(?:创建|登记|生成|建单)', question):
            return False
        changes = [match for match in re.finditer(r'改选|改为|改成|换成|重新选择', question)
                   if not re.search(r'(?:数量|预算|日期|规格|仓库|收货仓|用途|单位|单价|到货)(?:调整)?$', question[:match.start()])]
        active = question[changes[-1].start():] if changes else question
        if changes and re.search(r'保留\s*(?:' + target + ')', question[:changes[-1].start()], re.I):
            active = question
        if re.search(r'取消.{0,12}(?:选择|需求)|不再.{0,8}(?:上轮|刚才|原物料)', active) and not re.search(target, active, re.I):
            return False
        if re.search(target, active, re.I):
            if re.search(r'候选|还是|或者|比较|对比', active):
                return False
            for clause in re.split(r'[，,。；;\n]', active):
                match = re.search(target, clause, re.I)
                if not match:
                    continue
                if re.search(r'不(?:要|选|用|采购|买)|不是|排除|取消', clause[:match.start()]):
                    return False
                if re.search(r'^(?:暂时)?(?:不要|不买|取消|不选|排除)', clause[match.end():].strip()):
                    return False
            return bool(re.search(r'采购|购买|需要|买|选定|选择|改选|登记|创建|确认', active))
        # 当前明确指定了另一物料时，不能回退到更早的历史选择。
        if changes or re.search(r'(?:采购|购买|买|选定|选择|物料(?:编码|名称)?[：:])\s*(?:\d+(?:\.\d+)?\s*(?:Nos|个|件|台|套|盒|包|米)\s*)?(?!需求|数量|预算|日期|单位)[A-Za-z\u4e00-\u9fff]', active):
            return False
    return False


def create(module, turn, company, schedule_date, items, current_documents=()):
    if module not in MODULES:
        frappe.throw('该模块不能创建物料需求单。', frappe.PermissionError)
    runtime.validate_input({'company': company, 'schedule_date': schedule_date, 'items': items}, CREATE_TOOL['input_schema'])
    try:
        due = date.fromisoformat(schedule_date)
    except ValueError:
        raise RuntimeError('缺少有效需求日期，请提供 YYYY-MM-DD 日期。') from None
    if due.isoformat() != schedule_date or due < date.fromisoformat(nowdate()):
        raise RuntimeError('需求日期不能早于今天，请明确实际需求日期。')
    frappe.get_doc('Company', company).check_permission('read')
    if not frappe.has_permission('Material Request', 'create'):
        frappe.throw('没有创建物料需求单的权限。', frappe.PermissionError)
    if re.search(r'(?:只|仅)(?:做)?(?:分析|核算|查询|比较)|(?:不要|不|暂不|无需|别)(?:再)?(?:创建|登记|生成|建单)', turn.question or ''):
        raise RuntimeError('本轮仅分析或已撤销建单授权，未创建需求单。')
    filters = {'owner': turn.owner, 'conversation': turn.conversation}
    if getattr(turn, 'creation', None):
        filters['creation'] = ['<', turn.creation]
    previous = frappe.get_list('Agent Turn', filters=filters, fields=['question', 'result'],
                               order_by='creation desc, name desc', limit_page_length=0)
    user_questions = [turn.question or '', *(record.question or '' for record in previous)]
    rows = []
    for row in items:
        item = frappe.get_doc('Item', row['item_code'])
        item.check_permission('read')
        if item.disabled or not item.is_purchase_item or item.has_variants:
            raise RuntimeError('所选物料不可采购，请选择启用的可采购明细物料。')
        # 只认可本账号会话里的用户选择，不把模型候选或旧助手回答当成确认。
        unique_name = bool(item.item_name) and frappe.db.count('Item', {'item_name': item.item_name, 'disabled': 0, 'has_variants': 0}) == 1
        if not selected_by_user(item, unique_name, user_questions):
            raise RuntimeError('物料编码或完整名称尚未由用户选定，请先确认具体物料；候选数据和助手建议不能代替用户选择。')
        warehouse = frappe.get_doc('Warehouse', row['warehouse'])
        warehouse.check_permission('read')
        if warehouse.company != company or warehouse.is_group or warehouse.disabled:
            raise RuntimeError('需求仓库必须是所选公司的启用明细仓库。')
        conversion = 1 if row['uom'] == item.stock_uom else next(
            (unit.conversion_factor for unit in item.uoms if unit.uom == row['uom']), None)
        if not conversion or conversion <= 0:
            raise RuntimeError('该物料没有所选单位的有效换算，请使用已核实的库存单位。')
        rows.append({**row, 'qty': float(row['qty']), 'conversion_factor': conversion,
                     'schedule_date': schedule_date})
    # 同一会话相同需求复用已有单据；更改需求应打开原单，独立新需求使用新会话。
    payload = {'company': company, 'schedule_date': schedule_date,
               'items': sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    documents = [*current_documents, *(doc for record in previous
                 for doc in json.loads(record.result or '{}').get('documents', []))]
    for document in documents:
        if document.get('fingerprint') != fingerprint:
            continue
        existing = frappe.get_doc('Material Request', document['name'])
        existing.check_permission('read')
        if existing.docstatus == 2:
            raise RuntimeError('同一需求的原单已取消；如需重新采购，请开启新对话登记新需求。')
        return {**document, 'reused': True, 'docstatus': existing.docstatus}
    doc = frappe.get_doc({'doctype': 'Material Request', 'material_request_type': 'Purchase',
                          'company': company, 'schedule_date': schedule_date, 'items': rows}).insert()
    return {'doctype': doc.doctype, 'name': doc.name, 'docstatus': doc.docstatus,
            'fingerprint': fingerprint, 'reused': False,
            'next_step': '打开物料需求单核对并提交 → 创建询价单 → 收集供应商报价并比价 → 采购草稿申请审批 → 批准后提交采购订单。'}
