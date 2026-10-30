"""四模块共用的单据审批。申请审批后，原单必须审批通过且内容未变才能提交。"""
import json
import frappe
from suke_ops.api import require
from suke_ops.engine import digest

TYPES = ('Purchase Order', 'Sales Order', 'Work Order', 'Stock Entry')


def document_hash(doc):
    # 审批的实际业务字段；排除保存时间、自动计算状态等噪声。
    fields = ('company', 'supplier', 'customer', 'currency', 'conversion_rate', 'grand_total', 'schedule_date',
              'delivery_date', 'production_item', 'bom_no', 'qty', 'planned_start_date', 'planned_end_date',
              'source_warehouse', 'fg_warehouse', 'wip_warehouse', 'stock_entry_type', 'purpose')
    row_fields = ('item_code', 'qty', 'stock_qty', 'required_qty', 'rate', 'amount', 'schedule_date', 'delivery_date',
                  'warehouse', 'source_warehouse', 's_warehouse', 't_warehouse', 'uom', 'conversion_factor', 'basic_rate',
                  'charge_type', 'account_head', 'tax_amount', 'cost_center')
    data = {f: doc.get(f) for f in fields}
    for name in ('items', 'required_items', 'taxes'):
        data[name] = [{f: r.get(f) for f in row_fields} for r in (doc.get(name) or [])]
    return digest(json.loads(frappe.as_json(data)))


@frappe.whitelist()
def request(document_type, document_name, reason):
    require()
    if document_type not in TYPES or not str(reason or '').strip():
        frappe.throw('请选择支持的业务单据并填写审批说明')
    source = frappe.get_doc(document_type, document_name)
    source.check_permission('write')
    if source.docstatus != 0:
        frappe.throw('只有草稿单据可以申请提交审批')
    signature = document_hash(source)
    key = digest([document_type, document_name, signature])
    existing = frappe.db.exists('Operations Approval', {'request_key': key})
    if existing:
        return existing
    return frappe.get_doc({'doctype': 'Operations Approval', 'source_type': document_type, 'source_name': document_name,
                           'source_hash': signature, 'reason': reason, 'request_key': key}).insert().name


def validate(doc, method=None):
    old = doc.get_doc_before_save()
    if old and any(doc.get(f) != old.get(f) for f in ('source_type', 'source_name', 'source_hash', 'request_key')):
        frappe.throw('审批对象不可修改，请重新发起审批')
    if not old or doc.workflow_state != old.workflow_state and doc.workflow_state in ('待审批', '已批准'):
        source = frappe.get_doc(doc.source_type, doc.source_name)
        source.check_permission('read')
        if source.docstatus != 0 or document_hash(source) != doc.source_hash:
            frappe.throw('业务单据已变化，请重新申请审批')


@frappe.whitelist()
def transition(name, action, reason=None):
    require(action in ('批准', '驳回'))
    if action not in ('提交审批', '批准', '驳回', '修正重提'):
        frappe.throw('无效审批动作')
    from frappe.model.workflow import apply_workflow
    doc = frappe.get_doc('Operations Approval', name)
    doc.check_permission('write')
    if action in ('驳回', '修正重提') and not str(reason or '').strip():
        frappe.throw('请填写驳回或修正说明')
    if reason:
        doc.reason = reason
        doc.save()
        doc.add_comment('Comment', action + '：' + reason)
    return apply_workflow(doc.as_json(), action).name


def guard_submit(doc, method=None):
    approvals = frappe.get_all('Operations Approval', filters={'source_type': doc.doctype, 'source_name': doc.name},
                               fields=['name', 'source_hash', 'workflow_state'])
    if approvals and not any(a.workflow_state == '已批准' and a.source_hash == document_hash(doc) for a in approvals):
        frappe.throw('该单据已进入协同审批，必须取得与当前内容一致的批准后才能提交')


def install():
    from suke_ops.setup import field
    if not frappe.db.exists('DocType', 'Operations Approval'):
        frappe.get_doc({'doctype': 'DocType', 'name': 'Operations Approval', 'module': 'Buying', 'custom': 1,
                        'autoname': 'format:OPS-APP-{#####}', 'track_changes': 1,
                        'fields': [field('source_type', '业务类型', 'Select', options='\n'.join(TYPES), reqd=1, read_only=1, in_list_view=1),
                                   field('source_name', '业务单据', 'Dynamic Link', options='source_type', reqd=1, read_only=1, in_list_view=1),
                                   field('reason', '申请／驳回／修正说明', 'Small Text', reqd=1),
                                   field('workflow_state', '审批状态', 'Link', options='Workflow State', read_only=1, in_list_view=1),
                                   field('source_hash', '审批内容版本', read_only=1), field('request_key', '防重标识', read_only=1, unique=1)],
                        'permissions': [{'role': r, 'read': 1, 'write': 1, 'create': 1, 'report': 1} for r in ['System Manager', 'Supply Planner', 'Supply Approver']]}).insert()
    if not frappe.db.exists('Workflow', '四模块业务单据审批'):
        workflow = frappe.get_doc('Workflow', '供应协同方案审批')
        new = frappe.copy_doc(workflow)
        new.workflow_name = '四模块业务单据审批'
        new.document_type = 'Operations Approval'
        new.states = [s for s in new.states if s.state != '已执行']
        new.transitions = [t for t in new.transitions if t.action != '执行']
        new.insert()
    frappe.clear_cache()
    frappe.db.commit()
