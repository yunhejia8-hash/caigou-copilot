// 兼容旧的跨模块全局数据链接，避免工作区名称和数据范围不一致。
function redirect_legacy_operations_data() {
    const workspace = decodeURIComponent(window.location.pathname).split('/')[2];
    if (['selling', 'stock', 'manufacturing'].includes(workspace)) {
        window.location.replace('/desk/' + workspace + '/operations-data-' + workspace);
        return true;
    }
    if (workspace !== '主页') {
        window.location.replace('/desk/主页/operations-data');
        return true;
    }
    return false;
}
frappe.pages['operations-data'].on_page_load = function (wrapper) {
    if (redirect_legacy_operations_data()) return;
    const page = frappe.ui.make_app_page({parent: wrapper, title: '数据总览', single_column: true});
    frappe.require(['/assets/suke_ops/hub.css?v=procurement-approval-v1.css', '/assets/suke_ops/hub.js?v=procurement-approval-v1.js'], () => {
        wrapper.supply_hub = new frappe.SupplyHub(page, 'data', 'control');
    });
};
frappe.pages['operations-data'].on_page_show = function (wrapper) {
    if (redirect_legacy_operations_data()) return;
    if (wrapper.supply_hub) wrapper.supply_hub.refresh();
};
