app_name = 'suke_ops'
app_title = 'Supply Operations'
app_publisher = 'SUKE'
app_description = '采购、销售、库存与生产协同'
app_email = 'demo@example.invalid'
app_license = 'MIT'
app_include_js = ['/assets/suke_ops/ops.js']
doc_events = {'Supply Decision': {'validate': 'suke_ops.api.validate_decision'},
              'Operations Approval': {'validate': 'suke_ops.approval.validate'},
              'Purchase Order': {'before_submit': 'suke_ops.approval.guard_submit'},
              'Sales Order': {'before_submit': 'suke_ops.approval.guard_submit'},
              'Work Order': {'before_submit': 'suke_ops.approval.guard_submit'},
              'Stock Entry': {'before_submit': 'suke_ops.approval.guard_submit'}}
