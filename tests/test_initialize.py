"""失败日志不能包含口令；已安装应用不重复强制安装。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'deploy/initialize.py'
spec = importlib.util.spec_from_file_location('initialize', path)
initialize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initialize)


class InitializeTests(unittest.TestCase):
    def test_failure_does_not_expose_credentials(self):
        with patch.object(initialize.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(SystemExit) as result:
                initialize.run('new-site', 'focus.local', '--admin-password', 'synthetic-secret')
            self.assertNotIn('synthetic-secret', str(result.exception))
            self.assertNotIn('--admin-password', str(result.exception))

    def test_installed_app_is_not_force_reinstalled(self):
        with patch.object(initialize, 'run', return_value='["frappe", "erpnext", "suke_ops"]') as run:
            with patch.object(initialize.Path, 'exists', return_value=True), patch.object(initialize.Path, 'write_text'):
                initialize.main()
            self.assertFalse(any('install-app' in call.args for call in run.call_args_list))

    def test_partial_erp_install_can_resume(self):
        with patch.object(initialize, 'run', return_value='["frappe"]') as run:
            with patch.object(initialize.Path, 'exists', return_value=True), patch.object(initialize.Path, 'write_text'):
                initialize.main()
            self.assertTrue(any(call.args[-3:] == ('install-app', 'erpnext', '--force') for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
