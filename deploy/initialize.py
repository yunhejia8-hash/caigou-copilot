"""初始化本发行版独立站点，不读取或迁移原演示站点。"""
import os
import json
import subprocess
from pathlib import Path


def run(*args, capture=False):
    result = subprocess.run(['bench', *args], check=False, text=True,
                            stdout=subprocess.PIPE if capture else None)
    if result.returncode:
        # 不抛 CalledProcessError：它会把含口令的完整参数打印到日志。
        raise SystemExit(f'初始化步骤失败，退出码 {result.returncode}；查看上方业务错误。')
    return result.stdout


def main():
    site = os.environ.get('SITE_NAME', 'focus.local')
    if '/' in site or '\\' in site or site.startswith('.'):
        raise ValueError('站点名无效')
    Path('sites/apps.txt').write_text('frappe\nerpnext\nsuke_ops\n')
    run('set-config', '-g', 'db_host', 'db')
    run('set-config', '-g', 'redis_cache', 'redis://redis-cache:6379')
    run('set-config', '-g', 'redis_queue', 'redis://redis-queue:6379')
    run('set-config', '-g', 'redis_socketio', 'redis://redis-queue:6379')
    run('set-config', '-gp', 'socketio_port', '9000')
    run('set-config', '-g', 'webserver_host', 'http://backend')
    run('set-config', '-gp', 'webserver_port', '8000')
    if not Path('sites', site, 'site_config.json').exists():
        run('new-site', site, '--mariadb-user-host-login-scope=%',
            '--db-root-password', os.environ['DB_PASSWORD'],
            '--admin-password', os.environ['ADMIN_PASSWORD'], '--install-app', 'erpnext')
    installed = json.loads(run('--site', site, 'execute', 'frappe.get_installed_apps', capture=True))
    if 'erpnext' not in installed:
        # 只恢复尚未完成安装的应用；已安装应用不能用 force 重置权限。
        run('--site', site, 'install-app', 'erpnext', '--force')
    if 'suke_ops' not in installed:
        run('--site', site, 'install-app', 'suke_ops')
    run('--site', site, 'execute', 'erpnext.focus_boundary.initialize_demo')
    run('--site', site, 'execute', 'suke_ops.setup.install')
    run('--site', site, 'execute', 'suke_ops.seed.create_case')
    run('--site', site, 'execute', 'erpnext.focus_boundary.complete_demo_setup')
    run('--site', site, 'enable-scheduler')
    run('--site', site, 'clear-cache')


if __name__ == '__main__':
    main()
