"""移除控制器中已经不适用的可选后续动作，不跳过核心库存或账务校验。"""
import ast
import re
import shutil
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / 'apps' / 'erpnext' / 'erpnext'


def remove_functions(file, names, remove_calls=True):
    path = PKG / file
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text)
    edits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            edits.append((start, node.end_lineno))
    lines = text.splitlines()
    for start, end in sorted(edits, reverse=True):
        lines[start-1:end] = []
    text = '\n'.join(lines) + '\n'
    if remove_calls:
        for name in names:
            text = re.sub(r'(?m)^([ \t]*)self\.' + re.escape(name) + r'\(\)[ \t]*$', r'\1pass  # 已移除的可选后续动作。', text)
    # 彻底去除已经没有使用者的跨域拒绝别名。
    tree = ast.parse(text)
    uses = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    lines = text.splitlines()
    unused = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
              and node.module == 'erpnext.focus_boundary'
              and all((alias.asname or alias.name) not in uses for alias in node.names)]
    for node in sorted(unused, key=lambda node: node.lineno, reverse=True):
        lines[node.lineno-1:node.end_lineno] = []
    result = '\n'.join(lines) + '\n'
    ast.parse(result)
    path.write_text(result, encoding='utf-8')


def main():
    remove_functions('accounts/doctype/payment_entry/payment_entry.py',
                     {'update_linked_dunnings', 'trigger_invoice_update_for_subscriptions'})
    remove_functions('selling/doctype/customer/customer.py',
                     {'make_opportunity', 'update_lead_status', 'copy_communication', 'set_loyalty_program', 'get_loyalty_programs'})
    remove_functions('accounts/doctype/account_category/account_category.py', {'after_rename'})
    remove_functions('accounts/doctype/sales_invoice/sales_invoice.py', {'get_loyalty_programs', 'create_invoice_discounting'})
    remove_functions('stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py',
                     {'get_reserved_serial_nos_for_pos', 'get_reserved_batches_for_pos'}, remove_calls=False)
    for file in ('accounts/doctype/payment_entry/payment_entry.py', 'accounts/doctype/journal_entry/journal_entry.py'):
        path = PKG / file
        text = path.read_text(encoding='utf-8')
        text = re.sub(r'get_party_account_based_on_invoice_discounting\([^\n()]+\)\s*or\s*', '', text)
        path.write_text(text, encoding='utf-8')
        remove_functions(file, set())
    path = PKG / 'selling/doctype/customer/customer.js'
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'\t\t\tOpportunity: "Opportunity",\n', '', text)
    text = re.sub(r'\t\t\tOpportunity: \(\) =>\s*frappe.model.open_mapped_doc\(\{.*?\}\),\n', '', text, flags=re.S)
    path.write_text(text, encoding='utf-8')
    remove_functions('accounts/doctype/payment_entry/payment_entry.py', set())
    # 上游公开版本不存在 Item Standard Cost 实现，四模块沿用 FIFO/移动平均。
    path = PKG / 'stock/doctype/bin/bin.py'
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'\n        if get_valuation_method\(self.item_code, self.company\) == "Standard Cost":.*?(?=\n    def )', '', text, flags=re.S)
    path.write_text(text, encoding='utf-8')
    # 销售毛利查询被采购/销售结算复用，保留这一计算依赖。
    upstream = PKG.parents[3] / 'erpnext/erpnext/accounts/report/gross_profit'
    if upstream.exists():
        shutil.copytree(upstream, PKG / 'accounts/report/gross_profit', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('test*', '__pycache__'))
    removed_assets = ['public/js/call_popup', 'public/js/projects', 'public/js/bank_reconciliation_tool',
                      'public/images/bank-logos', 'accounts/test',
                      'public/js/sms_manager.js', 'public/js/telephony.js',
                      'public/js/utils/crm_activities.js', 'public/js/templates/crm_notes.html',
                      'public/js/templates/crm_activities.html', 'public/js/templates/call_link.html',
                      'public/js/bank-reconciliation-tool.bundle.js', 'public/js/financial_statements.js',
                      'public/scss/call_popup.scss', 'public/scss/point-of-sale.scss']
    for relative in removed_assets:
        target = (PKG / relative).resolve()
        if not target.is_relative_to(PKG.resolve()):
            raise ValueError('删除目标超出新副本')
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (PKG / 'public/scss/erpnext.bundle.scss').write_text('@import "./erpnext";\n', encoding='utf-8')
    print('已移除催款扩展、订阅更新、商机创建及财务报表回调。')


if __name__ == '__main__':
    main()
