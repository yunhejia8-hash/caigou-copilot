"""Server-enforced intake -> preflight -> immutable PDF -> inquiry -> comparison.
Workflow snapshots live in owner-scoped Agent Turn records; no second inventory ledger.
"""
import ast
import hashlib
import inspect
import json
import re
import sqlite3
from html import escape
from contextlib import closing

import frappe
from frappe.utils import now_datetime
from suke_ops.vendor.procurement_agent import chat_service as intake
from suke_ops import procurement_bridge

MODULES = ('buying', 'buying-demand', 'buying-inquiry', 'buying-comparison')
SCHEMA = next(ast.literal_eval(n.args[0]) for n in ast.walk(ast.parse(inspect.getsource(intake.init_tables)))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'executescript')
LABELS = {'needs_info':'待补充需求', 'needs_clarification':'预审待澄清', 'blocked':'预审未通过',
          'review':'预审完成 · 待人工复核', 'passed':'预审通过 · 待审批', 'no_purchase':'无需新增采购'}


def preflight(fields, issues=(), duplicates=()):
    """Run the original rule engine in memory; never initialize its local/demo DB."""
    def identity(f):
        return f.get('material_id') or ('TMP-'+hashlib.sha256(intake.dump([f.get('material_name'),f.get('spec'),f.get('unit')]).encode()).hexdigest()[:16] if f.get('material_name') else None)
    scratch_fields = dict(fields)
    if identity(fields):
        scratch_fields['material_id'] = identity(fields)
    else:
        scratch_fields.pop('material_id',None)
    with closing(sqlite3.connect(':memory:')) as c:
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        for row in duplicates:
            c.execute('INSERT INTO chat_requests VALUES (?,?,?,?,?,?)',
                      (row['id'], intake.dump({**row['fields'],'material_id':identity(row['fields'])}), '[]', intake.dump({'state': row['state']}), '', ''))
        result = intake.review(c, scratch_fields, '__current__', issues=list(issues))
    # Keep the applicant's identity instead of the temporary in-memory material ID.
    result['fields'] = {**result['fields'], 'material_id': fields.get('material_id')}
    if result.get('purchase_quantity') == 0 and not any(result.get(k) for k in ('missing','clarifications','blockers')):
        result['state'] = 'no_purchase'
    result['new_material'] = bool(result.get('new_material'))
    if not fields.get('spec'):
        result['missing'].append('物料规格')
        if result['state'] not in ('blocked','needs_clarification'):
            result['state'] = 'needs_info'
    result['history_note'] = '原预审规则已执行；当前尚未迁移原采购历史库，历史数量/价格缺少可核验来源时保留人工复核。'
    result['department_budget_checked'] = False
    result['warnings'].append('收货仓为申请人填写的拟用仓库，尚未核实ERP仓库主数据；正式建单前须匹配公司与仓库。')
    return result


def previous_workflow(conversation, before=None):
    filters = {'owner': frappe.session.user, 'conversation': conversation,
               'agent_module': ['in', list(MODULES)], 'state': 'done'}
    if before:
        filters['creation'] = ['<', before]
    # Query all relevant turns so a long conversation never silently forgets its application.
    for row in frappe.get_list('Agent Turn', filters=filters, fields=['name','result'], order_by='creation desc, name desc', limit_page_length=0):
        data = json.loads(row.result or '{}')
        if data.get('workflow'):
            return data['workflow']
    return None


def duplicate_candidates(application_id):
    latest = {}
    for row in frappe.get_list('Agent Turn', filters={'owner':frappe.session.user,'state':'done','agent_module':['in',list(MODULES)]},
                               fields=['result'],order_by='creation desc',limit_page_length=0):
        wf = json.loads(row.result or '{}').get('workflow')
        if wf and wf['id'] != application_id and wf['id'] not in latest:
            latest[wf['id']] = {'id':wf['id'],'fields':wf['fields'],'state':wf['preflight']['state']}
    return list(latest.values())


def inquiry_fields(wf):
    """保留申请原始数量，询比价使用本版预审的净采购数量。"""
    fields = dict(wf['fields'])
    quantity = wf.get('preflight', {}).get('purchase_quantity')
    if quantity is not None:
        fields['quantity'] = quantity
    return fields


