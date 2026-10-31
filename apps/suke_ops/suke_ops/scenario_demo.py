"""供应商延期场景：独立合成数据、真实 Agent 会话与结果核验。"""
import copy
import json
import uuid
from pathlib import Path

import frappe
from frappe.utils import now_datetime
from suke_ops import api, hub
from suke_ops.engine import compare
from suke_ops.seed import create_case

TAG = 'DEMO1031'

def _path():
    return Path(frappe.get_site_path('private', 'files', 'connector-scenario-demo.json'))

def seed():
    frappe.set_user('Administrator')
    case_name = create_case(TAG)
    case = frappe.get_doc('Supply Case', case_name)
    analysis = api.get_analysis(case_name)
    ctx = analysis['context']
    assert ctx['actual_qty'] == 320
    assert sum(row['qty'] for row in ctx['reservations']) == 120
    assert sum(row['qty'] for row in ctx['demands']) == 720
    assert all(plan['current_free_qty'] == 200 and plan['unconfirmed_qty'] == 200 and plan['shortage_qty'] == 400 for plan in analysis['plans'])
    manifest = json.loads(_path().read_text()) if _path().exists() else {}
    manifest.update({'tag': TAG, 'case': case_name, 'item': case.item_code, 'purchase_order': case.purchase_order,
                     'sales_orders': [row['document'] for row in ctx['demands'] if row['kind'] == 'sales'],
                     'work_orders': [row['document'] for row in ctx['demands'] if row['kind'] == 'production'],
                     'baseline': analysis, 'seeded_at': str(now_datetime())})
    # 仅计算假设，不修改供应商承诺，不把200件再叠加到原采购600件。
    conditional = copy.deepcopy(ctx)
    conditional['supplies'] = [row for row in conditional['supplies'] if row.get('purchase_order') != case.purchase_order and row.get('replaces_part_of') != case.purchase_order]
    late = next(row['eta'] for row in ctx['supplies'] if row.get('purchase_order') == case.purchase_order)
    conditional['supplies'] += [{'id': 'hypothetical-early', 'qty': 200, 'eta': case.early_arrival, 'confirmed': True},
                                {'id': 'hypothetical-late', 'qty': 400, 'eta': late, 'confirmed': True}]
    manifest['conditional_if_200_confirmed'] = compare(conditional)['plans']
    _path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return {key: manifest[key] for key in ('case', 'item', 'purchase_order', 'sales_orders', 'work_orders')}

def start(module):
    frappe.set_user('Administrator')
    manifest = json.loads(_path().read_text())
    case = manifest['case']
    common = f'仅分析合成演示场景 {case}，连接器物料 {manifest["item"]}，不要混入其他演示物料。上午9点供应商通知原定明天到的600件延后三天，可能先发200件。'
    questions = {
        'buying': '读取关联采购订单和 compare_supply_case，说明供方通知、确定/待确认供货、采购下一步与需要总控决策的事项。',
        'selling': '读取此场景销售单并用 compare_supply_case 比较，汇报重点客户与原有预留归属，说明销售优先的影响与需总控协调事项。',
        'stock': '用 compare_supply_case 核实现存、已锁定、可分配与未确认供货，汇报库存不足的证据，不得释放既有预留。',
        'manufacturing': '读取此场景工单并用 compare_supply_case 核实明日开工、BOM用量、最小批次，汇报生产优先的产量及对销售的影响。',
        'control': '先用 read_module_reports 读取本场景四模块最新上报，再调用采购Agent用compare_supply_case复核当前两方案。汇总问题和两方案的数量、影响、下一步与负责人需选的取舍。可能先发200件只作条件方案，不得计入当前可用库存。不要审批、执行或修改业务单据。',
    }
    question = common + questions[module] + '请控制在500字左右，列出工具依据与真实单号；这是分析演示，不能声称已执行未执行的动作。'
    conversation = str(uuid.uuid4())
    response = hub.start(question, module=module, conversation=conversation, request_id=str(uuid.uuid4()))
    manifest.setdefault('turns', {})[module] = {'name': response['name'], 'conversation': conversation}
    _path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    frappe.db.commit()
    return {'module': module, **manifest['turns'][module]}

def status(module):
    frappe.set_user('Administrator')
    manifest = json.loads(_path().read_text())
    result = hub.poll(manifest['turns'][module]['name'])
    if result['state'] in ('done', 'failed'):
        manifest.setdefault('results', {})[module] = result
        _path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return {'module': module, 'state': result['state'], 'answer': result.get('answer'),
            'tools': [row['tool'] for row in result.get('trace', [])]}

def verify():
    frappe.set_user('Administrator')
    manifest = json.loads(_path().read_text())
    latest = api.get_analysis(manifest['case'])
    assert latest['fingerprint'] == manifest['baseline']['fingerprint'], 'Agent分析期间业务依据发生了变化'
    assert not latest['decisions'], '分析不应创建审批或执行方案'
    all_done = all(manifest.get('results', {}).get(module, {}).get('state') == 'done' for module in (*hub.MODULE_DOCS, 'control'))
    manifest['checks'] = {'all_five_agents_done': all_done, 'business_snapshot_unchanged': True,
                          'no_decision_created': True, 'supplier_200_still_unconfirmed': not bool(latest['supplier'])}
    manifest['completed_at'] = str(now_datetime())
    _path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return manifest['checks']


def resume():
    frappe.set_user('Administrator')
    manifest = json.loads(_path().read_text())
    return {'results': {module: status(module) for module in list(manifest.get('turns', {}))}}

