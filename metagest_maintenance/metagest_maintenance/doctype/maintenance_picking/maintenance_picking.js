frappe.ui.form.on("Maintenance Picking", {
    refresh(frm) {
        if (frm.is_new()) return;

        const roles = frappe.user_roles || [];

        const warehouse_user =
            roles.includes("Stock User") ||
            roles.includes("Stock Manager");

        if (!warehouse_user) return;


        // Material procurement originates exclusively from Picking.
        if (
            ["Draft", "Prepared", "Partially Issued"].includes(
                frm.doc.status
            )
        ) {
            frm.add_custom_button(
                __("Request Missing Materials"),
                function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(
                            __("Save the Picking before requesting materials.")
                        );
                        return;
                    }

                    frappe.confirm(
                        __("Check uncovered shortages and prepare a Purchase Request?"),
                        function () {
                            frappe.call({
                                method:
                                    "metagest_maintenance.maintenance_procurement.request_procurement",
                                type: "POST",
                                args: {
                                    picking_name: frm.doc.name
                                },
                                freeze: true,
                                freeze_message:
                                    __("Checking outstanding procurement requirements..."),
                                callback(r) {
                                    const result = r.message;
                                    if (!result) return;

                                    if (!result.name) {
                                        frappe.msgprint(
                                            result.message ||
                                            __("No uncovered material shortages.")
                                        );
                                        return;
                                    }

                                    if (result.created) {
                                        frappe.show_alert({
                                            message: __(
                                                "Draft Purchase Request created: {0}",
                                                [result.name]
                                            ),
                                            indicator: "green"
                                        });
                                    } else {
                                        frappe.show_alert({
                                            message: __(
                                                "Existing Purchase Request: {0}",
                                                [result.name]
                                            ),
                                            indicator: "orange"
                                        });
                                    }

                                    frappe.set_route(
                                        "Form",
                                        "Material Request",
                                        result.name
                                    );
                                }
                            });
                        }
                    );
                },
                __("Warehouse Actions")
            );
        }


        if (
            ["Draft", "Prepared", "Partially Issued"].includes(
                frm.doc.status
            )
        ) {
            frm.add_custom_button(
                __("Refresh Availability"),
                function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(
                            __("Save picked quantities before refreshing.")
                        );
                        return;
                    }

                    frappe.call({
                        method:
                            "metagest_maintenance.maintenance_picking.refresh_picking_availability",
                        type: "POST",
                        args: {
                            picking_name: frm.doc.name
                        },
                        freeze: true,
                        callback(r) {
                            if (!r.message) return;

                            const result = r.message;

                            const rows = result.items.map(row => [
                                row.item_code,
                                row.outstanding,
                                row.available,
                                row.picked,
                                row.shortage,
                                row.verification_status || "-"
                            ]);

                            frm.reload_doc();

                            frappe.msgprint({
                                title: __("Updated Picking Availability"),
                                fields: [{
                                    fieldtype: "HTML",
                                    options: frappe.utils.escape_html(
                                        rows.map(row => row.join(" | ")).join("\\n")
                                    ).replace(/\\n/g, "<br>")
                                }],
                                message: result.requires_attention
                                    ? __("Selected quantities exceed current available stock.")
                                    : __("Availability refreshed."),
                                indicator: result.requires_attention
                                    ? "orange" : "green"
                            });
                        }
                    });
                },
                __("Warehouse Actions")
            );
        }




        // Optional inventory exception process.
        // Normal picking and issuance require no physical stock count.
        if (
            ["Draft", "Prepared", "Partially Issued"].includes(
                frm.doc.status
            ) &&
            frm.doc.items?.length
        ) {
            frm.add_custom_button(
                __("Report Stock Discrepancy"),
                function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(
                            __("Save the Picking first.")
                        );
                        return;
                    }

                    const rows = frm.doc.items.map(row => ({
                        label: `${row.item_code} (${row.name})`,
                        value: row.name
                    }));

                    const dialog = new frappe.ui.Dialog({
                        title: __("Select Material for Stock Verification"),
                        fields: [{
                            fieldname: "picking_item",
                            fieldtype: "Select",
                            label: __("Material"),
                            options: [
                                "",
                                ...rows.map(row => row.label)
                            ],
                            reqd: 1
                        }],
                        primary_action_label: __("Continue"),
                        primary_action(values) {
                            const selected = rows.find(
                                row => row.label === values.picking_item
                            );

                            if (!selected) return;

                            frappe.call({
                                method:
                                    "metagest_maintenance.maintenance_picking.create_picking_stock_verification",
                                type: "POST",
                                args: {
                                    picking_name: frm.doc.name,
                                    picking_item: selected.value
                                },
                                freeze: true,
                                callback(r) {
                                    if (!r.message?.name) return;

                                    dialog.hide();

                                    frappe.set_route(
                                        "Form",
                                        "Maintenance Stock Verification",
                                        r.message.name
                                    );
                                }
                            });
                        }
                    });

                    dialog.show();
                },
                __("Warehouse Actions")
            );
        }

        if (!["Draft", "Prepared", "Partially Issued"].includes(frm.doc.status)) {
            return;
        }

        frm.add_custom_button(
            __("Confirm & Issue Materials"),
            function () {
                if (frm.is_dirty()) {
                    frappe.msgprint(
                        __("Save the picked quantities before issuance.")
                    );
                    return;
                }

                frappe.confirm(
                    __("Confirm and issue the selected materials from warehouse?"),
                    function () {
                        frappe.call({
                            method:
                                "metagest_maintenance.maintenance_picking.confirm_and_issue_picking",
                            args: {
                                picking_name: frm.doc.name
                            },
                            freeze: true,
                            freeze_message: __("Issuing maintenance materials..."),
                            callback(r) {
                                if (!r.message) return;

                                frappe.msgprint({
                                    title: __("Materials Issued"),
                                    message: __(
                                        "Stock Entry {0} submitted. Quantity issued: {1}.",
                                        [
                                            r.message.stock_entry,
                                            r.message.issued_quantity
                                        ]
                                    ),
                                    indicator: "green"
                                });

                                frm.reload_doc();
                            }
                        });
                    }
                );
            },
            __("Warehouse Actions")
        );
    }
});
