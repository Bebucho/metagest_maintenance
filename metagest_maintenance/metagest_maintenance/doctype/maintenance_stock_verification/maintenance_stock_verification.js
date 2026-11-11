
frappe.ui.form.on("Maintenance Stock Verification", {
    refresh(frm) {

        const is_stock_manager =
            (frappe.user_roles || []).includes("Stock Manager");

        if (
            !frm.is_new() &&
            frm.doc.status === "Discrepancy" &&
            !frm.doc.stock_reconciliation &&
            is_stock_manager
        ) {
            frm.add_custom_button(
                __("Adjust Stock"),
                function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(
                            __("Save the document before adjustment.")
                        );
                        return;
                    }

                    const dialog = new frappe.ui.Dialog({
                        title: __("Authorise Stock Adjustment"),
                        fields: [
                            {
                                fieldtype: "HTML",
                                fieldname: "adjustment_summary",
                                options: `
                                    <p><strong>${__(
                                        "Item"
                                    )}:</strong> ${
                                        frappe.utils.escape_html(
                                            frm.doc.item_code || ""
                                        )
                                    }</p>
                                    <p><strong>${__(
                                        "System Quantity"
                                    )}:</strong> ${frm.doc.system_quantity}</p>
                                    <p><strong>${__(
                                        "Physical Quantity"
                                    )}:</strong> ${frm.doc.counted_quantity}</p>
                                    <p><strong>${__(
                                        "Difference"
                                    )}:</strong> ${frm.doc.difference}</p>
                                `
                            },
                            {
                                fieldname: "reason",
                                fieldtype: "Small Text",
                                label: __("Adjustment Reason"),
                                reqd: 1
                            }
                        ],
                        primary_action_label: __("Confirm Adjustment"),
                        primary_action(values) {
                            const reason = (values.reason || "").trim();

                            if (!reason) {
                                frappe.msgprint(
                                    __("Adjustment reason is required.")
                                );
                                return;
                            }

                            frappe.confirm(
                                __(
                                    "Submit the stock adjustment? " +
                                    "This will update the Stock Ledger."
                                ),
                                function () {
                                    dialog.get_primary_btn().prop(
                                        "disabled", true
                                    );

                                    frappe.call({
                                        method:
                                            "metagest_maintenance.metagest_maintenance.doctype.maintenance_stock_verification.maintenance_stock_verification.reconcile_stock_discrepancy",
                                        type: "POST",
                                        args: {
                                            name: frm.doc.name,
                                            reason: reason
                                        },
                                        freeze: true,
                                        freeze_message:
                                            __("Posting stock adjustment..."),
                                        callback(r) {
                                            if (r.message) {
                                                dialog.hide();

                                                frappe.show_alert({
                                                    message: __(
                                                        "Stock adjusted: {0}",
                                                        [
                                                            r.message
                                                                .stock_reconciliation
                                                        ]
                                                    ),
                                                    indicator: "green"
                                                });

                                                frm.reload_doc();
                                            } else {
                                                dialog.get_primary_btn().prop(
                                                    "disabled", false
                                                );
                                            }
                                        },
                                        error() {
                                            dialog.get_primary_btn().prop(
                                                "disabled", false
                                            );
                                        }
                                    });
                                }
                            );
                        }
                    });

                    dialog.show();
                },
                __("Warehouse Actions")
            );
        }


        if (frm.is_new() || frm.doc.status !== "Draft") return;

        const roles = frappe.user_roles || [];

        if (!roles.includes("Stock User") &&
            !roles.includes("Stock Manager")) return;

        frm.add_custom_button(
            __("Verify Count"),
            function () {
                if (frm.is_dirty()) {
                    frappe.msgprint(
                        __("Save the physical count before verification.")
                    );
                    return;
                }

                frappe.confirm(
                    __("Confirm that the physical stock count is complete?"),
                    function () {
                        frappe.call({
                            method:
                                "metagest_maintenance.metagest_maintenance.doctype.maintenance_stock_verification.maintenance_stock_verification.verify_count",
                            args: {
                                name: frm.doc.name
                            },
                            freeze: true,
                            freeze_message: __("Verifying stock count..."),
                            callback(r) {
                                if (!r.message) return;

                                frappe.show_alert({
                                    message: __("Stock count verified: {0}", [
                                        r.message.status
                                    ]),
                                    indicator:
                                        r.message.status === "Verified"
                                            ? "green" : "orange"
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
