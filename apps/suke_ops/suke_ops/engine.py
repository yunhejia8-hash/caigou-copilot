"""确定性的日期供需分配和模拟议价；不依赖模型计算数量。"""
import hashlib
import json
import math
from datetime import datetime


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def quantity(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('数量必须是非负有限数')
    return value


def check(ctx):
    quantity(ctx['actual_qty'])
    quantity(ctx.get('blocked_qty', 0))
    keys = set()
    for row in ctx['demands']:
        if row['id'] in keys:
            raise ValueError('重复需求来源')
        keys.add(row['id'])
        quantity(row['qty'])
        datetime.fromisoformat(row['need_by'])
    for row in ctx['supplies']:
        quantity(row['qty'])
        datetime.fromisoformat(row['eta'])
    for row in ctx['reservations']:
        quantity(row['qty'])
    if sum(r['qty'] for r in ctx['reservations']) + ctx.get('blocked_qty', 0) > ctx['actual_qty'] + 1e-6:
        raise ValueError('预留与冻结合计超过现存，请先核对库存')


def allocate(ctx, priority):
    check(ctx)
    locked = {}
    for r in ctx['reservations']:
        locked[r['demand_id']] = locked.get(r['demand_id'], 0) + r['qty']
    free = max(0, ctx['actual_qty'] - ctx.get('blocked_qty', 0) - sum(locked.values()))
    pools = [{'id': 'current_stock', 'qty': free, 'eta': ctx['as_of'], 'current': True}]
    pools += [dict(r, current=False) for r in ctx['supplies'] if r['confirmed'] and r['qty'] > 0]
    pools.sort(key=lambda r: datetime.fromisoformat(r['eta']))
    # 同一销售订单对应的成品需求不进入原料需求集，只有其工单 BOM 消耗进入。
    def rank(row):
        due = datetime.fromisoformat(row['need_by'])
        important = 0 if row.get('priority') == '重点客户' else 1
        kind = 0 if row['kind'] == priority else 1
        if ctx.get('scope') == 'on_hand_only':
            # A key customer's finished-goods demand is represented by its WO.
            # Production-first instead respects the earlier material need time.
            return (important, kind, due, row['id']) if priority == 'sales' else (kind, due, important, row['id'])
        return (kind, important, due, row['id'])
    demands = sorted(ctx['demands'], key=rank)
    rows = []
    for demand in demands:
        own = min(locked.get(demand['id'], 0), demand['qty'])
        remaining = demand['qty'] - own
        taken = []
        eligible = [p for p in pools if datetime.fromisoformat(p['eta']) <= datetime.fromisoformat(demand['need_by'])]
        possible = own + min(remaining, sum(p['qty'] for p in eligible))
        batch = demand.get('min_batch_qty', 0)
        # 无法形成最小生产批次时保留已锁定量，但不继续占用自由库存。
        can_start = demand.get('other_materials_ready', True) and (not batch or possible >= batch)
        if demand['kind'] != 'production' or can_start:
            for p in eligible:
                amount = min(remaining, p['qty'])
                if amount > 0:
                    taken.append({'source': p['id'], 'qty': amount, 'eta': p['eta'], 'current': p['current']})
                    p['qty'] -= amount
                    remaining -= amount
        allocated = demand['qty'] - remaining
        rows.append({**demand, 'locked_qty': own, 'allocated_qty': allocated,
                     'shortage_qty': remaining, 'current_new_qty': sum(t['qty'] for t in taken if t['current']),
                     'startable': can_start if demand['kind'] == 'production' else None,
                     'production_units': math.floor(allocated / demand.get('per_unit', 1)) if demand['kind'] == 'production' and can_start else 0,
                     'sources': taken})
    return {'id': priority + '_first', 'title': '优先保障销售交付' if priority == 'sales' else '优先保障生产开工',
            'allocations': rows, 'shortage_qty': sum(r['shortage_qty'] for r in rows),
            'current_free_qty': free, 'locked_qty': sum(locked.values()),
            'unconfirmed_qty': sum(r['qty'] for r in ctx['supplies'] if not r['confirmed']),
            'requires_owner_choice': True, 'fully_feasible': not any(r['shortage_qty'] for r in rows),
            'extra_cost': ctx.get('extra_cost', 0),
            'note': '按需用时点分配；现有预留保持原归属；未来供货仅为计划，实际到仓后才能形成库存预留。'}


def compare(ctx):
    return {'fingerprint': digest(ctx), 'plans': [allocate(ctx, 'sales'), allocate(ctx, 'production')]}


def negotiate_simulated(total_qty, deadline, late_eta, ceiling=23, max_rounds=3, refusal=False):
    """模拟供应商：先报24，再接受22.8；最多先交200件，剩余仍在原采购内。"""
    quantity(total_qty)
    if not 1 <= int(max_rounds) <= 5:
        raise ValueError('议价轮数须在1到5之间')
    ceiling = quantity(ceiling)
    history = []
    for turn in range(1, int(max_rounds) + 1):
        price = 24 if turn == 1 else 22.8
        history.append({'round': turn, 'buyer': f'请确认提前到仓数量及时间，单价上限{ceiling:g}。',
                        'supplier': '模拟供应商拒绝提前交付' if refusal else f'模拟报价：最多200件，单价{price:g}，可在需求时点前到仓。',
                        'price': price, 'accepted': not refusal and price <= ceiling})
        if not refusal and price <= ceiling:
            early = min(200, total_qty)
            return {'status': 'confirmed_simulation', 'mode': '模拟供应商', 'rounds': history,
                    'schedule': [{'id': 'sim-early', 'qty': early, 'eta': deadline, 'confirmed': True, 'unit_price': price},
                                 {'id': 'sim-late', 'qty': total_qty - early, 'eta': late_eta, 'confirmed': True, 'unit_price': 22}],
                    'extra_cost': round(early * (price - 22), 2),
                    'note': '只调整原采购的分批承诺；总量不增加，未向真实供应商发送消息。'}
    return {'status': 'needs_human', 'mode': '模拟供应商', 'rounds': history, 'schedule': None,
            'extra_cost': None, 'note': '未满足授权条件或达到轮数上限，转人工处理。'}
