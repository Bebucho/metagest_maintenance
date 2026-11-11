frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        // Loading the form must not modify material rows.
        if (!frm.is_new()) {
            frm.add_custom_button(
                __("Refresh Material Balances"),
                function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(
                            __("Save the Work Order before refreshing balances.")
                        );
                        return;
                    }

                    refresh_all_parts(frm);

                    frappe.show_alert({
                        message: __("Refreshing material availability"),
                        indicator: "blue"
                    });
                },
                __("Maintenance Actions")
            );
        }
    },

    company(frm) {
        refresh_all_parts(frm);
    }
});

frappe.ui.form.on("Maintenance Work Order Part", {
    item_code(frm, cdt, cdn) {
        fetch_part_stock(frm, cdt, cdn);
    },

    warehouse(frm, cdt, cdn) {
        fetch_part_stock(frm, cdt, cdn);
    },

    required_quantity(frm, cdt, cdn) {
        check_part_stock(cdt, cdn);
    }
});

function refresh_all_parts(frm) {
    (frm.doc.parts_and_materials || []).forEach(row => {
        fetch_part_stock(frm, row.doctype, row.name);
    });
}

function fetch_part_stock(frm, cdt, cdn) {
    const row = locals[cdt] && locals[cdt][cdn];

    if (!row) return;

    if (!frm.doc.company || !row.item_code || !row.warehouse) {
        frappe.model.set_value(cdt, cdn, "available_quantity", 0);
        return;
    }

    const company = frm.doc.company;
    const item_code = row.item_code;
    const warehouse = row.warehouse;

    frappe.call({
        method: "metagest_maintenance.api.get_part_availability",
        args: {
            item_code: item_code,
            warehouse: warehouse,
            company: company
        },

        callback(r) {
            const current = locals[cdt] && locals[cdt][cdn];

            if (!current || !r.message) return;

            // Prevent an outdated request from updating a changed row.
            if (
                frm.doc.company !== company ||
                current.item_code !== item_code ||
                current.warehouse !== warehouse
            ) {
                return;
            }

            const data = r.message;

            const updates = [
                ["available_quantity", data.available_qty],
                ["item_name", data.item_name],
                ["uom", data.uom],
                ["unit_cost", data.valuation_rate]
            ];

            // Update fields individually for ERPNext/Frappe v15.
            updates.forEach(([fieldname, value]) => {
                const current_value = current[fieldname];

                const unchanged =
                    current_value === value ||
                    (
                        current_value != null &&
                        value != null &&
                        String(current_value) === String(value)
                    );

                if (!unchanged) {
                    frappe.model.set_value(cdt, cdn, fieldname, value);
                }
            });

            frm.refresh_field("parts_and_materials");

            check_part_stock(cdt, cdn);
        },

        error() {
            frappe.show_alert({
                message: __("Unable to retrieve stock availability"),
                indicator: "red"
            });
        }
    });
}

function check_part_stock(cdt, cdn) {
    const row = locals[cdt] && locals[cdt][cdn];

    if (!row) return;

    const required = flt(row.required_quantity);
    const available = flt(row.available_quantity);

    if (required > available) {
        frappe.show_alert({
            message: __("Insufficient stock for {0}: required {1}, available {2}", [
                row.item_code,
                required,
                available
            ]),
            indicator: "orange"
        });
    }
}


// METAGEST MAINTENANCE - MATERIAL REQUEST ACTION

frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (frm.doc.docstatus === 2) return;
        if (["Closed", "Cancelled"].includes(frm.doc.status)) return;

        frm.add_custom_button(
            __("Request Materials"),
            function () {
                if (frm.is_dirty()) {
                    frappe.msgprint(
                        __("Save the Work Order before requesting materials.")
                    );
                    return;
                }

                frappe.call({
                    method:
                        "metagest_maintenance.maintenance_materials.request_materials",
                    args: {
                        work_order: frm.doc.name
                    },
                    freeze: true,
                    freeze_message: __("Preparing Material Request..."),
                    callback(r) {
                        if (!r.message || !r.message.name) return;

                        const mr_name = r.message.name;

                        if (!r.message.created) {
                            frappe.show_alert({
                                message: __("Opening existing Material Request {0}", [
                                    mr_name
                                ]),
                                indicator: "blue"
                            });
                        }

                        frappe.set_route(
                            "Form",
                            "Material Request",
                            mr_name
                        );
                    }
                });
            },
            __("Maintenance Actions")
        );
    }
});
// METAGEST MAINTENANCE - MATERIAL RETURNS

frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        if (frm.is_new() || frm.doc.docstatus === 2) return;
        if (["Closed", "Cancelled"].includes(frm.doc.status)) return;

        const has_unused_materials = (frm.doc.parts_and_materials || [])
            .some(row =>
                flt(row.issued_quantity) > flt(row.consumed_quantity)
            );

        if (!has_unused_materials) return;

        frm.add_custom_button(
            __("Return Unused Materials"),
            function () {
                if (frm.is_dirty()) {
                    frappe.msgprint(__("Save the Work Order first."));
                    return;
                }

                frappe.call({
                    method:
                        "metagest_maintenance.maintenance_returns.request_material_return",
                    args: {
                        work_order: frm.doc.name
                    },
                    freeze: true,
                    freeze_message: __("Preparing material return..."),
                    callback(r) {
                        if (!r.message || !r.message.name) return;

                        frappe.set_route(
                            "Form",
                            "Stock Entry",
                            r.message.name
                        );
                    }
                });
            },
            __("Maintenance Actions")
        );
    }
});



// INALCA_EXTERNAL_SERVICE_REQUEST_V2
frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        if (
            frm.is_new() ||
            !["Approved", "In Progress"].includes(frm.doc.status)
        ) {
            return;
        }

        frm.add_custom_button(
            __("Request External Service"),
            () => {
                if (frm.is_dirty()) {
                    frappe.msgprint(
                        __("Save the Work Order before requesting procurement.")
                    );
                    return;
                }

                const services = (
                    frm.doc.custom_external_services || []
                ).filter(row => row.service_item);

                if (!services.length) {
                    frappe.msgprint(
                        __("Add an External Services row and save first.")
                    );
                    return;
                }

                const options = services.map(row => ({
                    label:
                        `${row.idx}. ${row.service_item}`
                        + ` — ${row.description || ""}`
                        + (row.material_request
                            ? ` [${row.material_request}]`
                            : ""),
                    value: row.name,
                }));

                const dialog = new frappe.ui.Dialog({
                    title: __("Request External Service"),
                    fields: [{
                        fieldname: "service_row",
                        label: __("External Service"),
                        fieldtype: "Select",
                        options: options,
                        reqd: 1,
                    }],
                    primary_action_label: __("Open / Create Purchase Request"),
                    primary_action(values) {
                        frappe.call({
                            method:
                                "metagest_maintenance."
                                + "maintenance_external_services."
                                + "request_external_service",
                            args: {
                                work_order: frm.doc.name,
                                service_row: values.service_row,
                            },
                            freeze: true,
                            callback(r) {
                                if (!r.message) return;

                                dialog.hide();

                                frappe.set_route(
                                    "Form",
                                    "Material Request",
                                    r.message.name
                                );
                            },
                        });
                    },
                });

                dialog.show();
            },
            __("Maintenance Actions")
        );
    },
});

// INALCA_EXTERNAL_SERVICES_TABLE_V1

frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        frm.set_query(
            "service_item",
            "custom_external_services",
            () => ({
                filters: {
                    is_stock_item: 0,
                    is_purchase_item: 1,
                    disabled: 0,
                },
            })
        );
    },
});

frappe.ui.form.on("Maintenance Work Order External Service", {
    quantity(frm, cdt, cdn) {
        calculate_external_service_estimate(cdt, cdn);
    },

    estimated_rate(frm, cdt, cdn) {
        calculate_external_service_estimate(cdt, cdn);
    },
});

function calculate_external_service_estimate(cdt, cdn) {
    const row = locals[cdt][cdn];

    const quantity = flt(row.quantity);
    const rate = flt(row.estimated_rate);

    const total = flt(quantity * rate, 2);

    if (flt(row.estimated_cost, 2) !== total) {
        frappe.model.set_value(
            cdt,
            cdn,
            "estimated_cost",
            total
        );
    }
}


// INALCA_SERVICE_ACCEPTANCE_V1
frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        if (
            frm.is_new() ||
            !["Approved", "In Progress", "Completed"].includes(
                frm.doc.status
            )
        ) {
            return;
        }

        frm.add_custom_button(
            __("Accept External Service"),
            () => {
                if (frm.is_dirty()) {
                    frappe.msgprint(
                        __("Save the Work Order before accepting a service.")
                    );
                    return;
                }

                const services = (
                    frm.doc.custom_external_services || []
                ).filter(row =>
                    row.material_request &&
                    row.acceptance_status !== "Accepted"
                );

                if (!services.length) {
                    frappe.msgprint(
                        __("No external services are pending acceptance.")
                    );
                    return;
                }

                const dialog = new frappe.ui.Dialog({
                    title: __("External Service Acceptance"),
                    fields: [
                        {
                            fieldname: "service_row",
                            label: __("External Service"),
                            fieldtype: "Select",
                            options: services.map(row => ({
                                label:
                                    `${row.idx}. ${row.service_item}`
                                    + ` — ${row.description || ""}`,
                                value: row.name,
                            })),
                            reqd: 1,
                        },
                        {
                            fieldname: "notes",
                            label: __("Acceptance Observations"),
                            fieldtype: "Small Text",
                            reqd: 1,
                        },
                    ],
                    primary_action_label: __("Confirm Acceptance"),
                    primary_action(values) {
                        frappe.call({
                            method:
                                "metagest_maintenance."
                                + "maintenance_service_acceptance."
                                + "accept_external_service",
                            args: {
                                work_order: frm.doc.name,
                                service_row: values.service_row,
                                notes: values.notes,
                            },
                            freeze: true,
                            callback(r) {
                                if (!r.message) return;
                                dialog.hide();
                                frm.reload_doc();
                            },
                        });
                    },
                });

                dialog.show();
            },
            __("Maintenance Actions")
        );
    },
});


