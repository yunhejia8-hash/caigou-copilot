"""应用已审查的裁剪点；在生成目录运行，禁止指向原始 ERPNext。"""
import ast
import json
import pprint
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'apps' / 'erpnext' / 'erpnext'
UPSTREAM = ROOT.parent / 'erpnext' / 'erpnext'


def replace(path, before, after):
    path = PKG / path
    text = path.read_text(encoding='utf-8')
    if before not in text:
        if after and after in text:
            return
        raise ValueError(f'未匹配到裁剪位置：{path}')
    path.write_text(text.replace(before, after), encoding='utf-8')


def main():
    manifest = json.loads((PKG / 'focus_manifest.json').read_text(encoding='utf-8'))
    removed = set(manifest['removed_doctypes'])
    original = {}
    exec((UPSTREAM / 'hooks.py').read_text(encoding='utf-8'), original)
    preserve = ['app_name', 'app_publisher', 'app_license', 'app_logo_url', 'app_include_js',
                'app_include_css', 'app_include_icons', 'extend_doctype_class', 'treeviews',
                'jinja', 'naming_series_variables', 'auto_cancel_exempted_doctypes',
                'invoice_doctypes', 'advance_payment_receivable_doctypes', 'advance_payment_payable_doctypes',
                'accounting_dimension_doctypes', 'repost_allowed_doctypes', 'regional_overrides',
                'fields_for_group_similar_items', 'data_import_providers', 'export_python_type_annotations']
    hooks = {key: original[key] for key in preserve}
    for key, value in hooks.items():
        if isinstance(value, list):
            hooks[key] = [v for v in value if not isinstance(v, str) or v not in removed]
    hooks.update(app_title='供应协同', app_description='采购、销售、库存、生产',
                 app_email='maintainer@example.invalid', app_home='/desk/buying',
                 after_install='erpnext.focus_boundary.after_install',
                 before_request='erpnext.focus_boundary.before_request',
                 boot_session='erpnext.startup.boot.boot_session',
                 before_tests=None,
                 code_only_modules={'Accounts': ['Buying', 'Selling'], 'Setup': ['Stock'],
                                    'Regional': ['Buying', 'Selling'], 'Subcontracting': ['Manufacturing'], 'Utilities': ['Stock']})
    hooks['add_to_apps_screen'] = [{'name': 'erpnext', 'title': '供应协同', 'logo': hooks['app_logo_url'],
                                  'route': hooks['app_home'], 'has_permission': 'erpnext.check_app_permission'}]
    hooks['doc_events'] = {
        '*': {'before_validate': 'erpnext.focus_boundary.validate_scope'},
        'Stock Entry': original['doc_events']['Stock Entry'],
        'Item': original['doc_events']['Item'],
        tuple(d for d in original['period_closing_doctypes'] if d not in removed): original['doc_events'][tuple(original['period_closing_doctypes'])],
    }
    hooks['global_search_doctypes'] = {'Default': [row for row in original['global_search_doctypes']['Default'] if row['doctype'] not in removed]}
    hooks['scheduler_events'] = {'hourly': [
        'erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.repost_entries',
        'erpnext.manufacturing.doctype.bom_update_log.bom_update_log.resume_bom_cost_update_jobs'],
        'daily': ['erpnext.stock.reorder_item.reorder_item',
                  'erpnext.accounts.doctype.fiscal_year.fiscal_year.auto_create_fiscal_year',
                  'erpnext.controllers.accounts_controller.update_invoice_status',
                  'erpnext.buying.doctype.supplier_quotation.supplier_quotation.set_expired_status',
                  'erpnext.selling.doctype.quotation.quotation.set_expired_status']}
    (PKG / 'hooks.py').write_text('# 四模块发行版：保留上游 GPL-3.0 许可。\n' + '\n\n'.join(
        f'{key} = {pprint.pformat(value, width=110, sort_dicts=False)}' for key, value in hooks.items()) + '\n', encoding='utf-8')
    replace('startup/boot.py', 'frappe.db.get_single_value("CRM Settings", "default_valid_till")', '30')
    replace('setup/doctype/company/company.py', '                sync_financial_report_templates(self.chart_of_accounts, self.existing_company)\n', '')
    replace('stock/doctype/item/item.py', 'return bool(frappe.db.exists("Asset", {"item_code": self.name, "docstatus": 1}))', 'return False  # 固定资产业务已从此发行版移除。')
    replace('accounts/utils.py', '    update_linked_dunnings(ref_doc, previous_outstanding_amount)\n', '')
    # POS 预留不再存在，库存报告只统计四模块真实预留。
    path = PKG / 'stock/report/stock_projected_qty/stock_projected_qty.py'
    text = path.read_text(encoding='utf-8')
    text = text.replace('reserved_qty_for_pos = get_pos_reserved_qty(bin.item_code, bin.warehouse)', 'reserved_qty_for_pos = 0')
    path.write_text(text, encoding='utf-8')
    # 采购/销售界面不再打包营销、电话、POS 和门户脚本。
    path = PKG / 'public/js/erpnext.bundle.js'
    excluded = ('sms_manager', 'call_popup', 'telephony', 'call_link', 'crm_', 'utils/demo', 'financial_statements', 'help_links')
    path.write_text('\n'.join(line for line in path.read_text(encoding='utf-8').splitlines()
                              if not any(name in line for name in excluded)) + '\n', encoding='utf-8')
    # 完整网页/非本产品报表不进入发布源码，目录先验证在新副本之内。
    report_keep = {'accounts_payable', 'accounts_payable_summary', 'accounts_receivable', 'accounts_receivable_summary',
                   'general_ledger', 'payment_ledger', 'item_wise_purchase_register', 'item_wise_sales_register',
                   'purchase_register', 'sales_register', 'received_items_to_be_billed', 'delivered_items_to_be_billed',
                   'billed_items_to_be_received', 'supplier_ledger_summary', 'customer_ledger_summary', 'tax_withholding_details'}
    remove_paths = [PKG / 'selling/page/point_of_sale']
    remove_paths += [p for p in (PKG / 'accounts/report').iterdir() if p.is_dir() and p.name not in report_keep]
    for path in remove_paths:
        path = path.resolve()
        if path.is_relative_to(PKG.resolve()) and path.exists():
            shutil.rmtree(path)
    for path in PKG.glob('*/sidebar/*/*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        data['items'] = [row for row in data['items'] if row.get('link_to') not in removed
                         and row.get('link_to') != 'point-of-sale']
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    # 上游含银行集成的 Python 依赖在本发行版不需要。
    path = PKG.parent / 'pyproject.toml'
    text = path.read_text(encoding='utf-8')
    for name in ('googlemaps', 'plaid-python', 'python-youtube', 'mt-940', 'pdfplumber'):
        text = '\n'.join(line for line in text.splitlines() if f'"{name}' not in line)
    path.write_text(text + '\n', encoding='utf-8')
    print('已应用服务端边界、生产日历抽取、后台钩子和资源裁剪。')


if __name__ == '__main__':
    main()
