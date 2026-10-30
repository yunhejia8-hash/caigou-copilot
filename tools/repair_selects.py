"""保留会计 Asset 等同名单词；只裁剪 Dynamic Link 的单据类型选择器。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'apps/erpnext/erpnext'
original = {}
for path in (ROOT.parent / 'erpnext/erpnext').glob('*/doctype/*/*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict) and data.get('doctype') == 'DocType':
        original[data['name']] = data
changed = []
for path in PKG.glob('*/doctype/*/*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('doctype') != 'DocType':
        continue
    selectors = {field.get('options') for field in data['fields'] if field.get('fieldtype') == 'Dynamic Link'}
    fields = {field['fieldname']: field for field in original[data['name']]['fields']}
    modified = False
    for field in data['fields']:
        source = fields[field['fieldname']]
        if field.get('fieldtype') == 'Select' and field['fieldname'] not in selectors and field.get('options') != source.get('options'):
            field['options'] = source.get('options', '')
            modified = True
    if modified:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        changed.append([data['module'].lower().replace(' ', '_'), path.parent.name])
(ROOT / '.local-schema-adjustments.json').write_text(json.dumps(changed), encoding='utf-8')
print(changed)
