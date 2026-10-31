"""采购闭环的受控入口；所有事实来自有权读取的原生单据。"""
import json
import time
from contextvars import ContextVar
from datetime import date, timedelta

import frappe
from frappe.utils import now_datetime, nowdate, flt

from suke_ops import procurement as calc
from suke_ops.engine import digest

TYPES = ('Material Request', 'Request for Quotation', 'Supplier Quotation', 'Purchase Order',
         'Purchase Receipt', 'Quality Inspection', 'Purchase Invoice', 'Payment Entry',
         'Operations Approval', 'Supply Case', 'Supply Decision')
EDITORS = {'System Manager', 'Purchase Manager', 'Purchase User', 'Supply Planner', 'Supply Approver'}
REVIEWERS = {'System Manager', 'Purchase Manager', 'Supply Approver'}
ADVICE_DEADLINE = ContextVar('procurement_advice_deadline', default=None)


def _budget():
    deadline = ADVICE_DEADLINE.get()
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError('本轮经验参考达到时限')


def _json(value, default):
    return json.loads(value) if isinstance(value, str) and value else default if value is None or value == '' else value


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def _text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        frappe.throw(f'{label}须填写1至{limit}字')
    return value.strip()


def _require(write=False, review=False):
    from suke_ops.hub import require_user
    require_user()
    if not frappe.db.exists('DocType', 'Procurement Settings'):
        frappe.throw('采购闭环尚未安装')
    if not frappe.get_single('Procurement Settings').enabled:
        frappe.throw('采购闭环已关闭；原有查询与业务功能仍可使用')
    if write and not (set(frappe.get_roles()) & (REVIEWERS if review else EDITORS)):
        frappe.throw('没有采购维护或审核权限', frappe.PermissionError)


def _source(doctype, name, company=None):
    _budget()
    if doctype not in TYPES:
        frappe.throw('不支持的采购来源类型')
    doc = frappe.get_doc(doctype, name)
    doc.check_permission('read')
    _check_fields(doc)
    _budget()
    if doctype == 'Operations Approval':
        source = _source(doc.source_type, doc.source_name, company)
        actual_company = source.get('company')
    elif doctype == 'Supply Decision':
        actual_company = _source('Supply Case', doc.supply_case, company).company
    elif doctype == 'Quality Inspection':
        actual_company = _source(doc.reference_type, doc.reference_name, company).get('company')
    else:
        actual_company = doc.get('company')
    if not actual_company or (company and actual_company != company):
        frappe.throw('单据不属于当前采购公司的范围', frappe.PermissionError)
    frappe.get_doc('Company', actual_company).check_permission('read')
    return doc


def _check_fields(doc):
    # 快照会跨时间保留，保守要求来源及子表的字段权限完整，避免后续撤权后泄漏旧快照。
    for row in [doc, *(child for df in doc.meta.get_table_fields() for child in (doc.get(df.fieldname) or []))]:
        _budget()
        if any(df.permlevel and not row.has_permlevel_access_to(df.fieldname) for df in row.meta.fields):
            frappe.throw('当前账号缺少采购快照所需的来源字段权限', frappe.PermissionError)


def _inventory_permissions(process):
    for link in process.documents:
        _budget()
        if link.document_type != 'Material Request':
            continue
        mr = frappe.get_doc('Material Request', link.document_name)
        if not frappe.has_permission('Bin', 'read'):
            frappe.throw('采购快照需要库存读取权限', frappe.PermissionError)
        for item in mr.items:
            _budget()
            for doctype, name in [('Item', item.item_code), ('Warehouse', item.warehouse)]:
                if name:
                    source = frappe.get_doc(doctype, name)
                    source.check_permission('read')
                    _check_fields(source)
            for name in frappe.get_all('Bin', filters={'item_code': item.item_code, 'warehouse': item.warehouse}, pluck='name'):
                _budget()
                stock = frappe.get_doc('Bin', name)
                stock.check_permission('read')
                _check_fields(stock)


def _process(name, write=False):
    _budget()
    _require(write=write)
    if write:
        frappe.db.sql('select name from `tabProcurement Process` where name=%s for update', name)
    doc = frappe.get_doc('Procurement Process', name)
    doc.check_permission('read')
    frappe.get_doc('Company', doc.company).check_permission('read')
    # 公司权限不足或任一来源被撤权时整笔流程拒绝访问，不泄露数量及摘要。
    for row in doc.documents:
        _budget()
        source = _source(row.document_type, row.document_name, doc.company)
        if source.doctype == 'Material Request' and _synthetic_source(source):
            doc.synthetic = 1
    return doc


def _synthetic_source(mr):
    return ('演示' in mr.company or any(i.item_code.startswith('SUKE-') for i in mr.items))


def _save(doc):
    doc.flags.procurement_write = True
    return doc.save(ignore_permissions=True)


def guard(doc, method=None):
    """原生REST也必须经过同一写入约束，不能直接伪造审核或规则状态。"""
    if not doc.flags.procurement_write:
        frappe.throw('请使用采购流程工作台的受控操作', frappe.PermissionError)


