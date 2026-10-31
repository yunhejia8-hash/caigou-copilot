frappe.pages['operations-data-manufacturing'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: '生产数据', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css?v=procurement-approval-v1.css', '/assets/suke_ops/hub.js?v=procurement-approval-v1.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHub(page, 'data', 'manufacturing');
    });
};
frappe.pages['operations-data-manufacturing'].on_page_show = function(wrapper) {
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
