import frappe
from frappe import _
from frappe.utils import flt


def _can_read(doc):
    return frappe.has_permission(
        doc.doctype, "read", doc=doc
    )


def _records(doctype, filters):
    if not frappe.has_permission(doctype, "read"):
        return []

    result = []
    start = 0

    while True:
        batch = frappe.get_list(
            doctype,
            filters=filters,
            fields=["name"],
            limit_start=start,
            limit_page_length=200,
            order_by="name asc",
        )

        result.extend(r.name for r in batch)

        if len(batch) < 200:
            break

        start += 200

    return result


def _candidate_parents(child_dt, field, names):
    if not names:
        return set()

    # Candidate discovery only. Every returned parent is
    # individually checked for read permission below.
    rows = frappe.get_all(
        child_dt,
        filters={field: ["in", list(names)]},
        fields=["parent"],
        distinct=True,
        limit_page_length=0,
    )

    return {r.parent for r in rows}


def _add(result, group, doc, details=""):
    if not _can_read(doc):
        return

    key = (doc.doctype, doc.name)

    existing = {
        (r["doctype"], r["name"])
        for r in result[group]
    }

    if key in existing:
        return

    result[group].append({
        "doctype": doc.doctype,
        "name": doc.name,
        "status": str(
            doc.get("status")
            or ("Submitted" if doc.docstatus == 1
                else "Draft" if doc.docstatus == 0
                else "Cancelled")
        ),
        "details": str(details),
    })


@frappe.whitelist()
def get_work_order_traceability(work_order):
    if not frappe.db.exists(
        "Maintenance Work Order", work_order
    ):
        frappe.throw(_("Work Order not found."))

    wo = frappe.get_doc(
        "Maintenance Work Order", work_order
    )

    if not _can_read(wo):
        frappe.throw(
            _("Not permitted to read this Work Order."),
            frappe.PermissionError,
        )

    groups = {
        "pickings": [],
        "verifications": [],
        "material_requests": [],
        "purchase_orders": [],
        "purchase_receipts": [],
        "stock_entries": [],
        "cost_records": [],
        "external_services": [],
    }

    # Direct maintenance relationships.
    picking_names = _records(
        "Maintenance Picking",
        {"maintenance_work_order": work_order},
    )

    for name in picking_names:
        _add(
            groups, "pickings",
            frappe.get_doc("Maintenance Picking", name),
        )

    verification_names = _records(
        "Maintenance Stock Verification",
        {"maintenance_picking": ["in", picking_names]},
    ) if picking_names else []

    for name in verification_names:
        _add(
            groups, "verifications",
            frappe.get_doc(
                "Maintenance Stock Verification", name
            ),
        )

    # Material Requests directly linked to the WO or its pickings.
    mr_names = set(
        _records(
            "Material Request",
            {"maintenance_work_order": work_order},
        )
    )

    if picking_names:
        mr_names.update(
            _records(
                "Material Request",
                {
                    "custom_maintenance_picking":
                    ["in", picking_names]
                },
            )
        )

    for name in sorted(mr_names):
        mr = frappe.get_doc("Material Request", name)

        _add(
            groups, "material_requests", mr,
            mr.get("custom_maintenance_procurement_type")
            or "Maintenance",
        )

    # Purchase Orders linked through Material Request items.
    po_names = _candidate_parents(
        "Purchase Order Item",
        "material_request",
        mr_names,
    )

    readable_po_names = set()

    for name in sorted(po_names):
        po = frappe.get_doc("Purchase Order", name)

        if not _can_read(po):
            continue

        matching = [
            line for line in po.items
            if line.material_request in mr_names
        ]

        if not matching:
            continue

        readable_po_names.add(name)

        qty = sum(flt(line.qty) for line in matching)
        outstanding = sum(
            max(
                0,
                flt(line.qty) - flt(line.received_qty),
            )
            for line in matching
        )

        _add(
            groups, "purchase_orders", po,
            f"Quantidade: {qty:g} | "
            f"Por receber: {outstanding:g}",
        )

    # Purchase Receipts linked through PO or MR items.
    pr_names = set()

    pr_names.update(
        _candidate_parents(
            "Purchase Receipt Item",
            "purchase_order",
            readable_po_names,
        )
    )

    pr_names.update(
        _candidate_parents(
            "Purchase Receipt Item",
            "material_request",
            mr_names,
        )
    )

    for name in sorted(pr_names):
        pr = frappe.get_doc("Purchase Receipt", name)

        matching = [
            line for line in pr.items
            if (
                line.get("purchase_order") in readable_po_names
                or line.get("material_request") in mr_names
            )
        ]

        if matching:
            _add(
                groups, "purchase_receipts", pr,
                f"Linhas relacionadas: {len(matching)}",
            )

    # Stock Entries can link to WO or Picking.
    stock_names = set(
        _records(
            "Stock Entry",
            {"maintenance_work_order": work_order},
        )
    )

    if picking_names:
        stock_names.update(
            _records(
                "Stock Entry",
                {"maintenance_picking": ["in", picking_names]},
            )
        )

    for name in sorted(stock_names):
        _add(
            groups, "stock_entries",
            frappe.get_doc("Stock Entry", name),
        )

    # Existing analytical cost record.
    if wo.get("maintenance_cost_record"):
        name = wo.maintenance_cost_record

        if frappe.db.exists("Maintenance Cost Record", name):
            cost_doc = frappe.get_doc(
                "Maintenance Cost Record", name
            )

            _add(
                groups, "cost_records", cost_doc,
                "Custo total: {:,.2f}".format(
                    flt(cost_doc.get("total_cost"))
                ),
            )

    # External Services are Work Order child rows,
    # not independent documents.
    for row in wo.get("custom_external_services") or []:
        groups["external_services"].append({
            "service_row": row.name,
            "description": row.get("description")
                or row.get("service_item")
                or "",
            "material_request": row.get("material_request"),
            "purchase_order": row.get("purchase_order"),
            "acceptance_status": row.get("acceptance_status"),
            "financial_status": row.get("financial_status"),
            "estimated_cost": flt(row.get("estimated_cost")),
            "actual_cost": flt(row.get("actual_cost")),
            "received_quantity": flt(
                row.get("received_quantity")
            ),
        })

    return {
        "work_order": work_order,
        "asset": wo.get("asset"),
        "status": wo.get("status"),
        "groups": groups,
    }