def private_query(user=None):
    # 列表/报表不能逐源校验，统一通过下面的受控分页API读取。
    return '1=0'


def document_permission(doc, ptype='read', user=None, **kwargs):
    if ptype not in ('read', 'report') or (user and user != frappe.session.user):
        return False
    if not (set(frappe.get_roles()) & EDITORS):
        return False
    try:
        frappe.get_doc('Company', doc.company).check_permission('read')
        if doc.doctype == 'Procurement Rule':
            _rule_examples(doc)
        else:
            proc = doc if doc.doctype == 'Procurement Process' else frappe.get_doc('Procurement Process', doc.procurement_process)
            frappe.get_doc('Company', proc.company).check_permission('read')
            if proc.company != doc.company:
                return False
            for row in proc.documents:
                _source(row.document_type, row.document_name, proc.company)
            _inventory_permissions(proc)
            if doc.doctype == 'Procurement Experience':
                for row in _json(doc.sources_json, []):
                    _source(row['doctype'], row['name'], proc.company)
        return None  # 通过来源校验后仍须保留原生角色权限检查。
    except (frappe.PermissionError, frappe.ValidationError):
        frappe.clear_messages()
        return False


@frappe.whitelist()
def listing():
    _require()
    rows = frappe.get_all('Procurement Process', fields=['name', 'title', 'company', 'synthetic'],
                           order_by='modified desc', limit_page_length=100)
    result = []
    for row in rows:
        try:
            proc = _process(row.name)
        except frappe.PermissionError:
            frappe.clear_messages()
            continue
        row.synthetic = bool(proc.synthetic)
        result.append(row)
    return {'processes': result, 'limit': 100, 'can_edit': bool(set(frappe.get_roles()) & EDITORS),
            'can_review': bool(set(frappe.get_roles()) & REVIEWERS)}


@frappe.whitelist(methods=['POST'])
def create(material_request, title=None, synthetic=False):
    _require(write=True)
    mr = _source('Material Request', material_request)
    if mr.material_request_type != 'Purchase' or mr.docstatus == 2:
        frappe.throw('请选择有效的采购类型物料需求单')
    key = digest(['Material Request', mr.name])
    # 锁定来源行，跨标签页重复创建只产生同一条流程。
    frappe.db.sql('select name from `tabMaterial Request` where name=%s for update', mr.name)
    existing = frappe.db.exists('Procurement Process', {'source_key': key})
    if existing:
        return _process(existing).name
    doc = frappe.get_doc({'doctype': 'Procurement Process', 'title': _text(title or mr.name, '采购主题', 140),
                         'company': mr.company, 'source_key': key,
                         'synthetic': int(_synthetic_source(mr) or synthetic in (True, 1, '1', 'true')),
                         'documents': [{'document_type': 'Material Request', 'document_name': mr.name}]})
    _save(doc)
    return doc.name


def _references(doc):
    refs = set()
    names = {'material_request': 'Material Request', 'request_for_quotation': 'Request for Quotation',
             'supplier_quotation': 'Supplier Quotation', 'purchase_order': 'Purchase Order',
             'purchase_receipt': 'Purchase Receipt', 'supply_case': 'Supply Case'}
    for row in [doc, *(doc.get('items') or [])]:
        for field, doctype in names.items():
            if row.get(field):
                refs.add((doctype, row.get(field)))
    if doc.doctype == 'Payment Entry':
        refs.update((r.reference_doctype, r.reference_name) for r in doc.references)
    if doc.doctype in ('Operations Approval', 'Quality Inspection'):
        refs.add((doc.get('source_type') or doc.get('reference_type'),
                  doc.get('source_name') or doc.get('reference_name')))
    return refs


@frappe.whitelist(methods=['POST'])
def attach(process, document_type, document_name):
    doc = _process(process, write=True)
    if doc.review_json:
        frappe.throw('流程已复盘，请先重新打开复盘再调整单据')
    source = _source(document_type, document_name, doc.company)
    known = {(r.document_type, r.document_name) for r in doc.documents}
    if (document_type, document_name) in known:
        return doc.name
    if not (_references(source) & known):
        frappe.throw('单据未引用当前流程的上游，请先关联其上游单据')
    if len(known) >= 200:
        frappe.throw('流程已达到200张关联单据上限，请拆分管理')
    doc.append('documents', {'document_type': document_type, 'document_name': document_name})
    doc.stock_review = None
    _save(doc)
    return doc.name


def _documents(process):
    return [_source(r.document_type, r.document_name, process.company) for r in process.documents]


