"""仅对finance-*合成站点复现验收，不读取现有部署凭据。"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def verify(container, site, config):
    if not site.startswith('finance-') or not container.startswith('caigou-finance-'):
        raise ValueError('仅允许命名隔离的finance测试容器和站点')
    suites = {}
    for label, directory in (('app_python', 'apps/suke_ops/tests'), ('root_python', 'tests')):
        code = ("import sys,unittest;sys.path.insert(0,'apps/suke_ops');"
                f"s=unittest.defaultTestLoader.discover('{directory}');"
                "r=unittest.TextTestRunner().run(s);print(r.testsRun);sys.exit(not r.wasSuccessful())")
        output = execute([sys.executable, '-B', '-c', code])
        suites[label] = {'status': 'PASS', 'tests': int(output.splitlines()[0])}
    scripts = sorted((ROOT / 'tests').glob('test_*.cjs'))
    output = execute(['node', '--test', *[str(p.relative_to(ROOT)) for p in scripts]])
    suites['frontend'] = {'status': 'PASS', 'output': output.strip()}
    docker = ['docker', '--config', config]
    integration = json.loads(execute([*docker, 'exec', container, 'bench', '--site', site,
                                      'execute', 'suke_ops.finance_sync.integration.run']))
    assert integration['status'] == 'PASS'
    assets = {}
    from urllib.request import urlopen
    for asset in ('/login', '/assets/suke_ops/finance_connections.js'):
        with urlopen('http://127.0.0.1:8093' + asset, timeout=20) as response:
            assets[asset] = {'status': response.status, 'bytes': len(response.read())}
    return {'status': 'PASS', 'at_utc': datetime.now(timezone.utc).isoformat(),
            'scope': '工作区金蝶第一版，未提交；已有其他改动保留', 'suites': suites,
            'integration': integration, 'assets': assets, 'real_kingdee_contacted': False, 'model_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='caigou-finance-v1-app')
    parser.add_argument('--site', default='finance-v1.local')
    parser.add_argument('--docker-config', required=True)
    parser.add_argument('--output', default='verification/finance-sync-20261101.json')
    args = parser.parse_args()
    report = verify(args.container, args.site, args.docker_config)
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'suites': {k:v.get('tests','PASS') for k,v in report['suites'].items()},
                      'integration_checks': report['integration']['count']}, ensure_ascii=False))
