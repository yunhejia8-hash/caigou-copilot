# 四模块发行版：保留上游 GPL-3.0 许可。
app_name = 'erpnext'

app_publisher = 'Frappe Technologies Pvt. Ltd.'

app_license = 'GNU General Public License (v3)'

app_logo_url = '/assets/erpnext/images/erpnext-logo.svg'

app_include_js = 'erpnext.bundle.js'

app_include_css = 'erpnext.bundle.css'

app_include_icons = ['/assets/erpnext/icons/pos-icons.svg', '/assets/erpnext/icons/module-icons.svg']

extend_doctype_class = {'Address': 'erpnext.accounts.custom.address.ERPNextAddress'}

treeviews = ['Account',
 'Cost Center',
 'Warehouse',
 'Item Group',
 'Customer Group',
 'Supplier Group',
 'Sales Person',
 'Territory',
 'Department']

jinja = {'methods': ['erpnext.stock.serial_batch_bundle.get_serial_or_batch_nos']}

naming_series_variables = {'FY': 'erpnext.accounts.utils.parse_naming_series_variable',
 'TFY': 'erpnext.accounts.utils.parse_naming_series_variable',
 'ABBR': 'erpnext.accounts.utils.parse_naming_series_variable',
 'MM': 'erpnext.accounts.utils.parse_naming_series_variable',
 'DD': 'erpnext.accounts.utils.parse_naming_series_variable',
 'YY': 'erpnext.accounts.utils.parse_naming_series_variable',
 'YYYY': 'erpnext.accounts.utils.parse_naming_series_variable',
 'JJJ': 'erpnext.accounts.utils.parse_naming_series_variable',
 'WW': 'erpnext.accounts.utils.parse_naming_series_variable'}

auto_cancel_exempted_doctypes = ['Payment Entry']

invoice_doctypes = ['Sales Invoice', 'Purchase Invoice']

advance_payment_receivable_doctypes = ['Sales Order']

advance_payment_payable_doctypes = ['Purchase Order']

accounting_dimension_doctypes = ['GL Entry',
 'Payment Ledger Entry',
 'Sales Invoice',
 'Purchase Invoice',
 'Payment Entry',
 'Stock Entry',
 'Budget',
 'Delivery Note',
 'Sales Invoice Item',
 'Purchase Invoice Item',
 'Purchase Order Item',
 'Sales Order Item',
 'Journal Entry Account',
 'Journal Entry Template Account',
 'Material Request Item',
 'Delivery Note Item',
 'Purchase Receipt Item',
 'Stock Entry Detail',
 'Payment Entry Deduction',
 'Sales Taxes and Charges',
 'Purchase Taxes and Charges',
 'Shipping Rule',
 'Landed Cost Item',
 'Landed Cost Taxes and Charges',
 'Stock Reconciliation',
 'Opening Invoice Creation Tool',
 'Opening Invoice Creation Tool Item',
 'Purchase Order',
 'Purchase Receipt',
 'Sales Order',
 'Subcontracting Order',
 'Subcontracting Order Item',
 'Subcontracting Receipt',
 'Subcontracting Receipt Item',
 'Account Closing Balance',
 'Supplier Quotation',
 'Supplier Quotation Item',
 'Payment Reconciliation',
 'Payment Reconciliation Allocation',
 'Payment Request',
 'Advance Taxes and Charges']

repost_allowed_doctypes = ['Sales Invoice', 'Purchase Invoice', 'Journal Entry', 'Payment Entry', 'Purchase Receipt']

regional_overrides = {'United Arab Emirates': {'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.united_arab_emirates.utils.update_itemised_tax_data',
                          'erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_regional_gl_entries': 'erpnext.regional.united_arab_emirates.utils.make_regional_gl_entries'},
 'Saudi Arabia': {'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.united_arab_emirates.utils.update_itemised_tax_data'},
 'Italy': {'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.italy.utils.update_itemised_tax_data',
           'erpnext.controllers.accounts_controller.validate_regional': 'erpnext.regional.italy.utils.sales_invoice_validate'}}