def _snapshot(process):
    docs = _documents(process)
    rows, ordered, received, demands = [], {}, {}, []
    stock, warnings, quotes, reconciliation = [], [], [], []
    for doc in docs:
        _budget()
        rows.append({'doctype': doc.doctype, 'name': doc.name, 'modified': str(doc.modified),
                     'docstatus': doc.docstatus, 'status': doc.get('status'), 'workflow_state': doc.get('workflow_state'),
                     'outstanding_amount': doc.get('outstanding_amount')})
        if doc.docstatus == 2:
            continue
        if doc.doctype in ('Purchase Order', 'Purchase Receipt') and doc.docstatus == 1:
            target = ordered if doc.doctype == 'Purchase Order' else received
            for item in doc.items:
                key = f'{item.item_code}|{item.stock_uom}'
                target[key] = target.get(key, 0) + flt(item.stock_qty)
        if doc.doctype == 'Material Request':
            for item in doc.items:
                demands.append({'item_code': item.item_code, 'warehouse': item.warehouse,
                                'qty': flt(item.stock_qty), 'uom': item.stock_uom,
                                'need_by': str(item.schedule_date), 'ordered_qty': flt(item.ordered_qty)})
        if doc.doctype == 'Supplier Quotation':
            for item in doc.items:
                quotes.append({'name': doc.name, 'supplier': doc.supplier, 'item_code': item.item_code,
                               'qty': flt(item.qty), 'uom': item.uom, 'rate': flt(item.rate),
                               'currency': doc.currency, 'valid_till': str(doc.get('valid_till') or ''),
                               'docstatus': doc.docstatus, 'grand_total': flt(doc.grand_total),
                               'note': '逐物料报价；总额包括整张报价单，不直接当作单项到岸成本'})
        if doc.doctype == 'Purchase Invoice':
            for item in doc.items:
                issue = {'invoice': doc.name, 'item_code': item.item_code, 'qty': flt(item.qty),
                         'uom': item.uom, 'rate': flt(item.rate), 'currency': doc.currency,
                         'purchase_order': item.get('purchase_order'), 'purchase_receipt': item.get('purchase_receipt'),
                         'outstanding': flt(doc.outstanding_amount), 'state': 'needs_review'}
                issue['note'] = '须核对订单、收货和发票行，付款仍由财务人员操作'
                reconciliation.append(issue)
    # 同物料同仓库需求先合并，避免把同一份库存重复抵扣多个需求行。
    grouped = {}
    for row in demands:
        _budget()
        key = (row['item_code'], row['warehouse'], row['uom'])
        if key not in grouped:
            grouped[key] = dict(row)
        else:
            grouped[key]['qty'] += row['qty']
            grouped[key]['ordered_qty'] += row['ordered_qty']
            grouped[key]['need_by'] = min(grouped[key]['need_by'], row['need_by'])
    demands = list(grouped.values())
    for row in demands:
        if not row['warehouse']:
            warnings.append(f"{row['item_code']}缺少需求仓库")
            continue
        frappe.get_doc('Item', row['item_code']).check_permission('read')
        frappe.get_doc('Warehouse', row['warehouse']).check_permission('read')
        bins = frappe.get_list('Bin', filters={'item_code': row['item_code'], 'warehouse': row['warehouse']},
                               fields=['name', 'actual_qty', 'reserved_stock', 'modified'], limit_page_length=2)
        bin_row = bins[0] if bins else {}
        stock.append({**row, 'actual_qty': bin_row.get('actual_qty'),
                      'reserved_stock': bin_row.get('reserved_stock'),
                      'read_at': str(now_datetime()), 'version': str(bin_row.get('modified') or ''),
                      'note': '现存减锁定尚须扣除冻结及待检；库存和按时在途待仓储/采购核实，不能直接自动补购'})
    # 缺料量不在资料不全时猜测；未知字段不触发规则。
    late_days = [max(0, (date.fromisoformat(nowdate()) - date.fromisoformat(str(i.schedule_date))).days)
                 for d in docs if d.doctype == 'Purchase Order' and d.docstatus == 1
                 for i in d.items if i.schedule_date and flt(i.received_qty) < flt(i.qty)]
    context = {'late_days': max(late_days, default=0),
               'quote_count': sum(d.doctype == 'Supplier Quotation' and d.docstatus == 1
                                  and (not d.get('valid_till') or str(d.valid_till) >= nowdate()) for d in docs),
               'quality_rejected': any(d.doctype == 'Quality Inspection' and d.docstatus == 1 and d.get('status') == 'Rejected' for d in docs)}
    suppliers = {d.supplier for d in docs if d.doctype == 'Purchase Order' and d.docstatus == 1}
    if len(suppliers) == 1:
        context['supplier'] = next(iter(suppliers))
    groups = {frappe.get_cached_value('Item', r['item_code'], 'item_group') for r in demands}
    if len(groups) == 1 and None not in groups:
        context['item_group'] = next(iter(groups))
    signature = digest({'documents': rows, 'stock': [{k: v for k, v in r.items() if k != 'read_at'} for r in stock]})
    business_fingerprint = digest(rows)
    stages = calc.progress(rows, ordered, received,
                           stock_checked=_json(process.stock_review, {}).get('fingerprint') == signature,
                           reviewed=_json(process.review_json, {}).get('business_fingerprint') == business_fingerprint)
    requests = [d for d in docs if d.doctype == 'Material Request' and d.docstatus != 2]
    if any(d.docstatus != 1 or flt(d.per_ordered) < 100 - 1e-6 for d in requests):
        next(r for r in stages if r['id'] == 'order')['state'] = 'partial' if ordered else 'pending'
    # 独立跟踪每张采购单，防止A单超收掩盖B单短收；质检不合格不能标为收货完成。
    orders = [d for d in docs if d.doctype == 'Purchase Order' and d.docstatus == 1 and d.status not in ('Cancelled',)]
    receipt_stage = next(r for r in stages if r['id'] == 'receipt')
    if orders and (any(flt(i.received_qty) < flt(i.qty) - 1e-6 for d in orders for i in d.items)
                   or context['quality_rejected']):
        receipt_stage['state'] = 'partial'
    for doc in docs:
        if doc.doctype == 'Purchase Order' and doc.docstatus == 1:
            approved = any(d.doctype == 'Operations Approval' and d.source_name == doc.name
                           and d.workflow_state == '已批准' for d in docs)
            if not approved:
                warnings.append(doc.name + '未关联已批准的协同审批，请核实企业授权依据')
    if not any(d.doctype == 'Purchase Order' and d.docstatus == 1 for d in docs):
        for stage in stages:
            if stage['id'] in ('receipt', 'finance'):
                stage['state'] = 'pending'
    if orders and any(flt(d.per_billed) < 100 - 1e-6 for d in orders):
        next(r for r in stages if r['id'] == 'finance')['state'] = 'partial'
    return {'name': process.name, 'title': process.title, 'company': process.company,
            'synthetic': bool(process.synthetic), 'documents': rows, 'stock': stock, 'context': context,
            'quotations': quotes, 'reconciliation': reconciliation,
            'fingerprint': signature, 'stages': stages, 'warnings': warnings,
            'business_fingerprint': business_fingerprint,
            'review': _json(process.review_json, {}), 'at': str(now_datetime())}


