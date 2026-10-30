"""审计四模块与 ERPNext 单据、Python 导入之间的依赖，保留可追溯原因。"""
import ast
import json
from collections import Counter
from pathlib import Path
import sys


def audit(root):
    docs = {}
    for path in root.glob('erpnext/*/doctype/*/*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('doctype') == 'DocType':
            docs[data['name']] = (path, data)
    roots = {name for name, (_, data) in docs.items()
             if data.get('module') in {'Buying', 'Selling', 'Stock', 'Manufacturing'}}
    selected = {name: '四模块业务' for name in roots}
    queue = list(roots)
    while queue:
        name = queue.pop()
        _, data = docs[name]
        for field in data.get('fields', []):
            dep = field.get('options')
            if field.get('fieldtype') in {'Link', 'Table', 'Table MultiSelect'} and dep in docs and dep not in selected:
                selected[dep] = f"{name}.{field['fieldname']}"
                queue.append(dep)
    return {
        'all_doctypes': len(docs), 'business_doctypes': len(roots),
        'linked_doctypes': len(selected),
        'modules': dict(Counter(docs[name][1]['module'] for name in selected)),
        'outside_four_modules': {name: reason for name, reason in sorted(selected.items()) if name not in roots},
    }


if __name__ == '__main__':
    result = audit(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'outside_four_modules'}, ensure_ascii=False, indent=2))
