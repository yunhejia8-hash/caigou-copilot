app_name = 'suke_ops'
app_title = 'Supply Operations'
app_publisher = 'SUKE'
app_description = '帮助采购跟进交期、识别缺料风险并辅助决策'
app_email = 'demo@example.invalid'
app_license = 'MIT'
app_include_js = ['/assets/suke_ops/ops.js', '/assets/suke_ops/business_search.js?v=click-only-20261031-2',
                  '/assets/suke_ops/bear-pet.js', '/assets/suke_ops/bear-control.js']
app_include_css = ['/assets/suke_ops/business_search.css']
awesomebar_search = ['suke_ops.business_search.awesomebar']
doc_events = {'Supply Decision': {'validate': 'suke_ops.api.validate_decision'},
              'Operations Approval': {'validate': 'suke_ops.approval.validate'},
              'Purchase Order': {'before_submit': ['suke_ops.approval.guard_submit', 'suke_ops.procurement_api.mandatory_approval']},
              'Sales Order': {'before_submit': 'suke_ops.approval.guard_submit'},
              'Work Order': {'before_submit': 'suke_ops.approval.guard_submit'},
              'Stock Entry': {'before_submit': 'suke_ops.approval.guard_submit'}}

after_migrate = 'suke_ops.procurement_setup.install'
for procurement_doctype in ('Procurement Process', 'Procurement Experience', 'Procurement Rule'):
    doc_events[procurement_doctype] = {'validate': 'suke_ops.procurement_api.guard'}
has_permission = {name: 'suke_ops.procurement_api.document_permission' for name in
                  ('Procurement Process', 'Procurement Experience', 'Procurement Rule')}
permission_query_conditions = {name: 'suke_ops.procurement_api.private_query' for name in
                               ('Procurement Process', 'Procurement Experience', 'Procurement Rule')}