@frappe.whitelist()
def overview(process):
    return _snapshot(_process(process))


@frappe.whitelist(methods=['POST'])
def confirm_stock(process, fingerprint, note):
    doc = _process(process, write=True)
    current = _snapshot(doc)
    if current['fingerprint'] != fingerprint:
        frappe.throw('业务依据已变化，请刷新后重新核对')
    doc.stock_review = _dump({'fingerprint': fingerprint, 'note': _text(note, '库存及按时在途核对说明'),
                              'by': frappe.session.user, 'at': str(now_datetime())})
    _save(doc)
    return doc.name


def _provenance(doc):
    return {'doctype': doc.doctype, 'name': doc.name, 'modified': str(doc.modified),
            'read_at': str(now_datetime()), 'created_by': doc.owner,
            'note': '单据账号不等于业务承诺人'}


@frappe.whitelist(methods=['POST'])
def record_experience(process, stage, title, situation, recommendation, conditions,
                      kind='case', source_type=None, source_name=None):
    doc = _process(process, write=True)
    if stage not in calc.STAGES or kind not in ('case', 'policy', 'expert'):
        frappe.throw('经验环节或类型无效')
    conditions = calc.validate_conditions(_json(conditions, []))
    sources = []
    if source_type or source_name:
        if (source_type, source_name) not in {(r.document_type, r.document_name) for r in doc.documents}:
            frappe.throw('经验来源须先关联至当前采购流程')
        source = _source(source_type, source_name, doc.company)
        sources.append(_provenance(source))
    snapshot = _snapshot(doc)
    values = {'doctype': 'Procurement Experience', 'title': _text(title, '经验主题', 140),
              'company': doc.company, 'procurement_process': doc.name, 'stage': stage, 'kind': kind,
              'situation': _text(situation, '场景及判断依据', 6000),
              'recommendation': _text(recommendation, '建议及例外', 1500),
              'conditions_json': _dump(conditions), 'context_json': _dump(snapshot),
              'sources_json': _dump(sources), 'outcome': 'unknown', 'verified': 0,
              'synthetic': doc.synthetic}
    values['source_key'] = digest([doc.name, stage, values['situation'], values['recommendation'],
                                    [(r['doctype'], r['name'], r['modified']) for r in sources]])
    existing = frappe.db.exists('Procurement Experience', {'source_key': values['source_key']})
    if existing:
        return existing
    experience = frappe.get_doc(values)
    _save(experience)
    return experience.name


@frappe.whitelist(methods=['POST'])
def import_judgement(process, source_type, source_name, stage):
    """导入旧判断时不伪造当时的事实快照或执行效果。"""
    if source_type not in ('Supply Decision', 'Operations Approval', 'Supply Case'):
        frappe.throw('只支持已有异常及审批判断来源')
    proc = _process(process, write=True)
    source = _source(source_type, source_name, proc.company)
    text = source.get('reason') or source.get('event_message')
    if not text:
        frappe.throw('来源没有可导入的判断文字')
    name = record_experience(process, stage, source_name + '历史判断', str(text),
                             '历史判断待复核，不能直接推广为规则',
                             [{'field': 'quote_count', 'op': 'gte', 'value': 0}],
                             source_type=source_type, source_name=source_name)
    experience = frappe.get_doc('Procurement Experience', name)
    experience.context_json = _dump({'context': {}, 'legacy': True,
                                    'note': '未保存原判断时点事实，禁止用于自动规则归纳'})
    _save(experience)
    return name


