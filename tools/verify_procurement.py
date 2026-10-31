"""复现采购闭环验收，报告不含业务正文、配置或密钥。"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ['apps/suke_ops/suke_ops/' + name for name in
                ('procurement.py', 'procurement_api.py', 'procurement_setup.py', 'procurement_integration.py',
                 'hub.py', 'hooks.py', 'setup.py', 'public/hub.js', 'public/procurement.js', 'public/procurement.css')]
SOURCE_FILES += ['apps/erpnext/erpnext/buying/page/procurement_workspace/' + name
                 for name in ('procurement_workspace.json', 'procurement_workspace.js')]
SOURCE_FILES += ['apps/suke_ops/suke_ops/catalog.py', 'apps/suke_ops/suke_ops/agent_runtime.py']
SOURCE_FILES += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'apps/suke_ops/suke_ops/finance_sync').glob('*.py'))]


def source_hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def run(command, cwd=ROOT):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError('检查失败：' + ' '.join(command) + '\n' + result.stdout + result.stderr)
    return result.stdout


def deployment_hashes(container):
    code = ("from pathlib import Path;import hashlib,json;root=Path('/home/frappe/frappe-bench');"
            f"files={SOURCE_FILES!r};"
            "print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files}))")
    return json.loads(run(['docker', 'exec', container, '/home/frappe/frappe-bench/env/bin/python', '-c', code]))


def verify(container, site, url):
    if not container.startswith('caigou-procurement-') or not site.startswith('procurement-'):
        raise ValueError('仅允许caigou-procurement-*容器及procurement-*独立合成验收站点')
    hashes = source_hashes()
    if hashes != deployment_hashes(container):
        raise RuntimeError('部署源码与本地源码不一致，请先重建增量镜像')
    core = run([sys.executable, '-B', '-c',
                "import sys,unittest;sys.path.insert(0,'apps/suke_ops');s=unittest.defaultTestLoader.discover('apps/suke_ops/tests');r=unittest.TextTestRunner().run(s);print(r.testsRun);sys.exit(not r.wasSuccessful())"])
    root = run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'])
    frontend = run(['node', '--test', *[str(p.relative_to(ROOT)) for p in sorted((ROOT / 'tests').glob('test_*.cjs'))]])
    integration = json.loads(run(['docker', 'exec', container, 'bench', '--site', site,
                                 'execute', 'suke_ops.procurement_integration.run']))
    from verify_assets import verify as verify_assets
    assets = verify_assets(url)
    return {'status': 'PASS', 'at_utc': datetime.now(timezone.utc).isoformat(),
            'source_commit': run(['git', 'rev-parse', 'HEAD']).strip(),
            'source_files_sha256': hashes, 'deployment_source_matches': True,
            'core_python_tests': int(core.strip().splitlines()[-1]),
            'root_python_tests': 'PASS', 'frontend': frontend.strip(),
            'database_integration': integration, 'assets': assets,
            'model_calls': 0, 'external_payments': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--site', required=True)
    parser.add_argument('--url', required=True, help='独立验收前端地址')
    parser.add_argument('--output', default='verification/procurement-20261101.json')
    args = parser.parse_args()
    result = verify(args.container, args.site, args.url)
    path = ROOT / args.output
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'core_tests': result['core_python_tests'],
                      'integration_checks': len(result['database_integration']['checks']),
                      'assets': result['assets']['count']}, ensure_ascii=False))
