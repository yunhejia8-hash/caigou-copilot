"""从固定版本生成四模块源码；只写入新的目标目录，不修改上游目录。"""
import ast
import json
import re
import shutil
import sys
from pathlib import Path

BUSINESS = {'buying', 'selling', 'stock', 'manufacturing'}
SUPPORT = {'accounts', 'setup', 'regional', 'subcontracting'}
REMOVED = {'crm', 'projects', 'support', 'assets', 'maintenance', 'quality_management',
           'telephony', 'communication', 'edi', 'erpnext_integrations', 'portal', 'bulk_transaction'}
REMOVED_DT_PREFIXES = ('pos_', 'subscription', 'process_subscription', 'loyalty_', 'share_', 'shareholder',
                       'bank_statement_', 'bank_transaction_rule', 'bank_reconciliation_tool',
                       'bisect_', 'financial_report_', 'invoice_discounting', 'discounted_invoice',
                       'dunning', 'psoa_', 'process_statement_of_accounts', 'sms_center', 'installation_note')
EXTRA_DIRS = {'controllers', 'startup', 'public', 'templates', 'translations', 'config', 'utilities'}
EXTRA_FILES = {'__init__.py', 'exceptions.py', 'hooks.py', 'modules.txt', 'patches.txt', 'commands.py'}


def blocked_module(name):
    parts = (name or '').split('.')
    if len(parts) < 2 or parts[0] != 'erpnext':
        return False
    return parts[1] in REMOVED or ('doctype' in parts and any(p.startswith(REMOVED_DT_PREFIXES) for p in parts[2:]))


def rewrite_python(text, relative, imports):
    # 删除已移除域的导入。旧控制器的可选调用由明确拒绝的边界函数承接。
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    edits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and blocked_module(node.module):
            replacements = []
            for alias in node.names:
                name = alias.asname or alias.name
                implementation = {'get_holidays': 'get_holidays',
                                  'update_purchase_cost': 'reject_projects',
                                  'update_consumed_material_cost': 'reject_projects',
                                  'update_consumed_material_cost_for_vouchers': 'check_voucher_projects'}.get(alias.name, 'unsupported')
                replacements.append(f'from erpnext.focus_boundary import {implementation} as {name}')
            imports.append({'file': relative, 'removed_import': node.module, 'names': [a.name for a in node.names]})
            edits.append((node.lineno, node.end_lineno, '\n'.join(' ' * node.col_offset + r for r in replacements)))
    lines = text.expandtabs(4).splitlines()
    # AST 列偏移使用原始 tab；用原行缩进保持所在块。
    for start, end, replacement in reversed(sorted(edits)):
        indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
        replacement = '\n'.join(' ' * indent + line.lstrip() for line in replacement.splitlines())
        lines[start - 1:end] = replacement.splitlines()
    return '\n'.join(lines) + '\n'


