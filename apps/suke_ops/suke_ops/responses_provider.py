"""Responses transport for the procurement project's existing model configuration."""
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def configured():
    return bool(os.environ.get('OPENAI_API_KEY') and os.environ.get('OPENAI_MODEL'))


def call(payload):
    base = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    p = urlsplit(base)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise RuntimeError('模型接口地址必须是有效 HTTPS 地址。')
    body = dict(payload, model=os.environ['OPENAI_MODEL'], store=False)
    request = Request(base+'/responses', json.dumps(body, ensure_ascii=False).encode(),
                      {'Authorization': 'Bearer '+os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json',
                       'User-Agent': 'ProcurementPrototype/1.0', 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
        if result.get('status') != 'completed':
            raise RuntimeError('模型回答不完整，请重试。')
        return result
    except HTTPError as error:
        raise RuntimeError(f'模型服务返回 HTTP {error.code}，请检查配置或额度。') from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError('模型连接失败或超时，请重试。') from None


def request_model(system, messages, tools):
    inputs = []
    for message in messages:
        content = message['content']
        if isinstance(content, str):
            inputs.append(message)
        else:
            for block in content:
                if block['type'] == 'responses_item':
                    inputs.append(block['item'])
                elif block['type'] == 'tool_use':
                    # Original output retained in a responses_item, including reasoning.
                    if not block.get('preserved'):
                        inputs.append({'type': 'function_call', 'call_id': block['id'], 'name': block['name'],
                                       'arguments': json.dumps(block['input'], ensure_ascii=False)})
                elif block['type'] == 'tool_result':
                    inputs.append({'type': 'function_call_output', 'call_id': block['tool_use_id'], 'output': block['content']})
                elif block['type'] == 'text' and not block.get('preserved'):
                    inputs.append({'role': message['role'], 'content': block['text']})
    output = call({'instructions': system, 'input': inputs, 'include': ['reasoning.encrypted_content'],
                   'tools': [{'type': 'function', 'name': t['name'], 'description': t['description'],
                              'parameters': t['input_schema'], 'strict': False} for t in tools]})
    blocks = []
    for item in output.get('output', []):
        blocks.append({'type': 'responses_item', 'item': item})
        if item['type'] == 'function_call':
            try:
                args = json.loads(item['arguments'])
            except (ValueError, TypeError):
                raise RuntimeError('模型工具参数格式错误。') from None
            blocks.append({'type': 'tool_use', 'name': item['name'], 'id': item['call_id'], 'input': args, 'preserved': True})
        elif item['type'] == 'message':
            blocks.extend({'type': 'text', 'text': part['text'], 'preserved': True} for part in item.get('content', []) if part['type'] == 'output_text')
    return {'content': blocks, 'stop_reason': 'tool_use' if any(b['type'] == 'tool_use' for b in blocks) else 'end_turn'}
