frappe.pages['operations-manufacturing'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: '生产工作台', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css?v=procurement-qa-v1.css', '/assets/suke_ops/hub.js?v=procurement-qa-v1.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHub(page, 'chat', 'manufacturing');
    });
};
frappe.pages['operations-manufacturing'].on_page_show = function(wrapper) {
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
