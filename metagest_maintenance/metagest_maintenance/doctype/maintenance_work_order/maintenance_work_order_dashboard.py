from frappe import _


def get_data():
    return {
        "fieldname": "maintenance_work_order",
        "non_standard_fieldnames": {
            "Maintenance Picking": "maintenance_work_order",
            "Material Request": "maintenance_work_order",
            "Stock Entry": "maintenance_work_order",
        },
        "transactions": [
            {
                "label": _("Armazém e Materiais"),
                "items": [
                    "Maintenance Picking",
                    "Stock Entry",
                ],
            },
            {
                "label": _("Compras e Aprovisionamento"),
                "items": [
                    "Material Request",
                ],
            },
        ],
    }
