"""采购流程与可解释规则计算；不调用模型，不执行文本中的指令。"""
import json
import math
from collections import defaultdict
from decimal import Decimal

STAGES = {'demand': '需求', 'stock': '库存核对', 'inquiry': '询价', 'comparison': '比价',
          'approval': '审批', 'order': '下单与催交', 'receipt': '收货质检',
          'finance': '对账付款', 'review': '结案复盘'}
FIELDS = {'item_group': str, 'supplier': str, 'urgent': bool, 'late_days': float,
          'quote_count': float, 'price_change_pct': float, 'shortage_qty': float,
          'quality_rejected': bool, 'unmatched_invoice': bool}
OPS = {'eq', 'ne', 'gt', 'gte', 'lt', 'lte'}


def validate_conditions(conditions):
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 8:
        raise ValueError('规则须包含1至8个结构化条件')
    for row in conditions:
        if not isinstance(row, dict) or set(row) != {'field', 'op', 'value'}:
            raise ValueError('条件只能包含field、op、value')
        kind = FIELDS.get(row['field'])
        value = row['value']
        if not kind or row['op'] not in OPS:
            raise ValueError('不支持的条件字段或运算符')
        if kind is float:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('条件数值必须有限')
        elif type(value) is not kind or (kind is str and not 1 <= len(value) <= 140):
            raise ValueError('条件类型或文本长度错误')
        if kind is not float and row['op'] not in ('eq', 'ne'):
            raise ValueError('文本和布尔条件只支持相等或不等')
    return conditions


def match(conditions, context):
    validate_conditions(conditions)
    for row in conditions:
        actual, expected = context.get(row['field']), row['value']
        kind = FIELDS[row['field']]
        if kind is float:
            if type(actual) not in (int, float) or not math.isfinite(actual):
                return False
        elif type(actual) is not kind:
            return False
        checks = {'eq': lambda: actual == expected, 'ne': lambda: actual != expected,
                  'gt': lambda: actual > expected, 'gte': lambda: actual >= expected,
                  'lt': lambda: actual < expected, 'lte': lambda: actual <= expected}
        if not checks[row['op']]():
            return False
    return True


def replay(rule, examples, training_ids=(), as_of=None):
    """仅评估未参加归纳的案例；同一流程只计一次，矛盾标签不当作成功。"""
    training = set(training_ids)
    by_process = defaultdict(list)
    for row in examples:
        if (as_of and row['observed_at'] > as_of) or row['stage'] != rule['stage'] or row['recommendation'] != rule['recommendation']:
            continue
        if row.get('verified') and not row.get('synthetic') and row.get('outcome') in ('positive', 'negative'):
            by_process[row.get('case_id', row['process'])].append(row)
    tested, positive, negative, ids = 0, 0, 0, []
    excluded_processes = {r.get('case_id', r['process']) for r in examples if r['name'] in training}
    for process, rows in by_process.items():
        if process in excluded_processes or len({r['outcome'] for r in rows}) != 1:
            continue
        row = max(rows, key=lambda r: r['observed_at'])
        if (as_of and row['observed_at'] > as_of) or row['stage'] != rule['stage']:
            continue
        # 只有采用相同建议的观察结果能验证该建议，不能把别的动作结果归因于它。
        if row['recommendation'] != rule['recommendation'] or not match(rule['conditions'], row['context']):
            continue
        tested += 1
        positive += row['outcome'] == 'positive'
        negative += row['outcome'] == 'negative'
        ids.append(row['name'])
    return {'tested': tested, 'positive': positive, 'negative': negative, 'example_ids': ids,
            'eligible': tested >= 2 and negative == 0,
            'limitation': '观察性历史回放，样本少且非因果证明；仍需人工审核并持续观察'}


def conditions_key(conditions):
    # AND条件顺序不改变语义；两处分组必须使用相同口径。
    return json.dumps(sorted(validate_conditions(conditions), key=lambda row: json.dumps(row, sort_keys=True)), sort_keys=True)


