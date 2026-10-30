"""四模块版本的服务端边界；拒绝被裁剪的可选业务，不伪装成功。"""
import json
from pathlib import Path

import frappe

MANIFEST = json.loads(Path(__file__).with_name('focus_manifest.json').read_text(encoding='utf-8'))
REMOVED = set(MANIFEST['removed_doctypes'])


def unsupported(*args, **kwargs):
    frappe.throw('此业务能力未包含在采购、销售、库存、生产版本中。', frappe.ValidationError)


def reject_projects(projects):
    if projects:
        unsupported()


def check_voucher_projects(vouchers):
    # 估值重算仍需执行；只核验凭证未携带被裁剪的项目成本归集。
    for doctype, name in vouchers:
        doc = frappe.get_doc(doctype, name)
        if doc.get('project') or any(row.get('project') for row in doc.get('items', [])):
            unsupported()


def get_holidays(holiday_list_name):
    # 生产排程需要日历，原工具位于客服工单模块，抽取到共享依赖。
    return [row.holiday_date for row in frappe.get_cached_doc('Holiday List', holiday_list_name).holidays]


def validate_scope(doc, method=None):
    if doc.doctype in REMOVED:
        unsupported()
    for row in [doc, *doc.get_all_children()]:
        for field in MANIFEST['boundary_fields'].get(row.doctype, []):
            if row.get(field):
                frappe.throw(f'{row.doctype}.{field} 属于已移除的可选业务，不允许写入。')


def before_request():
    method = frappe.form_dict.get('cmd') or ''
    if method.startswith('erpnext.') and method.split('.')[1] in MANIFEST['removed_modules']:
        unsupported()


def after_install():
    from frappe.desk.page.setup_wizard.setup_wizard import add_all_roles_to
    from erpnext.setup import install
    from erpnext.setup.doctype.incoterm.incoterm import create_incoterms

    install.set_single_defaults()
    install.setup_repost_defaults()
    install.create_print_setting_custom_fields()
    install.create_address_and_contact_custom_fields()
    add_all_roles_to('Administrator')
    create_incoterms()
    install.make_default_operations()
    install.update_pegged_currencies()
    install.grant_address_read_to_accounts_manager()


def initialize_demo(company_name='供应协同演示', abbr='SC'):
    """仅在新安装环境创建合成主数据，不读取旧站点数据。"""
    from datetime import date
    from erpnext.setup.setup_wizard.operations import install_fixtures

    if frappe.defaults.get_global_default('focus_demo_initialized'):
        return {'already_initialized': True}
    frappe.set_user('Administrator')
    records = [record for record in install_fixtures.get_preset_records('China')
               if record['doctype'] not in REMOVED
               and not (record['doctype'] == 'Party Type' and record.get('party_type') in REMOVED)]
    for values in records:
        condition = values.get('__condition')
        if condition and not condition():
            continue
        doc = frappe.get_doc({key: value for key, value in values.items() if not key.startswith('__')})
        naming = doc.meta.autoname or ''
        candidate = doc.get(naming[6:]) if naming.startswith('field:') else doc.get('name')
        if candidate and frappe.db.exists(doc.doctype, candidate):
            continue
        if doc.get('is_group') and not doc.get('parent_' + frappe.scrub(doc.doctype)):
            doc.flags.ignore_mandatory = True
        doc.insert()
    install_fixtures.add_uom_data()
    install_fixtures.update_item_variant_settings()
    install_fixtures.update_selling_defaults()
    install_fixtures.update_buying_defaults()
    year = date.today().year
    if not frappe.db.exists('Fiscal Year', str(year)):
        frappe.get_doc({'doctype': 'Fiscal Year', 'year': str(year),
                        'year_start_date': f'{year}-01-01', 'year_end_date': f'{year}-12-31'}).insert()
    if not frappe.db.exists('Company', company_name):
        frappe.get_doc({'doctype': 'Company', 'company_name': company_name, 'abbr': abbr,
                        'default_currency': 'CNY', 'country': 'China',
                        'create_chart_of_accounts_based_on': 'Standard Template',
                        'chart_of_accounts': 'Standard', 'enable_perpetual_inventory': 1}).insert()
    company = frappe.get_doc('Company', company_name)
    warehouse = frappe.db.get_value('Warehouse', {'company': company.name, 'is_group': 0, 'warehouse_name': 'Stores'}, 'name')
    if not warehouse:
        warehouse = frappe.db.get_value('Warehouse', {'company': company.name, 'is_group': 0, 'warehouse_type': ['!=', 'Transit']}, 'name')
    company.default_warehouse = warehouse
    company.save()
    # 原生保存会同步 DefaultValue；直接更新 Singles 会残留上游 INR 默认值。
    defaults = frappe.get_single('Global Defaults')
    defaults.update({'default_company': company.name, 'default_currency': 'CNY', 'country': 'China'})
    defaults.save()
    for label, buying, selling in [('Standard Buying', 1, 0), ('Standard Selling', 0, 1)]:
        if not frappe.db.exists('Price List', label):
            frappe.get_doc({'doctype': 'Price List', 'price_list_name': label, 'enabled': 1,
                            'buying': buying, 'selling': selling, 'currency': 'CNY'}).insert()
    frappe.db.set_single_value('System Settings', {'setup_complete': 1, 'time_zone': 'Asia/Singapore', 'language': 'zh'})
    frappe.defaults.set_global_default('focus_demo_initialized', '1')
    frappe.db.commit()
    return {'company': company.name, 'warehouse': warehouse}
