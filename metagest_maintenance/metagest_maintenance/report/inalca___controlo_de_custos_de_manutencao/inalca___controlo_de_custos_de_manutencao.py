import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = frappe._dict(filters or {})

    if not frappe.has_permission("Maintenance Work Order", "read"):
        frappe.throw(
            _("Maintenance Work Order read permission required"),
            frappe.PermissionError,
        )

    columns = [
        {"fieldname": "work_order", "label": _("Ordem de Trabalho"),
         "fieldtype": "Link", "options": "Maintenance Work Order", "width": 170},
        {"fieldname": "asset", "label": _("Activo"),
         "fieldtype": "Link", "options": "Asset", "width": 150},
        {"fieldname": "asset_location", "label": _("Localização"),
         "fieldtype": "Link", "options": "Location", "width": 140},
        {"fieldname": "maintenance_type", "label": _("Tipo"),
         "fieldtype": "Data", "width": 150},
        {"fieldname": "status", "label": _("Estado"),
         "fieldtype": "Data", "width": 110},
        {"fieldname": "date_planned", "label": _("Data Prevista"),
         "fieldtype": "Date", "width": 110},
        {"fieldname": "completion_date", "label": _("Conclusão"),
         "fieldtype": "Date", "width": 110},
        {"fieldname": "parts_cost", "label": _("Materiais"),
         "fieldtype": "Currency", "width": 140},
        {"fieldname": "labour_cost", "label": _("Mão-de-Obra"),
         "fieldtype": "Currency", "width": 140},
        {"fieldname": "external_cost", "label": _("Serviços Externos"),
         "fieldtype": "Currency", "width": 150},
        {"fieldname": "actual_total", "label": _("Custo Real"),
         "fieldtype": "Currency", "width": 150},
        {"fieldname": "external_estimate", "label": _("Estimativa Serviços"),
         "fieldtype": "Currency", "width": 160},
        {"fieldname": "cost_record", "label": _("Registo de Custos"),
         "fieldtype": "Link", "options": "Maintenance Cost Record", "width": 170},
        {"fieldname": "reconciliation", "label": _("Reconciliação"),
         "fieldtype": "Data", "width": 160},
    ]

    conditions = {}

    for key in ("company", "asset", "maintenance_type", "status"):
        if filters.get(key):
            conditions[key] = filters[key]

    if filters.get("from_date") and filters.get("to_date"):
        if filters.from_date > filters.to_date:
            frappe.throw(_("Data inicial posterior à data final"))

    if filters.get("from_date"):
        conditions["date_planned"] = [
            ">=", filters.from_date
        ]

    if filters.get("to_date"):
        if "date_planned" in conditions:
            conditions["date_planned"] = [
                "between",
                [filters.from_date, filters.to_date],
            ]
        else:
            conditions["date_planned"] = [
                "<=", filters.to_date
            ]

    data = []
    offset = 0

    while True:
        batch = frappe.get_list(
            "Maintenance Work Order",
            filters=conditions,
            fields=[
                "name", "asset", "asset_location", "maintenance_type",
                "status", "date_planned", "completion_date",
                "parts_total_cost", "labour_total_cost",
                "external_services_total", "total_maintenance_cost",
                "maintenance_cost_record",
            ],
            order_by="date_planned desc, name desc",
            limit_start=offset,
            limit_page_length=200,
        )

        if not batch:
            break

        for wo in batch:
            parts = flt(wo.parts_total_cost)
            labour = flt(wo.labour_total_cost)
            external = flt(wo.external_services_total)
            actual = flt(wo.total_maintenance_cost)

            # External service estimates are not actual expenditure.
            document = frappe.get_doc(
                "Maintenance Work Order", wo.name
            )

            external_estimate = sum(
                flt(service.estimated_cost)
                for service in (
                    document.get("custom_external_services") or []
                )
            )

            cost_record = None
            reconciliation = "Sem registo de custos"

            if wo.maintenance_cost_record:
                if frappe.db.exists(
                    "Maintenance Cost Record",
                    wo.maintenance_cost_record,
                ):
                    record = frappe.get_doc(
                        "Maintenance Cost Record",
                        wo.maintenance_cost_record,
                    )

                    if frappe.has_permission(
                        "Maintenance Cost Record",
                        "read",
                        doc=record,
                    ):
                        cost_record = record.name
                        record_total = flt(record.total_cost)

                        if abs(actual - record_total) <= 0.01:
                            reconciliation = "Conciliado"
                        else:
                            reconciliation = "Diferença de custos"
                    else:
                        reconciliation = "Registo sem acesso"
                else:
                    reconciliation = "Registo inexistente"

            if abs(actual - (parts + labour + external)) > 0.01:
                reconciliation = "Diferença nos componentes"

            data.append({
                "work_order": wo.name,
                "asset": wo.asset,
                "asset_location": wo.asset_location,
                "maintenance_type": wo.maintenance_type,
                "status": wo.status,
                "date_planned": wo.date_planned,
                "completion_date": wo.completion_date,
                "parts_cost": parts,
                "labour_cost": labour,
                "external_cost": external,
                "actual_total": actual,
                "external_estimate": external_estimate,
                "cost_record": cost_record,
                "reconciliation": reconciliation,
            })

        offset += len(batch)

        if len(batch) < 200:
            break

    return columns, data
