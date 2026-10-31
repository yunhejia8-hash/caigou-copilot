"""用纯合成内容验证模型服务的工具调用与结果返回，不访问业务数据库。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'suke_ops'))
from suke_ops.agent_runtime import request_model, schema, text_schema
from configure_model import load_model_env


def verify():
    probe = schema('echo_probe', 'Return a synthetic connection check value.',
                   {'value': text_schema(['MODEL_SWITCH_OK'])}, ['value'])
    system = 'You are testing API tool calling. Follow the user instruction exactly.'
    messages = [{'role': 'user', 'content': 'Call echo_probe with value MODEL_SWITCH_OK once. After the tool result, reply MODEL_SWITCH_OK. This is a synthetic connection check.'}]
    first = request_model(system, messages, [probe])
    calls = [b for b in first.get('content', []) if b.get('type') == 'tool_use']
    assert len(calls) == 1 and calls[0].get('name') == 'echo_probe', '未返回预期工具调用'
    assert calls[0].get('input') == {'value': 'MODEL_SWITCH_OK'}, '工具参数异常'
    messages.extend([{'role': 'assistant', 'content': first['content']}, {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': calls[0]['id'], 'content': 'MODEL_SWITCH_OK'}]}])
    second = request_model(system, messages, [probe])
    text = ''.join(b.get('text', '') for b in second.get('content', []) if b.get('type') == 'text')
    assert second.get('stop_reason') == 'end_turn' and 'MODEL_SWITCH_OK' in text, '未完成工具结果往返'
    return {'status': 'PASS', 'base_url': os.environ['ANTHROPIC_BASE_URL'],
            'model': os.environ['ANTHROPIC_MODEL'], 'tool_call': True,
            'tool_result_roundtrip': True, 'data': 'synthetic_only'}


if __name__ == '__main__':
    try:
        load_model_env(ROOT / '.env')
        print(json.dumps(verify(), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status': 'FAIL', 'error': str(error) if isinstance(error, (RuntimeError, AssertionError)) else type(error).__name__}))
        sys.exit(1)