fields_for_group_similar_items = ['qty', 'amount']

data_import_providers = {'Customer': 'erpnext.utilities.party_import_provider.PartyImportProvider',
 'Supplier': 'erpnext.utilities.party_import_provider.PartyImportProvider'}

export_python_type_annotations = True

app_title = '供应协同'

app_description = '采购、销售、库存、生产'

app_email = 'maintainer@example.invalid'

app_home = '/desk/buying/operations-hub'

after_install = 'erpnext.focus_boundary.after_install'

before_request = 'erpnext.focus_boundary.before_request'

boot_session = 'erpnext.startup.boot.boot_session'

before_tests = None

code_only_modules = {'Accounts': ['Buying', 'Selling'],
 'Setup': ['Stock'],
 'Regional': ['Buying', 'Selling'],
 'Subcontracting': ['Manufacturing'],
 'Utilities': ['Stock']}

add_to_apps_screen = [{'name': 'erpnext',
  'title': '供应协同',
  'logo': '/assets/erpnext/images/erpnext-logo.svg',
  'route': '/desk/buying/operations-hub',
  'has_permission': 'erpnext.check_app_permission'}]

doc_events = {'*': {'before_validate': 'erpnext.focus_boundary.validate_scope'},
 'Stock Entry': {'on_submit': 'erpnext.stock.doctype.material_request.material_request.update_completed_and_requested_qty',
                 'on_cancel': 'erpnext.stock.doctype.material_request.material_request.update_completed_and_requested_qty'},
 'Item': {'on_update': 'erpnext.stock.doctype.item.item_search.reindex_item',
          'after_rename': 'erpnext.stock.doctype.item.item_search.reindex_renamed_item'},
 ('Sales Invoice', 'Purchase Invoice', 'Journal Entry', 'Bank Clearance', 'Stock Entry', 'Payment Entry', 'Period Closing Voucher', 'Process Deferred Accounting', 'Delivery Note', 'Landed Cost Voucher', 'Purchase Receipt', 'Stock Reconciliation', 'Subcontracting Receipt'): {'validate': 'erpnext.accounts.doctype.accounting_period.accounting_period.validate_accounting_period_on_doc_save'}}

global_search_doctypes = {'Default': [{'doctype': 'Customer', 'index': 0},
             {'doctype': 'Supplier', 'index': 1},
             {'doctype': 'Item', 'index': 2},
             {'doctype': 'Sales Invoice', 'index': 3},
             {'doctype': 'Purchase Invoice', 'index': 4},
             {'doctype': 'Sales Order', 'index': 5},
             {'doctype': 'Purchase Order', 'index': 6},
             {'doctype': 'Quotation', 'index': 7},
             {'doctype': 'Delivery Note', 'index': 8},
             {'doctype': 'Purchase Receipt', 'index': 9},
             {'doctype': 'Payment Entry', 'index': 10},
             {'doctype': 'Journal Entry', 'index': 11},
             {'doctype': 'Supplier Quotation', 'index': 14},
             {'doctype': 'Material Request', 'index': 15},
             {'doctype': 'Stock Entry', 'index': 16},
             {'doctype': 'Work Order', 'index': 17},
             {'doctype': 'BOM', 'index': 18},
             {'doctype': 'Serial No', 'index': 23},
             {'doctype': 'Batch', 'index': 24},
             {'doctype': 'Employee', 'index': 25}]}

scheduler_events = {'hourly': ['erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.repost_entries',
            'erpnext.manufacturing.doctype.bom_update_log.bom_update_log.resume_bom_cost_update_jobs'],
 'daily': ['erpnext.stock.reorder_item.reorder_item',
           'erpnext.accounts.doctype.fiscal_year.fiscal_year.auto_create_fiscal_year',
           'erpnext.controllers.accounts_controller.update_invoice_status',
           'erpnext.buying.doctype.supplier_quotation.supplier_quotation.set_expired_status',
           'erpnext.selling.doctype.quotation.quotation.set_expired_status']}