@frappe.whitelist(methods=['POST'])
def review(process, summary, evidence_type, evidence_name, baseline=None, actual=None, comparable=False):
    _require(write=True, review=True)
    doc = _process(process, write=True)
    current = _snapshot(doc)
    if (evidence_type, evidence_name) not in {(r.document_type, r.document_name) for r in doc.documents}:
        frappe.throw('复盘证据须关联至当前流程')
    evidence = _source(evidence_type, evidence_name, doc.company)
    if evidence_type not in ('Purchase Receipt', 'Quality Inspection', 'Purchase Invoice', 'Payment Entry') or evidence.docstatus != 1:
        frappe.throw('请提供已提交的履约、质量或财务结果证据，审批不等于效果')
    if not any(r['id'] == 'order' and r['state'] == 'done' for r in current['stages']):
        frappe.throw('采购订单尚未完成提交，不能结案')
    if any(r['state'] != 'done' for r in current['stages'] if r['id'] in ('receipt', 'finance')):
        frappe.throw('须先完成所有关联订单的收货质检、对账结清，再作结案复盘；中途发现先记录经验')
    value = calc.savings(baseline, actual, comparable=comparable in (True, 1, '1', 'true'), verified=True)
    doc.review_json = _dump({'summary': _text(summary, '结果及成本口径说明', 6000),
                            'evidence': _provenance(evidence), 'baseline': baseline, 'actual': actual,
                            'realized_savings': value, 'currency': frappe.get_cached_value('Company', doc.company, 'default_currency'),
                            'fingerprint': current['fingerprint'], 'business_fingerprint': current['business_fingerprint'],
                            'by': frappe.session.user, 'at': str(now_datetime()),
                            'note': '人工核实的结果；成本须同规格、数量、税费、币种及交期口径，跨环节不重复累计'})
    _save(doc)
    return _json(doc.review_json, {})


@frappe.whitelist(methods=['POST'])
def reopen(process, reason):
    _require(write=True, review=True)
    doc = _process(process, write=True)
    doc.add_comment('Comment', '重新打开复盘：' + _text(reason, '原因'))
    doc.review_json = None
    _save(doc)
    # 原复盘改变后，相应经验重新待核实，规则发布时也会重新核对样本版本。
    for row in frappe.get_all('Procurement Experience', filters={'procurement_process': doc.name}, pluck='name'):
        exp = frappe.get_doc('Procurement Experience', row)
        exp.verified = 0
        _save(exp)
    return doc.name


@frappe.whitelist(methods=['POST'])
def verify_experience(name, outcome, note):
    _require(write=True, review=True)
    exp = frappe.get_doc('Procurement Experience', name)
    exp.check_permission('read')
    proc = _process(exp.procurement_process)
    if outcome not in ('unknown', 'positive', 'negative'):
        frappe.throw('核实结果无效')
    if outcome != 'unknown' and not proc.review_json:
        frappe.throw('须先核实流程实际结果，再评价经验成效')
    exp.verified = 1
    exp.outcome = outcome
    exp.observed_at = now_datetime()
    exp.verification_json = _dump({'by': frappe.session.user, 'at': str(now_datetime()),
                                   'note': _text(note, '核实说明'), 'review_hash': digest(proc.review_json)})
    _save(exp)
    return name


def _experiences(company, include_legacy=False, deadline=None):
    rows = frappe.get_all('Procurement Experience', filters={'company': company, 'verified': 1},
                           fields=['name'], limit_page_length=501)
    if len(rows) > 500:
        frappe.throw('已超过本版500条候选经验的单次评估上限，请先按公司分批整理')
    result = []
    for row in rows:
        _budget()
        if deadline and time.monotonic() > deadline:
            raise TimeoutError('经验检索达到本轮时限')
        exp = frappe.get_doc('Procurement Experience', row.name)
        try:
            proc = _process(exp.procurement_process)
            for source in _json(exp.sources_json, []):
                _source(source['doctype'], source['name'], company)
        except frappe.PermissionError:
            frappe.clear_messages()
            continue
        snapshot = _json(exp.context_json, {})
        verification = _json(exp.verification_json, {})
        if verification.get('review_hash') != digest(proc.review_json):
            continue
        if exp.outcome != 'unknown' and _json(proc.review_json, {}).get('business_fingerprint') != _snapshot(proc)['business_fingerprint']:
            continue
        if snapshot.get('legacy') and not include_legacy:
            continue
        result.append({'name': exp.name, 'process': exp.procurement_process, 'stage': exp.stage, 'verified': bool(exp.verified),
                       'case_id': digest([company, sorted((r.document_type, r.document_name) for r in proc.documents if r.document_type == 'Purchase Order') or proc.source_key]),
                       'synthetic': bool(exp.synthetic or proc.synthetic), 'outcome': exp.outcome,
                       'observed_at': str(exp.observed_at).replace(' ', 'T'),
                       'conditions': _json(exp.conditions_json, []), 'context': snapshot.get('context', {}),
                       'title': exp.title, 'sources': _json(exp.sources_json, []),
                       'legacy': bool(snapshot.get('legacy')), 'source_excerpt': exp.situation[:800],
                       'excerpt_truncated': len(exp.situation) > 800,
                       'recommendation': exp.recommendation, 'modified': str(exp.modified)})
    return result


