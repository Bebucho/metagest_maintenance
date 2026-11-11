import frappe
from frappe import _
from frappe.utils import flt


OPEN_PICKING_STATUSES = [
    "Draft", "Prepared", "Confirmed", "Partially Issued"
]


def execute(filters=None):
    filters = frappe._dict(filters or {})

    if not frappe.has_permission("Maintenance Work Order", "read"):
        frappe.throw(
            _("Maintenance Work Order read permission required"),
            frappe.PermissionError,
        )

    columns = [
        dict(fieldname="work_order", label="Ordem de Trabalho",
             fieldtype="Link", options="Maintenance Work Order", width=170),
        dict(fieldname="asset", label="Activo",
             fieldtype="Link", options="Asset", width=150),
        dict(fieldname="maintenance_type", label="Tipo Manutenção",
             fieldtype="Data", width=150),
        dict(fieldname="wo_status", label="Estado OT",
             fieldtype="Data", width=110),
        dict(fieldname="date_planned", label="Data Prevista",
             fieldtype="Date", width=115),
        dict(fieldname="exception_type", label="Excepção",
             fieldtype="Data", width=190),
        dict(fieldname="document_type", label="Tipo Documento",
             fieldtype="Link", options="DocType", width=145),
        dict(fieldname="document_name", label="Documento",
             fieldtype="Dynamic Link", options="document_type", width=170),
        dict(fieldname="document_status", label="Estado Documento",
             fieldtype="Data", width=145),
        dict(fieldname="item_code", label="Artigo / Serviço",
             fieldtype="Link", options="Item", width=155),
        dict(fieldname="pending_qty", label="Qtd. Pendente",
             fieldtype="Float", width=125),
        dict(fieldname="uom", label="Unidade",
             fieldtype="Data", width=85),
        dict(fieldname="required_action", label="Acção Necessária",
             fieldtype="Data", width=260),
    ]

    rows = []
    seen = set()

    def add(wo, exception, dt, name, status, action,
            item_code=None, qty=None, uom=None):
        if (
            filters.get("exception_type")
            and filters.exception_type != exception
        ):
            return

        key = (wo.name, exception, dt, name, item_code, action)
        if key in seen:
            return
        seen.add(key)

        rows.append({
            "work_order": wo.name,
            "asset": wo.asset,
            "maintenance_type": wo.maintenance_type,
            "wo_status": wo.status,
            "date_planned": wo.date_planned,
            "exception_type": exception,
            "document_type": dt,
            "document_name": name,
            "document_status": status,
            "item_code": item_code,
            "pending_qty": qty,
            "uom": uom,
            "required_action": action,
        })

    def visible_names(dt, conditions):
        if not frappe.has_permission(dt, "read"):
            return []

        result = []
        offset = 0

        while True:
            batch = frappe.get_list(
                dt,
                filters=conditions,
                fields=["name"],
                limit_start=offset,
                limit_page_length=200,
                order_by="name asc",
            )
            if not batch:
                break

            result.extend(r.name for r in batch)
            offset += len(batch)

        return result

    conditions = {
        "status": ["not in", ["Closed", "Cancelled"]],
    }

    for key in ("company", "asset", "maintenance_type"):
        if filters.get(key):
            conditions[key] = filters[key]

    if filters.get("from_date") and filters.get("to_date"):
        if str(filters.from_date) > str(filters.to_date):
            frappe.throw(_("Invalid date range"))
        conditions["date_planned"] = [
            "between", [filters.from_date, filters.to_date]
        ]
    elif filters.get("from_date"):
        conditions["date_planned"] = [">=", filters.from_date]
    elif filters.get("to_date"):
        conditions["date_planned"] = ["<=", filters.to_date]

    wo_names = visible_names("Maintenance Work Order", conditions)

    for wo_name in wo_names:
        wo = frappe.get_doc("Maintenance Work Order", wo_name)
        initial_count = len(rows)

        # 1. Maintenance Picking
        picking_names = visible_names(
            "Maintenance Picking",
            {"maintenance_work_order": wo.name},
        )

        for picking_name in picking_names:
            picking = frappe.get_doc(
                "Maintenance Picking", picking_name
            )

            if picking.status in OPEN_PICKING_STATUSES:
                add(
                    wo, "Picking pendente",
                    "Maintenance Picking", picking.name,
                    picking.status,
                    "Preparar, confirmar ou emitir materiais",
                )

        # 2. Stock verification
        if picking_names:
            verification_names = visible_names(
                "Maintenance Stock Verification",
                {
                    "maintenance_picking":
                    ["in", picking_names],
                    "status": "Discrepancy",
                },
            )

            for name in verification_names:
                verification = frappe.get_doc(
                    "Maintenance Stock Verification", name
                )

                add(
                    wo, "Divergência de stock",
                    "Maintenance Stock Verification",
                    verification.name,
                    verification.status,
                    "Investigar e reconciliar divergência",
                )

        # 3. Purchase requirements
        mr_names = set(visible_names(
            "Material Request",
            {
                "maintenance_work_order": wo.name,
                "docstatus": 1,
            },
        ))

        if picking_names:
            mr_names.update(visible_names(
                "Material Request",
                {
                    "custom_maintenance_picking":
                    ["in", picking_names],
                    "docstatus": 1,
                },
            ))

        approved_mrs = {}
        mr_item_names = set()

        for mr_name in sorted(mr_names):
            mr = frappe.get_doc("Material Request", mr_name)

            if (
                mr.material_request_type != "Purchase"
                or mr.status in ("Stopped", "Cancelled")
            ):
                continue

            approved_mrs[mr.name] = mr

            for line in mr.items:
                mr_item_names.add(line.name)

                outstanding = max(
                    0,
                    flt(line.qty, 6)
                    - flt(line.ordered_qty, 6),
                )

                if outstanding > 0.000001:
                    add(
                        wo, "Requisição por encomendar",
                        "Material Request", mr.name,
                        mr.status,
                        "Emitir Ordem de Compra",
                        line.item_code,
                        outstanding,
                        line.uom,
                    )

        # 4. Maintenance-linked Purchase Orders.
        # Child rows discover candidate PO names only.
        # PO details are read only after permission checks.
        if mr_item_names and frappe.has_permission(
            "Purchase Order", "read"
        ):
            candidate_po_names = set()

            mr_list = list(approved_mrs)

            for start in range(0, len(mr_list), 200):
                candidate_items = frappe.get_all(
                    "Purchase Order Item",
                    filters={
                        "material_request": [
                            "in", mr_list[start:start + 200]
                        ],
                    },
                    fields=["parent"],
                )

                for candidate in candidate_items:
                    candidate_po_names.add(candidate.parent)

            for po_name in sorted(candidate_po_names):
                if not frappe.has_permission(
                    "Purchase Order", "read", doc=po_name
                ):
                    continue

                po = frappe.get_doc("Purchase Order", po_name)

                if (
                    po.docstatus != 1
                    or po.status in (
                        "Closed", "Cancelled", "Completed",
                    )
                ):
                    continue

                for line in po.items:
                    if (
                        line.material_request not in approved_mrs
                        or line.material_request_item
                            not in mr_item_names
                    ):
                        continue

                    outstanding = max(
                        0,
                        flt(line.qty, 6)
                        - flt(line.received_qty, 6),
                    )

                    if outstanding > 0.000001:
                        add(
                            wo, "Compra por recepcionar",
                            "Purchase Order", po.name,
                            po.status,
                            "Recepcionar quantidade pendente",
                            line.item_code,
                            outstanding,
                            line.uom,
                        )

        # 5. External services
        for service in (
            wo.get("custom_external_services") or []
        ):
            qty = flt(service.quantity, 6)
            received = flt(service.received_quantity, 6)
            outstanding = max(0, qty - received)

            if not service.material_request:
                add(
                    wo, "Serviço por requisitar",
                    "Maintenance Work Order", wo.name,
                    service.procurement_status or "Not Requested",
                    "Criar requisição do serviço externo",
                    service.service_item,
                    qty,
                )

            elif service.acceptance_status != "Accepted":
                add(
                    wo, "Serviço por aceitar",
                    "Maintenance Work Order", wo.name,
                    service.acceptance_status or "Not Started",
                    "Validar execução e obter aceitação do supervisor",
                    service.service_item,
                    qty,
                )

            elif outstanding > 0.000001:
                add(
                    wo, "Serviço por recepcionar",
                    "Maintenance Work Order", wo.name,
                    service.financial_status or "Not Received",
                    "Completar Purchase Receipt do serviço",
                    service.service_item,
                    outstanding,
                )

        # 6. Completed Work Orders with outstanding exceptions.
        if wo.status == "Completed" and len(rows) > initial_count:
            add(
                wo, "Ordem concluída com pendências",
                "Maintenance Work Order", wo.name,
                wo.status,
                "Regularizar pendências antes do encerramento",
            )

    rows.sort(
        key=lambda r: (
            str(r.get("date_planned") or ""),
            str(r.get("work_order") or ""),
            str(r.get("exception_type") or ""),
        )
    )

    return columns, rows
