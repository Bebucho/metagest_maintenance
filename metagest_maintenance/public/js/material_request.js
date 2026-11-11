frappe.ui.form.on("Material Request", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;
        if (frm.doc.material_request_type !== "Material Issue") return;
        if (!frm.doc.maintenance_work_order) return;

        // Maintenance issues must originate from confirmed Pickings.
        // Preserve standard issuance for non-maintenance requests.
        frm.remove_custom_button(
            __("Issue Material"),
            __("Create")
        );

        const roles = frappe.user_roles || [];

        if (
            !roles.includes("Stock User") &&
            !roles.includes("Stock Manager")
        ) {
            return;
        }

        frm.add_custom_button(
            __("Prepare Maintenance Picking"),
            function () {
                frappe.call({
                    method:
                        "metagest_maintenance.maintenance_picking.prepare_picking",
                    args: {
                        material_request: frm.doc.name
                    },
                    freeze: true,
                    freeze_message: __("Preparing warehouse picking..."),
                    callback(r) {
                        if (!r.message) return;

                        if (!r.message.name) {
                            frappe.msgprint(
                                __("No outstanding materials to pick.")
                            );
                            return;
                        }

                        frappe.set_route(
                            "Form",
                            "Maintenance Picking",
                            r.message.name
                        );
                    }
                });
            },
            __("Maintenance Warehouse")
        );
    }
});
