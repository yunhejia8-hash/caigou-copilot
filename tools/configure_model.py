"""交互填写模型 Key，仅更新本地 .env 的模型配置，不回显凭据。"""
import argparse
import getpass
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEYS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL', 'ANTHROPIC_AUTH_TOKEN')


def load_model_env(path):
    """只读取模型配置；调用进程已有环境变量优先，与 Compose 保持一致。"""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.strip() in MODEL_KEYS:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def configure(path, token, base_url='https://api.openai-next.com', model='gpt-6.1-sol'):
    if not path.exists():
        raise ValueError('请先运行 python tools/create_env.py。')
    values = dict(zip(MODEL_KEYS, (base_url.rstrip('/'), model.strip(), token.strip())))
    if any(not value or any(c in value for c in "\r\n'") for value in values.values()):
        raise ValueError('模型配置不能为空，且不能包含换行或单引号。')
    url = urlparse(values['ANTHROPIC_BASE_URL'])
    if not url.hostname or (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1'))):
        raise ValueError('模型服务必须使用 HTTPS 或本机 HTTP 地址。')
    lines = []
    remaining = set(values)
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
        if key in values:
            if key in remaining:
                # 单引号避免 Compose 将 Key 中的 $ 当作变量展开。
                lines.append(key + "='" + values[key] + "'")
                remaining.remove(key)
        else:
            lines.append(line)
    lines.extend(key + "='" + values[key] + "'" for key in MODEL_KEYS if key in remaining)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='https://api.openai-next.com')
    parser.add_argument('--model', default='gpt-6.1-sol')
    args = parser.parse_args()
    try:
        key = os.environ.get('ANTHROPIC_AUTH_TOKEN') or getpass.getpass('API Key（输入不显示）: ')
        configure(ROOT / '.env', key, args.base_url, args.model)
    except (ValueError, EOFError, KeyboardInterrupt) as error:
        parser.exit(1, (str(error) or '已取消配置。') + '\n')
    print('已保存本地模型配置，Key 不回显。运行 docker compose up -d --build 应用配置。')
