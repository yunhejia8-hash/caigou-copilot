function redirect_operations_home() {
    if (decodeURIComponent(window.location.pathname).split('/')[2] !== '主页') {
        window.location.replace('/desk/主页/operations-hub');
        return true;
    }
    return false;
}
frappe.pages['operations-hub'].on_page_load = function (wrapper) {
    if (redirect_operations_home()) return;
    const page = frappe.ui.make_app_page({parent: wrapper, title: '总控主页', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css?v=procurement-qa-v1.css', '/assets/suke_ops/hub.js?v=procurement-qa-v1.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHome(page);
    });
};
frappe.pages['operations-hub'].on_page_show = function (wrapper) {
    if (redirect_operations_home()) return;
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
