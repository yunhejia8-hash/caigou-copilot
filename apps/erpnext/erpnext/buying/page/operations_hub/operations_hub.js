frappe.pages['operations-hub'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: '供应协同工作台', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css', '/assets/suke_ops/hub.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHub(page);
    });
};
frappe.pages['operations-hub'].on_page_show = function (wrapper) {
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
