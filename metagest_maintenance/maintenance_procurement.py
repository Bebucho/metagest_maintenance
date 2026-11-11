import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate



@frappe.whitelist(methods=["POST"])
def request_procurement(picking_name):
    """Request missing materials exclusively from Maintenance Picking."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    if not {"Stock User", "Stock Manager"}.intersection(
        frappe.get_roles(frappe.session.user)
    ):
        frappe.throw(
            _("Only Warehouse personnel may request procurement."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc(
        "Maintenance Picking", picking_name
    )
    picking.check_permission("read")

    # Prevent concurrent procurement actions for the Work Order.
    locked = frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (picking.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order not found."))

    picking.reload()

    if picking.status not in (
        "Draft", "Prepared", "Partially Issued"
    ):
        frappe.throw(_("Picking is not open for procurement."))

    wo = frappe.get_doc(
        "Maintenance Work Order",
        picking.maintenance_work_order,
    )
    wo.check_permission("read")

    frappe.has_permission(
        "Material Request", "create", throw=True
    )

    if wo.docstatus == 2 or wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Work Order is closed or cancelled."))

    mr_source = frappe.get_doc(
        "Material Request", picking.material_request
    )
    mr_source.check_permission("read")

    if (
        mr_source.docstatus != 1
        or mr_source.material_request_type != "Material Issue"
        or mr_source.company != wo.company
        or mr_source.maintenance_work_order != wo.name
        or picking.company != wo.company
    ):
        frappe.throw(
            _("Invalid originating maintenance material request.")
        )

    if not picking.items:
        frappe.throw(_("Picking has no material lines."))

    source_rows = {row.name: row for row in mr_source.items}

    if len(source_rows) != len(mr_source.items):
        frappe.throw(_("Duplicate source request references."))

    for row in picking.items:
        source_row = source_rows.get(row.material_request_item)

        if (
            not source_row
            or row.item_code != source_row.item_code
            or picking.warehouse != source_row.warehouse
            or row.maintenance_work_order_part
                != source_row.maintenance_work_order_part
        ):
            frappe.throw(_("Picking material references do not match."))

    from metagest_maintenance.maintenance_fulfilment import (
        get_fulfilment_preview,
    )

    preview = get_fulfilment_preview(mr_source.name)

    pending = [
        line for line in preview["items"]
        if flt(line["outstanding"]) > 0
    ]

    if not pending:
        return {
            "name": None,
            "created": False,
            "message": "No outstanding material requirements.",
        }

    # Keep one open draft procurement request.
    # Submitted requests are already included in commitments.
    existing = frappe.get_all(
        "Material Request",
        filters={
            "maintenance_work_order": wo.name,
            "material_request_type": "Purchase",
            "custom_maintenance_procurement_type": "Materials",
            "custom_maintenance_picking": picking.name,
            "docstatus": 0,
        },
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )

    if existing:
        return {
            "name": existing[0].name,
            "created": False,
            "already_exists": True,
            "message": "Existing draft procurement request requires review.",
        }

    shortages = [
        line for line in pending
        if flt(line["procurement_uncovered"]) > 0
    ]

    if not shortages:
        return {
            "name": None,
            "created": False,
            "message": "No uncovered procurement shortage.",
        }

    request_date = getdate(nowdate())

    if wo.date_planned and getdate(wo.date_planned) > request_date:
        request_date = getdate(wo.date_planned)

    mr = frappe.new_doc("Material Request")
    mr.company = wo.company
    mr.material_request_type = "Purchase"
    mr.schedule_date = request_date
    mr.maintenance_work_order = wo.name
    mr.custom_maintenance_procurement_type = "Materials"
    mr.custom_maintenance_picking = picking.name

    for line in shortages:
        item = frappe.get_doc("Item", line["item_code"])

        if item.disabled or not item.is_stock_item:
            frappe.throw(
                _("Invalid stock item {0}.").format(line["item_code"])
            )

        mr.append("items", {
            "item_code": line["item_code"],
            "item_name": item.item_name,
            "qty": flt(line["procurement_uncovered"], 3),
            "warehouse": line["warehouse"],
            "schedule_date": request_date,
            "uom": item.stock_uom,
            "stock_uom": item.stock_uom,
            "conversion_factor": 1,
            "maintenance_work_order_part": line["maintenance_part"],
        })

    # Draft only. Procurement reviews and submits.
    mr.insert()

    return {
        "name": mr.name,
        "created": True,
        "shortages": shortages,
    }
