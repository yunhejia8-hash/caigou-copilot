frappe.pages['operations-data-buying'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: '采购数据', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css?v=home-v6.css', '/assets/suke_ops/hub.js?v=home-v6.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHub(page, 'data', 'buying');
    });
};
frappe.pages['operations-data-buying'].on_page_show = function(wrapper) {
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
