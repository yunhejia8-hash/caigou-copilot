frappe.pages['procurement-workspace'].on_page_load = function(wrapper) {
    frappe.require(['/assets/suke_ops/procurement.js', '/assets/suke_ops/procurement.css'], () => {
        wrapper.procurement = new frappe.ProcurementWorkspace(wrapper);
    });
};
frappe.pages['procurement-workspace'].on_page_show = function(wrapper) {
    if (wrapper.procurement) wrapper.procurement.load();
};
