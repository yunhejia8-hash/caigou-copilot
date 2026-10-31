"""驱动本地五个真实 Agent：逐个运行，保留队列和权限行为。"""
import json
import subprocess
import time
from pathlib import Path

def call(method, module=None):
    command = ['docker','compose','exec','-T','backend','bench','--site','focus.local','execute','suke_ops.scenario_demo.'+method]
    if module:
        command += ['--kwargs', json.dumps({'module': module})]
    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
    if completed.returncode:
        raise RuntimeError('演示命令失败：'+completed.stdout[-2000:]+completed.stderr[-2000:])
    return json.loads(completed.stdout)

results = call('resume')['results']
for module in ('buying','selling','stock','manufacturing','control'):
    if results.get(module,{}).get('state') == 'done':
        print(module+': reuse completed result',flush=True)
        continue
    for attempt in range(3):
        print(json.dumps(call('start', module),ensure_ascii=False),flush=True)
        deadline = time.monotonic()+1000
        previous = None
        while time.monotonic() < deadline:
            result = call('status', module)
            if result['state'] != previous:
                print(json.dumps(result,ensure_ascii=False),flush=True)
                previous=result['state']
            if result['state'] in ('done','failed'):
                results[module]=result
                break
            time.sleep(5)
        else:
            raise RuntimeError(module+'超过等待时间；会话仍可在数据库查询。')
        if result['state'] == 'done':
            break
        if '模型服务请求失败' not in (result.get('answer') or ''):
            raise RuntimeError(module+'分析未完成，停止后续演示。')
        time.sleep(2)
    if result['state'] != 'done':
        print(module+'连续三次模型连接失败，记录失败并继续其他模块。',flush=True)
checks = call('verify')
print(json.dumps(checks,ensure_ascii=False),flush=True)
Path('verification/scenario-agent-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')

if not checks['all_five_agents_done']:
    raise SystemExit('部分 Agent 未完成，实际结果已保留。')
