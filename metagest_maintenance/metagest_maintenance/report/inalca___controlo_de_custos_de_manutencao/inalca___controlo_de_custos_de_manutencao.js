frappe.query_reports["INALCA - Controlo de Custos de Manutencao"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Empresa"),
            fieldtype: "Link",
            options: "Company"
        },
        {
            fieldname: "asset",
            label: __("Activo"),
            fieldtype: "Link",
            options: "Asset"
        },
        {
            fieldname: "maintenance_type",
            label: __("Tipo de Manutenção"),
            fieldtype: "Select",
            options: "\nPreventive Maintenance\nCorrective Maintenance\nFacilities"
        },
        {
            fieldname: "status",
            label: __("Estado"),
            fieldtype: "Select",
            options: "\nDraft\nApproved\nIn Progress\nCompleted\nClosed\nCancelled"
        },
        {
            fieldname: "from_date",
            label: __("Data Prevista - De"),
            fieldtype: "Date"
        },
        {
            fieldname: "to_date",
            label: __("Data Prevista - Até"),
            fieldtype: "Date"
        }
    ]
};