@frappe.whitelist(methods=['POST'])
def evolve(company):
    _require(write=True)
    frappe.get_doc('Company', company).check_permission('read')
    examples = _experiences(company)
    candidates = calc.mine(examples, as_of=str(now_datetime()).replace(' ', 'T'))
    result = []
    for candidate in candidates:
        family = digest([company, candidate['stage'], candidate['conditions'], candidate['recommendation']])
        key = digest([family, candidate])
        existing = frappe.db.exists('Procurement Rule', {'source_key': key})
        if existing:
            result.append(existing)
            continue
        # 同一公司行锁保证并发归纳版本不冲突。
        frappe.db.sql('select name from tabCompany where name=%s for update', company)
        existing = frappe.db.exists('Procurement Rule', {'source_key': key})
        if existing:
            result.append(existing)
            continue
        version = frappe.db.count('Procurement Rule', {'family': family}) + 1
        ids = set(candidate['training_ids'] + candidate['holdout_ids'])
        report = {**candidate['report'], 'holdout_ids': candidate['holdout_ids'],
                  'sample_versions': {r['name']: r['modified'] for r in examples if r['name'] in ids},
                  'at': str(now_datetime())}
        rule = frappe.get_doc({'doctype': 'Procurement Rule', 'company': company, 'stage': candidate['stage'],
                               'title': calc.STAGES[candidate['stage']] + '经验归纳 v' + str(version),
                               'family': family, 'version': version, 'state': 'candidate',
                               'conditions_json': _dump(candidate['conditions']),
                               'recommendation': candidate['recommendation'], 'training_json': _dump(candidate['training_ids']),
                               'evaluation_json': _dump(report), 'source_key': key})
        _save(rule)
        result.append(rule.name)
    return {'candidates': result, 'verified_examples': len(examples),
            'note': '至少5笔不同采购流程、按时间分离归纳与留出样本；合成/未知/矛盾标签不参与，不自动启用'}


def _rule_examples(rule, examples=None):
    examples = _experiences(rule.company) if examples is None else examples
    report = _json(rule.evaluation_json, {})
    current = {r['name']: r['modified'] for r in examples}
    if any(current.get(k) != v for k, v in report.get('sample_versions', {}).items()):
        frappe.throw('规则依据已变化或无权读取，须重新归纳验证')
    return examples


@frappe.whitelist(methods=['POST'])
def publish_rule(name, action, reason, valid_until=None):
    _require(write=True, review=True)
    rule = frappe.get_doc('Procurement Rule', name)
    rule.check_permission('read')
    frappe.get_doc('Company', rule.company).check_permission('read')
    if action not in ('activate', 'disable'):
        frappe.throw('不支持的规则操作')
    reason = _text(reason, '审核或停用原因')
    if action == 'activate':
        if not valid_until or not nowdate() <= str(valid_until) <= str(date.fromisoformat(nowdate()) + timedelta(days=365)):
            frappe.throw('请设置一年内的有效期')
        examples = _rule_examples(rule)
        replay = calc.replay({'stage': rule.stage, 'conditions': _json(rule.conditions_json, []),
                              'recommendation': rule.recommendation}, examples,
                             _json(rule.training_json, []), as_of=str(now_datetime()).replace(' ', 'T'))
        if not _json(rule.evaluation_json, {}).get('eligible') or not replay['eligible']:
            frappe.throw('留出验证不足或存在反例，不可启用')
        # 系列互斥，回退也需重新验证，防止旧反例被忽略。
        frappe.db.sql('select name from tabCompany where name=%s for update', rule.company)
        for active in frappe.get_all('Procurement Rule', filters={'company': rule.company, 'stage': rule.stage,
                                                                  'state': 'active'}, pluck='name'):
            other_rule = frappe.get_doc('Procurement Rule', active)
            if other_rule.family == rule.family or other_rule.recommendation == rule.recommendation:
                continue
            if any(calc.match(_json(rule.conditions_json, []), e['context']) and
                   calc.match(_json(other_rule.conditions_json, []), e['context']) for e in examples):
                frappe.throw('已存在适用条件重叠但建议不同的生效规则，请先审核冲突并停用旧版本')
        for other in frappe.get_all('Procurement Rule', filters={'family': rule.family, 'state': 'active'}, pluck='name'):
            if other != rule.name:
                previous = frappe.get_doc('Procurement Rule', other)
                previous.state = 'disabled'
                _save(previous)
        rule.valid_until = valid_until
    rule.state = 'active' if action == 'activate' else 'disabled'
    history = _json(rule.publication_json, [])
    history.append({'action': action, 'reason': reason, 'by': frappe.session.user, 'at': str(now_datetime())})
    rule.publication_json = _dump(history)
    _save(rule)
    return name


