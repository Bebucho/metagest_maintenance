frappe.query_reports["Controlo de Compras de Manutenção"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Empresa"),
            fieldtype: "Link",
            options: "Company"
        },
        {
            fieldname: "work_order",
            label: __("Ordem de Trabalho"),
            fieldtype: "Link",
            options: "Maintenance Work Order"
        },
        {
            fieldname: "asset",
            label: __("Activo"),
            fieldtype: "Link",
            options: "Asset"
        },
        {
            fieldname: "procurement_type",
            label: __("Tipo de Aquisição"),
            fieldtype: "Select",
            options: "\nMaterials\nExternal Service"
        },
        {
            fieldname: "from_date",
            label: __("Requisição Desde"),
            fieldtype: "Date"
        },
        {
            fieldname: "to_date",
            label: __("Requisição Até"),
            fieldtype: "Date"
        }
    ]
};
