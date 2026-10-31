"""验证新部署同时包含框架、业务和工作台资源，不需要登录或读取凭据。"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def verify(base_url):
    base_url = base_url.rstrip('/')
    with urlopen(base_url + '/assets/assets.json', timeout=20) as response:
        manifest = json.load(response)
    required = {'frappe-web.bundle.js', 'desk.bundle.js', 'form.bundle.js',
                'controls.bundle.js', 'login.bundle.css', 'erpnext.bundle.js'}
    missing = required - manifest.keys()
    if missing:
        raise RuntimeError('静态资源清单缺少：' + ', '.join(sorted(missing)))
    assets = sorted(set(manifest.values()) | {'/assets/suke_ops/hub.js', '/assets/suke_ops/hub.css',
                    '/assets/suke_ops/business_search.js', '/assets/suke_ops/business_search.css'})
    for path in assets:
        # 只请求本部署的静态资源，禁止清单引导请求外部地址。
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc or not path.startswith('/assets/'):
            raise RuntimeError('资源清单包含非本站地址')
        with urlopen(base_url + path, timeout=20) as response:
            body = response.read()
            if response.status != 200 or not body or b'<!doctype html' in body[:100].lower():
                raise RuntimeError('资源不可用：' + path)
    return {'status': 'PASS', 'count': len(assets), 'base_url': base_url,
            'required_bundles': sorted(required), 'assets': assets}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8081')
    parser.add_argument('--output', default='verification/assets.json')
    args = parser.parse_args()
    result = verify(args.url)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'count': result['count']}))