@frappe.whitelist()
def knowledge(process, stage='order', _deadline=None):
    proc = _process(process)
    if stage not in calc.STAGES:
        frappe.throw('采购环节无效')
    if not frappe.get_single('Procurement Settings').knowledge_enabled:
        return {'state': 'disabled', 'experiences': [], 'rules': []}
    current = _snapshot(proc)
    examples = _experiences(proc.company, include_legacy=True, deadline=_deadline)
    experiences = [r for r in examples if r['stage'] == stage and r['synthetic'] == bool(proc.synthetic)
                   and r['process'] != process and calc.match(r['conditions'], current['context'])]
    rules = []
    for row in frappe.get_all('Procurement Rule', filters={'company': proc.company, 'stage': stage,
                                'state': 'active', 'valid_until': ['>=', nowdate()]}, fields=['name'], limit_page_length=101):
        _budget()
        rule = frappe.get_doc('Procurement Rule', row.name)
        try:
            available = _rule_examples(rule, examples)
        except frappe.ValidationError:
            frappe.clear_messages()
            continue
        replay = calc.replay({'stage': rule.stage, 'conditions': _json(rule.conditions_json, []),
                              'recommendation': rule.recommendation}, available, _json(rule.training_json, []))
        if not replay['eligible'] or proc.synthetic or not calc.match(_json(rule.conditions_json, []), current['context']):
            continue
        rules.append({'name': rule.name, 'title': rule.title, 'version': rule.version, 'recommendation': rule.recommendation,
                      'conditions': _json(rule.conditions_json, []), 'valid_until': str(rule.valid_until),
                      'evaluation': _json(rule.evaluation_json, {})})
    if len({r['recommendation'] for r in rules}) > 1:
        return {'state': 'conflict', 'process': process, 'stage': stage, 'rules': [],
                'experiences': experiences[:5], 'conflicting_rules': [r['name'] for r in rules],
                'note': '当前条件命中多条不同建议的规则，已暂停规则建议，请负责人核实冲突'}
    _budget()
    return {'state': 'ready', 'process': process, 'stage': stage, 'fingerprint': current['fingerprint'],
            'rules': rules[:5], 'experiences': experiences[:5],
            'note': '历史经验仅供参考；当前事实、制度和人工授权优先，缺少条件不匹配'}


def safe_advice(process, stage='order'):
    """不调用模型；知识库失败只降级参考，不改变业务查询结果。"""
    old_timeout = None
    token = ADVICE_DEADLINE.set(time.monotonic() + 1.0)
    try:
        frappe.db.savepoint('procurement_advice')
        if frappe.db.db_type == 'mariadb':
            old_timeout = frappe.db.sql('select @@session.max_statement_time')[0][0]
            frappe.db.sql('set session max_statement_time=0.3')
        return knowledge(process, stage, _deadline=time.monotonic() + 1.0)
    except Exception:
        try:
            frappe.db.rollback(save_point='procurement_advice')
        except Exception:
            pass
        return {'state': 'unavailable', 'rules': [], 'experiences': [],
                'note': '经验参考暂不可用；本轮业务事实仍以原有工具结果为准'}
    finally:
        ADVICE_DEADLINE.reset(token)
        if old_timeout is not None:
            try:
                frappe.db.sql('set session max_statement_time=%s', (old_timeout,))
            except Exception:
                pass  # 连接已断开时仍须返回业务回答；连接回收会清除会话设置。


@frappe.whitelist()
def library(process):
    doc = _process(process)
    experiences = frappe.get_all('Procurement Experience', filters={'company': doc.company, 'procurement_process': doc.name},
                                  fields=['name', 'title', 'stage', 'verified', 'outcome', 'synthetic'],
                                  order_by='modified desc', limit_page_length=100)
    for row in experiences:
        exp = frappe.get_doc('Procurement Experience', row.name)
        row.update({'situation': exp.situation, 'recommendation': exp.recommendation,
                    'conditions': _json(exp.conditions_json, []), 'sources': _json(exp.sources_json, []),
                    'verification': _json(exp.verification_json, {})})
    # 规则详情包含跨流程证据，逐条核验权限后才返回。
    rules = []
    examples = _experiences(doc.company, include_legacy=True)
    for row in frappe.get_all('Procurement Rule', filters={'company': doc.company}, fields=['name'], limit_page_length=100):
        rule = frappe.get_doc('Procurement Rule', row.name)
        try:
            _rule_examples(rule, examples)
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.clear_messages()
            continue
        rules.append({'name': rule.name, 'title': rule.title, 'stage': rule.stage, 'state': rule.state,
                      'recommendation': rule.recommendation, 'version': rule.version,
                      'evaluation': _json(rule.evaluation_json, {})})
    return {'experiences': experiences, 'rules': rules}


