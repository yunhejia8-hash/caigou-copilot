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
    </style></head><body><h1>采购需求申请表</h1><div class="meta">需求预审记录 · 待审批申请</div><table>'''+cells+'''</table><h2>预审依据与待核实事项</h2><ul>'''+notes+'''</ul><div class="footer">库存与在途如有填写，来源为申请人声明；未核实时不抵扣。此表不锁定库存或预算，不自动下单。<br>申请确认：____________　部门审批：____________　采购复核：____________</div></body></html>'''


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
            'status':LABELS[wf['preflight']['state']],
            'preview_url': '/api/method/suke_ops.procurement_workflow.application_pdf?name='+pdf['turn'] if pdf else None,
            'download_url':'/api/method/suke_ops.procurement_workflow.application_pdf?download=1&name='+pdf['turn'] if pdf else None}


def pack(wf, answer, evidence=None):
    return {'answer':answer,'workflow':wf,'application':document(wf),'offers':[],
            'trace':[{'agent':'buying','tool':'procurement_workflow','phase':wf['phase']}],
            'procurement':evidence or []}


def process(turn, prior=None):
    text = turn.question.strip()
    prior = prior if prior is not None else previous_workflow(turn.conversation, turn.creation)
    is_procurement = bool(re.search(r'需要|我要采购|采购需求|供应商[：:]|生成申请|申请表|询价|比价|查价|购买|买[0-9一二三四五六七八九十]',text))
    if not prior and not is_procurement:
        return None
    # Existing ERP order analytics continue to use their original tools.
    if not is_procurement and prior and re.search(r'订单|工单|销售|待收货',text) and not re.search('确认申请',text):
        return None
    wf = json.loads(json.dumps(prior)) if prior else {'id':'PA-'+str(intake.today()).replace('-','')+'-'+turn.name.upper(),
         'revision':0,'fields':{},'phase':'needs_info','preflight':{},'pdf':None}
    confirmation = re.fullmatch(r'确认申请\s+([A-Za-z0-9-]+)\s+第(\d+)版',text)
    if confirmation:
        if (confirmation[1] != wf['id'] or int(confirmation[2]) != wf['revision'] or wf['phase'] != 'awaiting_confirmation' or not wf.get('pdf')):
            return pack(wf,'请先查看并确认当前版本的申请表，旧版本不能进入询价。')
        wf['phase'] = 'inquiry'
        wf['confirmed_at'] = str(now_datetime())
        return pack(wf,'申请已确认，进入询价。请粘贴这笔申请的供应商报价，各供应商用空行分隔。')
    preamble = re.split(r'供应商[：:]',text,maxsplit=1)[0].strip()
    patch, issues = {}, []
    if preamble and not re.search(r'^(请)?(再|重新|重试)?(查价|比价|确认申请|预览|下载)',preamble):
        patch, issues = intake.local_extract(preamble)
        # Explicit fields can be supplemented with natural language when rules cannot understand it.
        if not patch and (not prior or wf['phase'] in ('needs_info','needs_clarification','blocked')):
            patch, issues, _ = intake.extract(preamble)
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
        if check['state'] in ('passed','review'):
            wf.update(revision=wf['revision']+1,phase='awaiting_confirmation',generated_at=str(now_datetime()))
            wf['pdf'] = create_pdf(wf,turn.name)
            return pack(wf,'预审已完成，申请表已生成。请预览核对，确认后进入询价。')
    if wf['phase'] in ('needs_info','needs_clarification','blocked'):
        check = wf['preflight']
        reasons = check.get('clarifications',[])+check.get('blockers',[])+check.get('missing',[])
        return pack(wf,'请补充或调整：'+'；'.join(dict.fromkeys(reasons))+'。')
    if wf['phase'] == 'no_purchase':
        return pack(wf,'预审结果：现有库存与按时在途已满足需求，无需新增采购。')
    if wf['phase'] == 'awaiting_confirmation':
        return pack(wf,'申请表已生成，请先预览并确认，再输入询价结果。')
    if re.search(r'供应商[：:]',text):
        # Save inquiry first; a separate explicit step starts comparison.
        wf.update(quote_source=text,quote_turn=turn.name,phase='quotations_ready')
        return pack(wf,'询价结果已保存。请核对后点击“开始比价”。')
    if text == '开始比价':
        if wf['phase'] != 'quotations_ready' or not wf.get('quote_source'):
            return pack(wf,'请先录入本笔申请的供应商报价，再开始比价。')
        result = procurement_bridge.run('procurement_compare',[wf['quote_source']],fields=wf['fields'])
        wf['phase'] = 'compared' if result.get('comparable_count',0)>=2 else 'quotations_ready'
        answer = f"已整理 {len(result.get('quotes',[]))} 份报价。"
        if result.get('lowest_total') is not None:
            answer += f"同口径最低到仓总额 {result['lowest_total']:,.2f} 元。请核对条件后确认采购方案。"
        else:
            answer += '报价条件尚未齐全，请补充标注项后再比价。'
        return pack(wf,answer,[{'tool':'procurement_compare','result':result}])
    if re.search('网上|联网|查价',text):
        result = procurement_bridge.run('procurement_search',[text],fields=wf['fields'])
        return pack(wf,'已查询网上参考价，请核对规格和来源。' if result.get('sources') else '本次未取得可核验参考价，请稍后重试。',
                    [{'tool':'procurement_search','result':result}])
    return pack(wf,'当前处于询价阶段，请提供本笔申请的供应商报价。')


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
