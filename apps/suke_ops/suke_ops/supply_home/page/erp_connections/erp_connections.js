frappe.pages['erp-connections'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: '企业数据连接', single_column: true});
    frappe.require('/assets/suke_ops/finance_connections.js', () => {
        wrapper.erp_connections = new frappe.ERPConnections(page);
    });
};
frappe.pages['erp-connections'].on_page_show = function (wrapper) {
    if (wrapper.erp_connections) wrapper.erp_connections.load();
};