def application_html(wf):
    f, check = wf['fields'], wf['preflight']
    def show(value, suffix=''):
        return '待核实' if value is None or value == '' else str(value)+suffix
    unit = f.get('unit') or ''
    rows = [('申请编号 / 版本',f"{wf['id']} / 第{wf['revision']}版"),('申请人 / 部门',f"{f.get('applicant','')} / {f.get('department','')}"),
            ('物料名称 / 编码',f"{f.get('material_name') or '待核实'} / {f.get('material_id') or '待正式建档'}"),
            ('规格 / 计量单位',f"{f.get('spec','')} / {unit}"),('申请数量',show(f.get('quantity'),unit)),
            ('用途与数量依据',show(f.get('purpose'))),('收货仓 / 要求到货',f"{f.get('warehouse','')} / {f.get('needed_by','')}"),
            ('本次预算上限',show(f.get('budget'),' 元')),('暂估采购单价',show(check.get('unit_price'),' 元/'+unit)),
            ('当前可用 / 按时在途',show(f.get('available_stock'),unit)+' / '+show(f.get('on_time_incoming'),unit)),
            ('建议新增采购 / 暂估金额',show(check.get('purchase_quantity'),unit)+' / '+show(check.get('estimated_total'),' 元')),
            ('本版询比价数量',show(inquiry_fields(wf).get('quantity'),unit)),
            ('价格依据', (check.get('price_source') or {}).get('description') or '尚未询价，不以预算倒推单价'),
            ('预审结论',LABELS[check['state']]),('生成时间',wf['generated_at'])]
    cells = ''.join('<tr><th>'+escape(k)+'</th><td>'+escape(v)+'</td></tr>' for k,v in rows)
    findings = list(check.get('warnings',[])) + [check['history_note'],'部门预算余额、技术适配和最终价格尚需相关人员核实；预审与申请确认均不代替审批。']
    notes = ''.join('<li>'+escape(x)+'</li>' for x in findings)
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    body{font-family:"Noto Sans CJK SC",sans-serif;color:#243b39;font-size:11px;line-height:1.55;margin:0}
    h1{font-size:25px;margin:0 0 6px} .meta{color:#627c72;margin-bottom:16px}table{width:100%;border-collapse:collapse;table-layout:fixed}
    tr{page-break-inside:avoid}th,td{border:1px solid #dbe5df;padding:7px 10px;text-align:left;word-wrap:break-word}th{width:29%;font-weight:500;background:#f0f5f1}
    h2{font-size:13px;margin:15px 0 5px}ul{padding-left:18px;font-size:10px;color:#586f64}li{margin:3px 0}.footer{margin-top:14px;border-top:1px solid #dbe5df;padding-top:10px;font-size:10px;color:#63786d}
    </style></head><body><h1>采购需求申请表</h1><div class="meta">需求预审记录 · 待审批申请</div><table>'''+cells+'''</table><h2>预审依据与待核实事项</h2><ul>'''+notes+'''</ul><div class="footer">库存与在途按申请人声明试算，尚未核实ERP库存，正式建单前须复核；未提供时不抵扣，询比价暂按申请数量。此表不锁定库存或预算，不自动下单。<br>申请确认：____________　部门审批：____________　采购复核：____________</div></body></html>'''


def create_pdf(wf, turn_name):
    from frappe.utils.pdf import get_pdf
    content = get_pdf(application_html(wf), options={'page-size':'A4','margin-top':'12mm','margin-bottom':'12mm',
                      'margin-left':'14mm','margin-right':'14mm','disable-javascript':'','disable-local-file-access':''})
    from frappe.utils.file_manager import save_file
    file = save_file(f"{wf['id']}-v{wf['revision']}.pdf",content,'Agent Turn',turn_name,is_private=1)
    return {'file':file.name,'turn':turn_name}


def document(wf):
    pdf = wf.get('pdf')
    return {'id':wf['id'],'revision':wf['revision'],'phase':wf['phase'], 'preflight_state':wf['preflight']['state'],
            'approval':wf.get('approval'),
            'status':(wf.get('approval') or {}).get('label') or {'inquiry':'申请已确认 · 询价中','quotations_ready':'申请已确认 · 报价待核对','compared':'申请已确认 · 比价完成'}.get(wf['phase'],LABELS[wf['preflight']['state']]),
            'preview_url': '/api/method/suke_ops.procurement_workflow.application_pdf?name='+pdf['turn'] if pdf else None,
            'download_url':'/api/method/suke_ops.procurement_workflow.application_pdf?download=1&name='+pdf['turn'] if pdf else None}


def pack(wf, answer, evidence=None):
    return {'answer':answer,'workflow':wf,'application':document(wf),'offers':[],
            'trace':[{'agent':'buying','tool':'procurement_workflow','phase':wf['phase']}],
            'procurement':evidence or []}


def process(turn, prior=None):
    text = turn.question.strip()
    prior = prior if prior is not None else previous_workflow(turn.conversation, turn.creation)
    # 已有订单的查询仍交给原工具；“需要催交”不是新采购申请。
    new_request = bool(re.search(r'我要采购|采购需求|(?:生成|创建|登记|提交|修改).{0,5}(?:申请|需求)|(?:需要|采购|购买|买)\s*\d+(?:\.\d+)?\s*(?:个|件|盒|包|台|套|米|公斤|千克)', text))
    denied_request = bool(re.search(r'(?:不要|无需|暂不|不|别)(?:再|自动)?(?:生成|创建|登记|提交|修改).{0,5}(?:申请|需求)', text))
    application_command = bool(re.search(r'^确认申请\s|^(?:开始比价|判断审批)$|供应商[：:]', text))
    if re.search(r'订单|工单|销售|待收货|催交', text) and not (new_request and not denied_request) and not application_command:
        return None
    is_procurement = (new_request and not denied_request) or bool(re.search(r'供应商[：:]|生成申请|申请表|询价|比价|查价',text))
    is_procurement = is_procurement or bool(re.search('采购',text) and re.search('数量|规格|预算|预审',text))
    if not prior and not is_procurement:
        return None
    # Existing ERP order analytics continue to use their original tools.
    if not is_procurement and prior and re.search(r'订单|工单|销售|待收货',text) and not re.search('确认申请',text):
        return None
    wf = json.loads(json.dumps(prior)) if prior else {'id':'PA-'+str(intake.today()).replace('-','')+'-'+turn.name.upper(),
         'revision':0,'fields':{},'phase':'needs_info','preflight':{},'pdf':None}
    image = json.loads(getattr(turn, 'result', None) or '{}').get('quote_image')
    if image:
        guard(wf, 'procurement_search')
        from suke_ops.procurement_images import extract
        if image in wf.get('quote_images', []):
            return pack(wf, '这张图片已录入，请继续上传其他供应商报价或开始比价。')
        try:
            rows = extract(image)
        except ValueError as error:
            return pack(wf, '图片识别未完成：'+str(error))
        if not rows:
            return pack(wf, '图片中未识别到报价，请上传清晰的报价单或补充文字。')
        if len(wf.get('saved_quotes', wf.get('image_quotes', []))) + len(rows) > 20:
            return pack(wf, '最多支持20份报价，请整理后用完整报价文字替换。')
        wf['quote_images'] = wf.get('quote_images', []) + [image]
        wf['image_quotes'] = wf.get('saved_quotes', wf.get('image_quotes', [])) + rows
        wf.pop('saved_quotes', None)
        wf.pop('quote_source', None)
        wf.update(phase='quotations_ready', quote_turn=turn.name)
        wf.pop('approval_reminder', None)
        wf.pop('approval', None)
        preview = procurement_bridge.run('procurement_compare', [], fields=inquiry_fields(wf), image_quotes=wf['image_quotes'], preview=True)
        return pack(wf, '图片报价已提取，请核对表格；识别错误请粘贴完整新版报价替换。确认后点击“开始比价”。', [{'tool':'quote_image_extraction','result':preview}])
    confirmation = re.fullmatch(r'确认申请\s+([A-Za-z0-9-]+)\s+第(\d+)版',text)
    if confirmation:
        if (confirmation[1] != wf['id'] or int(confirmation[2]) != wf['revision'] or wf['phase'] != 'awaiting_confirmation' or not wf.get('pdf')):
            return pack(wf,'请先查看并确认当前版本的申请表，旧版本不能进入询价。')
        wf['phase'] = 'inquiry'
        wf['confirmed_at'] = str(now_datetime())
        return pack(wf,'申请已确认，进入询价。请粘贴供应商报价（用空行分隔），或点击“发送报价图片”。')
    quote_hint = bool(re.search(r'供应商|报价|付款|税率|含税|未税',text))
    change = r'(?:数量|预算|物料|规格|收货仓|需求日期|用途)(?:调整为|改为|改成|变更为)'
    demand_change = bool(re.search(r'(?:需求|申请|采购)'+change,text) or re.search(r'预算(?:调整为|改为|改成)',text) or (not quote_hint and re.search(change,text)))
    quote_message = quote_hint and not demand_change and not re.search('网上|联网|查价',text)
    if prior and quote_message and wf['phase'] == 'awaiting_confirmation':
        return pack(wf,'申请表已生成，请先预览并确认，再输入询价结果。')
    if prior and quote_message and wf['phase'] in ('inquiry','quotations_ready','compared'):
        try:
            if re.search(r'供应商[：:]',text) and not (re.search('补充|更新|修改|调整',text) and (wf.get('saved_quotes') or wf.get('image_quotes') or wf.get('quote_source'))):
                preview = procurement_bridge.run('procurement_compare',[text],fields=inquiry_fields(wf),preview=True)
                rows = [row['quote'] for row in preview.get('quotes',[])]
            else:
                rows = wf.get('saved_quotes') or wf.get('image_quotes')
                if not rows and wf.get('quote_source'):
                    previous = procurement_bridge.run('procurement_compare',[wf['quote_source']],fields=inquiry_fields(wf),preview=True)
                    rows = [row['quote'] for row in previous.get('quotes',[])]
                if not rows:
                    return pack(wf,'请先按供应商全名录入报价，各供应商用空行分隔。')
                rows = procurement_bridge.update_quotes(rows,text)
                preview = procurement_bridge.run('procurement_compare',[],fields=inquiry_fields(wf),image_quotes=rows,preview=True)
        except ValueError as error:
            return pack(wf,str(error)+' 原申请和报价已保留。')
        if not rows:
            return pack(wf,'未提取到有效报价，原申请和报价已保留，请补充供应商报价。')
        wf.update(saved_quotes=rows,quote_source=text,quote_turn=turn.name,phase='quotations_ready')
        for key in ('image_quotes','quote_images','approval_reminder','approval'):
            wf.pop(key,None)
        return pack(wf,'报价已更新，请核对下表后点击“开始比价”。申请确认状态保持不变。',[{'tool':'quote_preview','result':preview}])
    preamble = re.split(r'供应商[：:]',text,maxsplit=1)[0].strip()
    patch, issues = {}, []
    if preamble and not re.search(r'^(请)?(再|重新|重试)?(查价|比价|确认申请|预览|下载)',preamble):
        patch, issues = intake.local_extract(preamble)
        # Explicit fields can be supplemented with natural language when rules cannot understand it.
        if (not prior or wf['phase'] in ('needs_info','needs_clarification','blocked')) and not all(({**wf['fields'],**patch}).get(k) for k in ('material_name','quantity','unit','purpose','budget','spec','warehouse','needed_by','applicant','department')):
            extra, ambiguities, _ = intake.extract(preamble)
            patch = {**extra,**patch}
            issues = list(dict.fromkeys(issues+ambiguities))
    changed = any(wf['fields'].get(k) != v for k,v in patch.items())
    needs_review = not wf.get('preflight') or changed or bool(issues)
    if needs_review:
        fields = {**wf['fields'],**patch}
        if any(k in patch and patch[k] != wf['fields'].get(k) for k in ('needed_by','warehouse','material_id','material_name','spec','unit')):
            for key in ('available_stock','on_time_incoming'):
                if key not in patch:
                    fields.pop(key,None)
        if any(k in patch and patch[k] != wf['fields'].get(k) for k in ('material_id','material_name','spec','unit')) and 'unit_price' not in patch:
            fields.pop('unit_price',None)
        check = preflight(fields, issues, duplicate_candidates(wf['id']))
        wf.update(fields=fields,preflight=check,pdf=None,phase=check['state'])
        wf.pop('confirmed_at',None)
        for key in ('quote_source','saved_quotes','image_quotes','quote_images','approval_reminder','approval'):
            wf.pop(key,None)
        if check['state'] in ('passed','review'):
            wf.update(revision=wf['revision']+1,phase='awaiting_confirmation',generated_at=str(now_datetime()))
            wf['pdf'] = create_pdf(wf,turn.name)
            return pack(wf,'预审已完成，申请表已生成。请预览核对，确认后进入询价。')
    if wf['phase'] in ('needs_info','needs_clarification','blocked'):
        check = wf['preflight']
        reasons = check.get('clarifications',[])+check.get('blockers',[])+check.get('missing',[])
        return pack(wf,'请补充或调整：'+'；'.join(dict.fromkeys(reasons))+'。')
    if wf['phase'] == 'no_purchase':
        return pack(wf,'按申请人声明试算，库存与按时在途可满足需求，暂不新增采购；尚未核实ERP库存，正式建单前须复核。')
    if wf['phase'] == 'awaiting_confirmation':
        return pack(wf,'申请表已生成，请先预览并确认，再输入询价结果。')
    if text in ('开始比价','判断审批'):
        if wf['phase'] not in ('quotations_ready','compared') or not (wf.get('quote_source') or wf.get('saved_quotes') or wf.get('image_quotes')):
            return pack(wf,'请先录入本笔申请的供应商报价，再开始比价。')
        result = procurement_bridge.run('procurement_compare',[] if wf.get('saved_quotes') else [wf.get('quote_source','')],fields=inquiry_fields(wf), **({'image_quotes':wf.get('saved_quotes') or wf['image_quotes']} if wf.get('saved_quotes') or wf.get('image_quotes') else {}))
        wf['phase'] = 'compared' if result.get('comparable_count',0)>=2 else 'quotations_ready'
        answer = f"已整理 {len(result.get('quotes',[]))} 份报价。"
        wf.pop('approval_reminder', None)
        wf.pop('approval', None)
        if result.get('lowest_total') is not None and wf['phase'] == 'compared':
            answer += f"同口径最低到仓总额 {result['lowest_total']:,.2f} 元。"
            wf['approval'] = evaluate_approval(result['lowest_total'], wf['fields'].get('budget'))
            wf['approval'].update(application_id=wf['id'], revision=wf['revision'], evaluated_at=str(now_datetime()))
            answer += wf['approval']['message']
        else:
            answer += '报价条件尚未齐全，请补充标注项后再比价。'
        return pack(wf,answer,[{'tool':'procurement_compare','result':result}])
    if re.search('网上|联网|查价',text):
        result = procurement_bridge.run('procurement_search',[text],fields=inquiry_fields(wf))
        return pack(wf,'已查询网上参考价，请核对规格和来源。' if result.get('sources') else '本次未取得可核验参考价，请稍后重试。',
                    [{'tool':'procurement_search','result':result}])
    if wf.get('approval'):
        return pack(wf,wf['approval']['message'])
    return pack(wf,'当前处于询价阶段，请提供本笔申请的供应商报价。')


def evaluate_approval(total, budget=None):
    from decimal import Decimal
    amount = Decimal(str(total))
    if not amount.is_finite() or amount < 0:
        raise ValueError('无效采购金额')
    required = amount > Decimal('100000')
    label = '申请快照 · 待人工审核' if required else '申请快照 · 审批自动通过'
    message = '申请快照审批结果：'+label+'。'+('最终方案金额超过100,000元，需人工审核后才能继续。' if required else '最终方案金额不超过100,000元，按金额规则自动通过。')+'此判定仅用于本版申请快照，不代表ERP单据审批、提交或下单。'
    over_budget = budget is not None and amount > Decimal(str(budget))
    if over_budget:
        message += '同时超过申请预算，请调整方案或申请追加预算。'
    return {'scope':'application_snapshot','threshold':100000,'amount':float(amount),'requires_escalation':required,'over_budget':over_budget,
            'status':'pending_review' if required else 'approved','label':label,'basis':'最低可比报价含税到仓总额','message':message}


def guard(context, operation):
    if operation in ('procurement_compare','procurement_search') and (not context or context.get('phase') not in ('inquiry','quotations_ready','compared') or not context.get('confirmed_at') or not context.get('pdf')):
        raise ValueError('请先完成需求预审、生成申请表并确认，之后才能进入询价和比价。')
    if operation == 'procurement_compare' and context.get('phase') not in ('quotations_ready','compared'):
        raise ValueError('请先保存本笔申请的询价结果，再开始比价。')


@frappe.whitelist()
def application_pdf(name,download=0):
    from suke_ops.hub import owned_turn
    turn = owned_turn(name)
    wf = json.loads(turn.result or '{}').get('workflow')
    if not wf or not wf.get('pdf') or wf['pdf']['turn'] != turn.name:
        frappe.throw('当前消息没有生成申请表。')
    file = frappe.get_doc('File',wf['pdf']['file'])
    if not file.is_private or file.attached_to_doctype != 'Agent Turn' or file.attached_to_name != turn.name:
        frappe.throw('申请文件关联无效。',frappe.PermissionError)
    from werkzeug.wrappers import Response
    filename = f"{wf['id']}-v{wf['revision']}.pdf"
    disposition = 'attachment' if str(download)=='1' else 'inline'
    return Response(file.get_content(),mimetype='application/pdf',headers={
        'Content-Disposition':disposition+'; filename="'+filename+'"',
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
