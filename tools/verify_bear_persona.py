"""仅发送合成对话，验证实际模型的陪聊和分流，不访问业务数据。"""
import json
from concurrent.futures import ThreadPoolExecutor
from suke_ops.bear_persona import decide

CASES = [
    ('打个招呼', [], 'chat'), ('给我打气，今天有点累', [], 'chat'),
    ('恭喜发财', [{'role': 'assistant', 'content': '采购订单需要核实交期。'}], 'chat'),
    ('做采购真累，陪我聊两句', [], 'chat'),
    ('采购询价是什么意思', [], 'chat'),
    ('请查询我们公司的待收货采购订单', [], 'buying'),
    ('请核实物料DEMO-001当前库存', [], 'stock'),
    ('销售交付和生产抢同一批料，请协调各部门', [], 'control'),
    ('交期呢', [{'role': 'user', 'content': '请核实采购订单PO-DEMO-001'}, {'role': 'assistant', 'content': '正在核实采购订单。'}], 'buying'),
]


def run(case):
    message, history, expected = case
    try:
        result = decide(message, history)
        actual = result.get('module', 'chat')
        return {'message': message, 'expected': expected, 'actual': actual, 'pass': actual == expected, **result}
    except Exception as error:
        return {'message': message, 'pass': False, 'error': type(error).__name__}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, CASES))
    print(json.dumps({'pass': all(row['pass'] for row in results), 'cases': results}, ensure_ascii=False))
