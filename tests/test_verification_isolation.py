"""有写操作的验收入口不得默认触达现有业务站点。"""
import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,patch


class VerificationIsolationTests(unittest.TestCase):
    def test_procurement_verifier_rejects_existing_service_before_any_command(self):
        path=Path(__file__).resolve().parents[1]/'tools/verify_procurement.py'
        spec=importlib.util.spec_from_file_location('procurement_verifier_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for container,site in [('caigou-copilot-backend-1','focus.local'),('caigou-procurement-test','focus.local')]:
            with self.subTest(site=site),patch.object(module,'run',side_effect=AssertionError('不得执行命令')):
                with self.assertRaises(ValueError):module.verify(container,site,'http://127.0.0.1:8095')

    def test_integration_rejects_existing_site_before_user_or_database_changes(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/suke_ops'))
        fake=MagicMock();fake.local.site='focus.local'
        with patch.dict(sys.modules,{'frappe':fake,'frappe.utils':NS(nowdate=lambda:'2026-11-01',getdate=lambda x:x),
                                    'suke_ops.procurement_api':MagicMock(),'suke_ops.approval':MagicMock(),'suke_ops.hub':MagicMock()}):
            module=importlib.import_module('suke_ops.procurement_integration')
        with patch.object(module,'frappe',fake),self.assertRaises(RuntimeError):module.run()
        fake.set_user.assert_not_called();fake.db.count.assert_not_called()