// INALCA DOCUMENT TRACEABILITY - STEP 211C
frappe.ui.form.on("Maintenance Work Order", {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(
            __("Rastreabilidade Documental"),
            () => {
                frappe.call({
                    method:
                        "metagest_maintenance.maintenance_traceability." +
                        "get_work_order_traceability",
                    args: {
                        work_order: frm.doc.name
                    },
                    freeze: true,
                    callback(r) {
                        if (!r.message) return;

                        const data = r.message;
                        const groups = data.groups || {};

                        const labels = {
                            pickings: "Maintenance Picking",
                            verifications: "Verificações de Stock",
                            material_requests: "Requisições de Compra",
                            purchase_orders: "Ordens de Compra",
                            purchase_receipts: "Recepções",
                            stock_entries: "Saídas de Armazém",
                            cost_records: "Registos de Custos",
                            external_services: "Serviços Externos"
                        };

                        const escapeHTML = (value) =>
                            String(value ?? "").replace(
                                /[&<>"']/g,
                                char => ({
                                    "&": "&amp;",
                                    "<": "&lt;",
                                    ">": "&gt;",
                                    '"': "&quot;",
                                    "'": "&#39;"
                                })[char]
                            );

                        let html = "";

                        for (const [key, title] of Object.entries(labels)) {
                            const records = groups[key] || [];

                            html += `
                                <h5 style="margin-top:18px">
                                    ${escapeHTML(title)}
                                    (${records.length})
                                </h5>
                            `;

                            if (!records.length) {
                                html += `<p class="text-muted">Sem registos.</p>`;
                                continue;
                            }

                            html += `<table class="table table-bordered table-sm">
                                <thead>
                                    <tr>
                                        <th>Documento / Serviço</th>
                                        <th>Estado / Informação</th>
                                    </tr>
                                </thead>
                                <tbody>`;

                            for (const record of records) {
                                if (key === "external_services") {
                                    html += `
                                        <tr>
                                            <td>
                                                ${escapeHTML(record.description)}
                                                <div class="text-muted small">
                                                    MR: ${escapeHTML(record.material_request || "-")}
                                                    | PO: ${escapeHTML(record.purchase_order || "-")}
                                                </div>
                                            </td>
                                            <td>
                                                ${escapeHTML(record.acceptance_status || "-")}
                                                /
                                                ${escapeHTML(record.financial_status || "-")}
                                                <div class="small">
                                                    Estimado: ${escapeHTML(record.estimated_cost)}
                                                    | Real: ${escapeHTML(record.actual_cost)}
                                                </div>
                                            </td>
                                        </tr>`;
                                } else {
                                    html += `
                                        <tr>
                                            <td>
                                                <a href="#"
                                                   class="inalca-trace-link"
                                                   data-dt="${escapeHTML(record.doctype)}"
                                                   data-name="${escapeHTML(record.name)}">
                                                    ${escapeHTML(record.name)}
                                                </a>
                                            </td>
                                            <td>
                                                ${escapeHTML(record.status)}
                                                <div class="text-muted small">
                                                    ${escapeHTML(record.details || "")}
                                                </div>
                                            </td>
                                        </tr>`;
                                }
                            }

                            html += `</tbody></table>`;
                        }

                        const dialog = new frappe.ui.Dialog({
                            title: __("Rastreabilidade Documental"),
                            size: "extra-large",
                            fields: [{
                                fieldname: "trace_html",
                                fieldtype: "HTML"
                            }]
                        });

                        const wrapper =
                            dialog.fields_dict.trace_html.$wrapper;

                        wrapper.html(`
                            <div style="max-height:70vh;overflow:auto">
                                <h4>${escapeHTML(data.work_order)}</h4>
                                <p>
                                    Activo: ${escapeHTML(data.asset || "-")}
                                    | Estado: ${escapeHTML(data.status || "-")}
                                </p>
                                ${html}
                            </div>
                        `);

                        wrapper.on(
                            "click",
                            ".inalca-trace-link",
                            function(e) {
                                e.preventDefault();

                                const dt = $(this).attr("data-dt");
                                const name = $(this).attr("data-name");

                                dialog.hide();
                                frappe.set_route("Form", dt, name);
                            }
                        );

                        dialog.show();
                    }
                });
            },
            __("Manutenção")
        );
    }
});