def build(source, target):
    source, target = source.resolve(), target.resolve()
    if target.exists():
        raise SystemExit('目标已存在；为保护修改，请指定新的目标目录')
    source_pkg = source / 'erpnext'
    docs = {}
    for path in source_pkg.glob('*/doctype/*/*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('doctype') == 'DocType':
            docs[data['name']] = (path, data)
    kept = {name for name, (path, data) in docs.items()
            if path.relative_to(source_pkg).parts[0] in BUSINESS | SUPPORT
            and not path.parent.name.startswith(REMOVED_DT_PREFIXES)}
    # 单位换算、货币兑换、交易删除锁是订单/库存的支撑记录。
    kept |= {name for name, (path, data) in docs.items() if path.relative_to(source_pkg).parts[0] == 'utilities'
             and path.parent.name in {'transaction_deletion_record', 'transaction_deletion_record_item'}}
    # 原单据子表保留结构兼容；子表不提供独立业务入口。
    changed = True
    while changed:
        changed = False
        for name in list(kept):
            for field in docs[name][1].get('fields', []):
                dep = field.get('options')
                if field.get('fieldtype') in {'Table', 'Table MultiSelect'} and dep in docs and dep not in kept:
                    if docs[dep][1].get('istable'):
                        kept.add(dep)
                        changed = True
    removed_docs = set(docs) - kept
    manifest = {'upstream': '9cd11b19575bfc34f092a24f6c8b368c4dd69481',
                'business_modules': sorted(BUSINESS), 'removed_modules': sorted(REMOVED),
                'kept_doctypes': sorted(kept), 'removed_doctypes': sorted(removed_docs),
                'boundary_fields': {}, 'removed_imports': [], 'moved_child_tables': {}}
    for path in source_pkg.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(source_pkg)
        parts = rel.parts
        if any(p in {'.git', '__pycache__', 'node_modules', 'tests', 'test_ui', 'demo', 'patches'} for p in parts):
            continue
        if path.name.startswith(('test_', 'test_records', '_test_')) or path.suffix == '.pyc':
            continue
        if parts[0] not in BUSINESS | SUPPORT | EXTRA_DIRS and str(rel) not in EXTRA_FILES:
            continue
        if 'doctype' in parts:
            idx = parts.index('doctype')
            if len(parts) > idx + 1:
                dt_dir = parts[idx + 1]
                dt_names = [n for n, (p, _) in docs.items() if p.parent.name == dt_dir]
                if dt_names and dt_names[0] not in kept:
                    continue
        if any(p in {'workspace', 'workspace_sidebar', 'sidebar', 'desktop_icon', 'page', 'dashboard_chart', 'number_card', 'dashboard'} for p in parts):
            if parts[0] not in BUSINESS:
                continue
        if parts[0] == 'utilities' and len(parts) > 1 and parts[1] == 'doctype':
            dt_dir = parts[2]
            if not any(p.parent.name == dt_dir and name in kept for name, (p, _) in docs.items()):
                continue
        if path.name == 'patches.txt':
            content = '[pre_model_sync]\n[post_model_sync]\n'
        elif path.suffix == '.py':
            content = rewrite_python(path.read_text(encoding='utf-8'), str(rel), manifest['removed_imports'])
        else:
            content = None
        destination = target / 'erpnext' / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            shutil.copy2(path, destination)
        else:
            destination.write_text(content, encoding='utf-8')
    # 从排除域带入的只有仍被四模块表格引用的子表，不带入父业务实现。
    for name in kept:
        path, data = docs[name]
        original_rel = path.relative_to(source_pkg)
        if original_rel.parts[0] in REMOVED:
            child_dir = target / 'erpnext' / 'setup' / 'doctype' / path.parent.name
            child_dir.mkdir(parents=True, exist_ok=True)
            for child in path.parent.glob('*'):
                if child.is_file() and child.suffix in {'.py', '.json', '.js'} and not child.name.startswith('test'):
                    shutil.copy2(child, child_dir / child.name)
            data['module'] = 'Setup'
            destination = child_dir / path.name
            manifest['moved_child_tables'][name] = '原单据兼容子表；不包含原模块父单据或业务接口'
        else:
            destination = target / 'erpnext' / original_rel
        boundary = []
        for field in data.get('fields', []):
            if field.get('fieldtype') == 'Link' and field.get('options') in removed_docs:
                boundary.append(field['fieldname'])
                field.update(fieldtype='Data', read_only=1, hidden=1, reqd=0)
                for key in ('options', 'fetch_from', 'default'):
                    field.pop(key, None)
            elif (field.get('fieldtype') == 'Select' and field.get('options')
                  and field['fieldname'] in {f.get('options') for f in data.get('fields', []) if f.get('fieldtype') == 'Dynamic Link'}):
                field['options'] = '\n'.join(v for v in field['options'].split('\n') if v not in removed_docs)
            if field.get('fieldname') in {'is_fixed_asset', 'is_pos', 'enable_deferred_revenue', 'enable_deferred_expense'}:
                boundary.append(field['fieldname'])
                field.update(read_only=1, hidden=1, default='0')
        if boundary:
            manifest['boundary_fields'][name] = sorted(set(boundary))
        data['links'] = [link for link in data.get('links', []) if link.get('link_doctype') not in removed_docs]
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    for filename in ('license.txt', 'pyproject.toml', 'package.json', 'yarn.lock'):
        if (source / filename).exists():
            shutil.copy2(source / filename, target / filename)
    (target / 'README.md').write_text('# ERPNext 四模块源码裁剪版\n\nGPL-3.0，保留上游版权。详见仓库根目录交付说明与依赖清单。\n', encoding='utf-8')
    (target / 'erpnext' / 'modules.txt').write_text('\n'.join(['Buying', 'Selling', 'Stock', 'Manufacturing', 'Accounts', 'Setup', 'Regional', 'Subcontracting', 'Utilities']) + '\n', encoding='utf-8')
    (target / 'erpnext' / 'focus_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'kept_doctypes': len(kept), 'removed_doctypes': len(removed_docs),
                      'files': sum(1 for p in target.rglob('*') if p.is_file()),
                      'boundary_imports': len(manifest['removed_imports'])}, ensure_ascii=False))


if __name__ == '__main__':
    build(Path(sys.argv[1]), Path(sys.argv[2]))
