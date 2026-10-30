"""源码边界验收，不使用完整 ERPNext 安装包来满足缺失依赖。"""
import ast
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'apps/erpnext/erpnext'
MANIFEST = json.loads((PKG / 'focus_manifest.json').read_text(encoding='utf-8'))


class SourceBoundaryTests(unittest.TestCase):
    def test_removed_domains_have_no_source(self):
        for module in MANIFEST['removed_modules']:
            self.assertFalse((PKG / module).exists(), module)

    def test_removed_doctypes_have_no_schema(self):
        found = set()
        for path in PKG.glob('*/doctype/*/*.json'):
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('doctype') == 'DocType':
                found.add(data['name'])
                for field in data.get('fields', []):
                    if field.get('fieldtype') in {'Link', 'Table', 'Table MultiSelect'}:
                        self.assertNotIn(field.get('options'), MANIFEST['removed_doctypes'], str(path))
        self.assertEqual(set(MANIFEST['kept_doctypes']), found)

    def test_internal_imports_resolve_from_release(self):
        spec = importlib.util.spec_from_file_location('check_imports', ROOT / 'tools/check_imports.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.check(), {})

    def test_account_asset_category_is_preserved(self):
        data = json.loads((PKG / 'accounts/doctype/account/account.json').read_text(encoding='utf-8'))
        field = next(field for field in data['fields'] if field['fieldname'] == 'root_type')
        self.assertIn('Asset', field['options'].split('\n'))

    def test_hook_targets_have_modules(self):
        namespace = {}
        exec((PKG / 'hooks.py').read_text(encoding='utf-8'), namespace)
        def check(value):
            if isinstance(value, dict):
                for child in value.values():
                    check(child)
            elif isinstance(value, (tuple, list)):
                for child in value:
                    check(child)
            elif isinstance(value, str) and value.startswith('erpnext.') and not value.endswith(('.js', '.css', '.svg')):
                path = ROOT / 'apps/erpnext' / '/'.join(value.split('.')[:-1])
                self.assertTrue(path.with_suffix('.py').exists() or (path / '__init__.py').exists(), value)
        for key, value in namespace.items():
            if not key.startswith('__'):
                check(value)


if __name__ == '__main__':
    unittest.main()
