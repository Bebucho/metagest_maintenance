import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_fulfilment import (
    get_fulfilment_preview,
)


@frappe.whitelist()
def get_picking_progress(picking_name):
    """Read the current fulfilment state of a Maintenance Picking."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("read")

    if not picking.material_request:
        frappe.throw(_("Material Request reference is missing."))

    mr = frappe.get_doc(
        "Material Request", picking.material_request
    )
    mr.check_permission("read")

    if (
        picking.company != mr.company
        or picking.maintenance_work_order != mr.maintenance_work_order
        or mr.docstatus != 1
        or mr.material_request_type != "Material Issue"
    ):
        frappe.throw(_("Invalid Maintenance Picking references."))

    preview = get_fulfilment_preview(mr.name)

    required = 0.0
    issued = 0.0
    pending = 0.0
    pickable = 0.0
    shortage = 0.0
    procurement = 0.0

    lines = []

    for row in preview["items"]:
        required += flt(row["required"])
        issued += flt(row["gross_issued"])
        pending += flt(row["outstanding"])
        pickable += flt(row["pickable"])
        shortage += flt(row["shortage"])
        procurement += flt(row["procurement_uncovered"])

        lines.append({
            "item_code": row["item_code"],
            "material_request_item": row["material_request_item"],
            "maintenance_part": row["maintenance_part"],
            "required": row["required"],
            "issued": row["gross_issued"],
            "pending": row["outstanding"],
            "pickable": row["pickable"],
            "shortage": row["shortage"],
            "procurement_uncovered": row["procurement_uncovered"],
            "stock_verified": row["stock_verified"],
            "verification_status": row["verification_status"],
        })

    required = flt(required, 3)
    issued = flt(issued, 3)
    pending = flt(pending, 3)

    if pending <= 0:
        status = "Fulfilled"
    elif issued > 0:
        status = "Partially Fulfilled"
    elif preview["requires_stock_verification"]:
        status = "Awaiting Stock Verification"
    elif pickable > 0:
        status = "Ready for Picking"
    elif shortage > 0:
        status = "Awaiting Procurement"
    else:
        status = "Pending"

    return {
        "picking": picking.name,
        "current_document_status": picking.status,
        "fulfilment_status": status,
        "material_request": mr.name,
        "maintenance_work_order": picking.maintenance_work_order,
        "required": required,
        "issued": issued,
        "pending": pending,
        "pickable": flt(pickable, 3),
        "shortage": flt(shortage, 3),
        "procurement_uncovered": flt(procurement, 3),
        "requires_stock_verification":
            preview["requires_stock_verification"],
        "is_fulfilled": pending <= 0,
        "lines": lines,
    }
