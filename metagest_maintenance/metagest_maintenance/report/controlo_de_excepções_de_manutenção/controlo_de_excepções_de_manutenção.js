frappe.query_reports["Controlo de Excepções de Manutenção"] = {
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
            fieldname: "exception_type",
            label: __("Tipo de Excepção"),
            fieldtype: "Select",
            options: [
                "",
                "Picking pendente",
                "Divergência de stock",
                "Requisição por encomendar",
                "Compra por recepcionar",
                "Serviço por requisitar",
                "Serviço por aceitar",
                "Serviço por recepcionar",
                "Ordem concluída com pendências"
            ].join("\n")
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
