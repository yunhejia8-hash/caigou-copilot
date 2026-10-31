"""验证无凭据实时握手：同源到达登录校验，跨站来源被拒绝。"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def probe(base_url, site, origin=None):
    headers = {'Origin': origin} if origin else {}
    endpoint = base_url.rstrip('/') + '/socket.io/?'

    def request(query, data=None):
        body = data.encode() if data is not None else None
        req = Request(endpoint + urlencode(query), data=body, headers=headers)
        with urlopen(req, timeout=20) as response:
            return response.read().decode()

    query = {'EIO': 4, 'transport': 'polling'}
    handshake = request(query)
    if not handshake.startswith('0'):
        raise RuntimeError('实时服务握手失败')
    query['sid'] = json.loads(handshake[1:])['sid']
    try:
        request(query, '40/' + site + ',')
        return request(query)
    finally:
        request(query, '1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8081')
    parser.add_argument('--site', default='focus.local')
    parser.add_argument('--output', default='verification/realtime.json')
    args = parser.parse_args()
    same = probe(args.url, args.site)
    cross = probe(args.url, args.site, 'https://untrusted.example')
    assert 'Missing cookie and authorization header' in same, '同源请求未到达登录校验'
    assert 'Invalid origin' in cross, '跨站来源未被拒绝'
    result = {'status': 'PASS', 'count': 2, 'checks': [
        '同源无 Origin 轮询到达登录校验，不绕过身份验证',
        '明确的跨站 Origin 原样传递，并被实时服务拒绝']}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'count': result['count']}))
