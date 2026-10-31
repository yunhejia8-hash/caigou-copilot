"""Expose the user's existing procurement-agent backend to Frappe conversations.
No original SQLite initializers, HTTP server, order actions or stock writes run here.
"""
import json
import re
from suke_ops.vendor.procurement_agent import chat_service as intake, procurement as quotes


def demand_from(texts):
    # Merge demand patches per user turn, not a concatenated transcript. Follow-up
    # commands ("重新比价", "查刚才的价格") must not be reinterpreted as demand.
    fields, errors, candidates = {}, [], []
    for text in texts:
        piece = re.split(r'供应商[：:]', text, maxsplit=1)[0].strip()
        if not piece or not re.search(r'需要|采购需求|我要采购|预算|规格|数量|物料|改为|改成|调整为|到货|送到|单位', piece):
            continue
        candidates.append(piece)
        patch, issues = intake.local_extract(piece)
        clean, invalid = intake.validate_fields(patch)
        fields.update(clean)
        errors.extend(issues+invalid)
    core = ('quantity','unit','spec','needed_by')
    if not errors and all(fields.get(k) for k in core) and (fields.get('material_name') or fields.get('material_id')):
        return fields, [], 'local'
    if not candidates:
        return fields, ['请先提供采购物料、规格、数量、单位和要求到货日期'], 'none'
    # Only actual demand descriptions reach the original model extractor.
    patch, ambiguities, engine = intake.extract('\n'.join(candidates)[-8000:])
    fields, invalid = intake.validate_fields(patch)
    return fields, ambiguities+invalid, engine


def run(operation, texts, fields=None):
    if operation not in ('procurement_intake','procurement_compare','procurement_search'):
        raise ValueError('未授权采购工具')
    if fields is None:
        fields, errors, engine = demand_from(texts)
    else:
        fields, errors, engine = dict(fields), [], "confirmed_application"
    required = [('quantity','需求数量'),('unit','计量单位'),('spec','规格'),('needed_by','要求到货日期')]
    missing = [label for key,label in required if not fields.get(key)]
    if not (fields.get('material_name') or fields.get('material_id')):
        missing.insert(0,'物料名称或编码')
    result = {'backend': 'ivvli/procurement-agent', 'operation': operation, 'fields': fields,
              'missing': missing, 'errors': errors, 'engine': engine,
              'note': '需求与报价为用户提供、待核实。未创建ERP单据、未审批、未下单，未变更库存。'}
    if operation == 'procurement_intake' or errors or missing:
        return result
    if operation == 'procurement_search':
        if not quotes.model_enabled():
            return dict(result, errors=['未配置支持搜索的模型服务'])
        search = {k:fields.get(k) for k in ('material_name','material_id','spec','quantity','unit')}
        out = quotes.response({'instructions':'搜索公开采购参考价，核实物料规格、计价单位、数量阶梯和税运。提供可点击来源。查不到就明确说查不到。结果不是供应商承诺，不自动下单，不搜索用户姓名、部门、预算。',
                               'input': intake.dump(search), 'tools':[{'type':'web_search'}], 'tool_choice':'required'})
        sources = {a['url']:a.get('title') or a['url'] for item in out.get('output',[]) for part in item.get('content',[]) for a in part.get('annotations',[]) if a.get('type')=='url_citation' and a.get('url','').startswith(('https://','http://'))}
        if not any(x.get('type')=='web_search_call' and x.get('status')=='completed' for x in out.get('output',[])) or not sources or not quotes.output_text(out).strip():
            return dict(result,errors=['搜索未返回可核验的来源，本轮不生成市场参考'])
        return dict(result,market_reference=quotes.output_text(out),sources=sources,reference_only=True)
    source = next((text for text in reversed(texts) if re.search(r'供应商[：:]',text)), None)
    if not source:
        return dict(result, missing=['请粘贴供应商实际报价文字，各供应商用空行分隔'])
    extracted = quotes.model_quotes(source) if quotes.model_enabled() else quotes.local_quotes(source)
    rows=[]
    for index, raw in enumerate(extracted[:20]):
        q=quotes.validate_quote(raw)
        issues=quotes.issues(q,fields)
        if not q.get('valid_until'):issues.append('报价有效期待核实')
        if not q.get('payment'):issues.append('付款条件待核实')
        if q.get('delivery') and q['delivery']<str(intake.today()):issues.append('承诺交期已过')
        rows.append({'source':f'本会话用户报价#{index+1}', 'quote':q, 'total':quotes.total(q),'issues':issues})
    eligible=[r for r in rows if not r['issues'] and r['total'] is not None]
    minimum=min((r['total'] for r in eligible),default=None)
    result.update(quotes=rows,source_text=source,comparable_count=len(eligible),
                  lowest_total=minimum if len(eligible)>=2 else None,
                  lowest_sources=[r['source'] for r in eligible if r['total']==minimum] if len(eligible)>=2 else [],
                  comparison_note='仅对条件齐全、符合需求的报价比较含税单价×数量+运费。不换算单位、不补税或运费。不新增综合评分。只使用最近一条含供应商标记的报价消息，修改报价请给出完整新版。')
    return result