def mandatory_approval(doc, method=None):
    if doc.doctype != 'Purchase Order' or not frappe.db.exists('DocType', 'Procurement Settings'):
        return
    if not frappe.get_single('Procurement Settings').mandatory_approval:
        return
    from suke_ops.approval import document_hash
    expected = document_hash(doc)
    approvals = frappe.get_all('Operations Approval', filters={'source_type': 'Purchase Order',
                                'source_name': doc.name, 'workflow_state': '已批准'}, fields=['source_hash'])
    if not any(r.source_hash == expected for r in approvals):
        frappe.throw('企业采购策略要求取得与当前订单内容一致的协同审批后才能提交')


@frappe.whitelist(methods=['POST'])
def prepare_draft(process, source_type, source_name, action):
    proc = _process(process, write=True)
    if (source_type, source_name) not in {(r.document_type, r.document_name) for r in proc.documents}:
        frappe.throw('草稿来源须属于当前流程')
    source = _source(source_type, source_name, proc.company)
    from suke_ops import hub
    if action in ('inquiry_from_request', 'purchase_from_quotation', 'receive_purchase'):
        module, required_type, _, method = hub.DRAFTS[action]
        if source_type != required_type:
            frappe.throw('草稿动作与来源不匹配')
        hub.draft_offer(module, action, source_name)
        draft = frappe.get_attr(method)(source_name)
    elif action in ('invoice_from_receipt', 'payment_from_invoice'):
        expected, target = ('Purchase Receipt', 'Purchase Invoice') if action == 'invoice_from_receipt' else ('Purchase Invoice', 'Payment Entry')
        if source_type != expected or source.docstatus != 1:
            frappe.throw('请选择已提交的收货单或采购发票')
        if not frappe.has_permission(target, 'create'):
            frappe.throw('没有创建财务单据的权限', frappe.PermissionError)
        if action == 'invoice_from_receipt':
            from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
            draft = make_purchase_invoice(source_name)
        else:
            from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
            draft = get_payment_entry('Purchase Invoice', source_name)
    else:
        frappe.throw('不支持的采购草稿动作')
    if not draft.is_new():
        frappe.throw('映射没有返回未保存草稿')
    return draft.as_dict()


@frappe.whitelist(methods=['POST'])
def discover(process):
    """仅把有真实上游引用、当前账号可读的后续单据纳入流程。"""
    proc = _process(process, write=True)
    if proc.review_json:
        frappe.throw('流程已复盘，请先重新打开复盘')
    mappings = [('Request for Quotation Item', 'material_request', 'Request for Quotation', 'Material Request'),
                ('Supplier Quotation Item', 'request_for_quotation', 'Supplier Quotation', 'Request for Quotation'),
                ('Purchase Order Item', 'material_request', 'Purchase Order', 'Material Request'),
                ('Purchase Order Item', 'supplier_quotation', 'Purchase Order', 'Supplier Quotation'),
                ('Purchase Receipt Item', 'purchase_order', 'Purchase Receipt', 'Purchase Order'),
                ('Purchase Invoice Item', 'purchase_receipt', 'Purchase Invoice', 'Purchase Receipt'),
                ('Purchase Invoice Item', 'purchase_order', 'Purchase Invoice', 'Purchase Order'),
                ('Payment Entry Reference', 'reference_name', 'Payment Entry', 'Purchase Invoice')]
    known = {(r.document_type, r.document_name) for r in proc.documents}
    added = []
    # 六轮足以覆盖需求到付款；每轮有严格总量上限，禁止无上游的模糊匹配。
    for _ in range(6):
        fresh = []
        for table, field, target, upstream in mappings:
            names = [name for dt, name in known if dt == upstream]
            if not names:
                continue
            filters = {field: ['in', names], 'parenttype': target}
            if table == 'Payment Entry Reference':
                filters['reference_doctype'] = upstream
            candidates = frappe.get_all(table, filters=filters, pluck='parent', limit_page_length=201)
            if len(candidates) > 200:
                frappe.throw('关联候选超过200条，请按流程拆分管理')
            fresh.extend((target, name) for name in candidates)
        for target, source_type in [('Operations Approval', 'Purchase Order'), ('Quality Inspection', 'Purchase Receipt')]:
            names = [name for dt, name in known if dt == source_type]
            if names:
                field = 'source_name' if target == 'Operations Approval' else 'reference_name'
                typefield = 'source_type' if target == 'Operations Approval' else 'reference_type'
                fresh.extend((target, name) for name in frappe.get_all(target, filters={field: ['in', names],
                                                                                       typefield: source_type}, pluck='name', limit_page_length=201))
        accepted = []
        for target, name in set(fresh) - known:
            try:
                _source(target, name, proc.company)
            except frappe.PermissionError:
                frappe.clear_messages()
                continue
            accepted.append((target, name))
        if not accepted:
            break
        for target, name in sorted(accepted):
            if len(known) >= 200:
                frappe.throw('关联单据超过200条，请拆分流程')
            known.add((target, name))
            proc.append('documents', {'document_type': target, 'document_name': name})
            added.append({'doctype': target, 'name': name})
    if added:
        proc.stock_review = None
        _save(proc)
    return {'added': added}
