"""发布前检查内部 Python 模块是否实际存在，不以旧安装包兜底。"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'apps' / 'erpnext'


def check():
    missing = {}
    for path in (ROOT / 'erpnext').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.startswith('erpnext.'):
                    destination = ROOT.joinpath(*module.split('.'))
                    if not destination.with_suffix('.py').exists() and not destination.is_dir():
                        missing.setdefault(module, []).append(str(path.relative_to(ROOT)))
    return missing


if __name__ == '__main__':
    print(json.dumps(check(), ensure_ascii=False, indent=2))