def mine(examples, as_of=None):
    groups, processes = defaultdict(list), defaultdict(list)
    for row in examples:
        if row.get('verified') and not row.get('synthetic') and row.get('outcome') in ('positive', 'negative'):
            if not as_of or row['observed_at'] <= as_of:
                processes[(row.get('case_id', row['process']), row['stage'], row['recommendation'],
                           conditions_key(row['conditions']))].append(row)
    for rows in processes.values():
        if len({r['outcome'] for r in rows}) != 1:
            continue
        row = max(rows, key=lambda r: r['observed_at'])
        validate_conditions(row['conditions'])
        if not match(row['conditions'], row['context']):
            continue
        key = json.dumps([row['stage'], conditions_key(row['conditions']),
                          row['recommendation']], sort_keys=True, ensure_ascii=False)
        groups[key].append(row)
    candidates = []
    for rows in groups.values():
        if len(rows) < 5:
            continue
        rows.sort(key=lambda r: (r['observed_at'], r['name']))
        split = max(3, len(rows) * 3 // 5)
        training = rows[:split]
        rule = {key: rows[0][key] for key in ('stage', 'conditions', 'recommendation')}
        ids = [r['name'] for r in training]
        report = replay(rule, rows[split:], as_of=as_of)
        report['eligible'] = report['eligible'] and all(r['outcome'] == 'positive' for r in training)
        candidates.append({**rule, 'support': len(rows), 'training_ids': ids,
                           'holdout_ids': [r['name'] for r in rows[split:]], 'report': report})
    return candidates


def progress(documents, ordered=None, received=None, stock_checked=False, reviewed=False):
    mapping = {'demand': 'Material Request', 'inquiry': 'Request for Quotation',
               'comparison': 'Supplier Quotation', 'approval': 'Operations Approval',
               'order': 'Purchase Order', 'receipt': 'Purchase Receipt', 'finance': 'Purchase Invoice'}
    next_steps = {'demand': '核对并提交需求单', 'stock': '逐物料核对可用库存、预留与按时在途',
                  'inquiry': '准备询价并人工收集真实报价', 'comparison': '核对同规格、单位、税费、交期与总成本',
                  'approval': '负责人核对方案及例外后审批', 'order': '批准后提交采购单并跟踪交付',
                  'receipt': '核对分批收货、合格量、拒收及退货', 'finance': '核对订单、收货、发票和付款记录',
                  'review': '记录实际结果并核实复盘'}
    result = []
    for key, title in STAGES.items():
        rows = [d for d in documents if d['doctype'] == mapping.get(key) and d.get('docstatus') != 2]
        state = 'pending'
        if rows:
            state = 'done' if all(d.get('docstatus') == 1 for d in rows) else 'partial'
        if key == 'stock':
            state = 'done' if stock_checked else 'pending'
        if key == 'approval' and rows:
            state = 'done' if all(d.get('workflow_state') == '已批准' for d in rows) else 'partial'
        if key == 'receipt' and ordered:
            state = 'done' if all((received or {}).get(k, 0) >= v - 1e-6 for k, v in ordered.items()) else 'partial' if received else 'pending'
        # 发票提交不等于付款，不从单据存在推断结清。
        if key == 'finance' and rows:
            state = 'done' if all(d.get('docstatus') == 1 and d.get('outstanding_amount') is not None
                                  and abs(float(d['outstanding_amount'])) < 0.005 for d in rows) else 'partial'
        if key == 'review':
            state = 'done' if reviewed else 'pending'
        result.append({'id': key, 'title': title, 'state': state, 'next_step': next_steps[key]})
    return result


def savings(baseline, actual, comparable=False, verified=False):
    if not comparable or not verified or baseline is None or actual is None:
        return None
    values = [Decimal(str(v)) for v in (baseline, actual)]
    if any(not v.is_finite() or v < 0 for v in values):
        raise ValueError('成本必须是非负有限数')
    return float((values[0] - values[1]).quantize(Decimal('0.01')))
