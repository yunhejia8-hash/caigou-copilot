"""Read local OpenAI configuration as data, never as shell commands."""
import os
from pathlib import Path


def load_local_config(path=None):
    path = Path(path) if path else Path(__file__).resolve().parent / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key not in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_BASE_URL', 'OPENAI_WEB_SEARCH_ENABLED', 'ERP_BASE_URL', 'ERP_API_KEY', 'ERP_API_SECRET'):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)


def responses_url():
    from urllib.parse import urlsplit
    base = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/')
    parts = urlsplit(base)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('OPENAI_BASE_URL 必须是有效的 HTTPS 接口地址')
    return base + '/responses'
