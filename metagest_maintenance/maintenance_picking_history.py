
import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_picking_issue_history(picking_name):
    """Get stock issues linked to a persistent Maintenance Picking."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("read")

    rows = frappe.db.sql(
        """
        SELECT
            se.name AS stock_entry,
            se.docstatus,
            se.posting_date,
            sed.name AS stock_entry_detail,
            sed.maintenance_picking_item AS picking_item,
            sed.material_request_item,
            sed.maintenance_work_order_part,
            sed.item_code,
            sed.s_warehouse AS warehouse,
            sed.qty,
            sed.transfer_qty
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name
        INNER JOIN `tabMaintenance Picking Item` pi
            ON pi.name = sed.maintenance_picking_item
           AND pi.parent = se.maintenance_picking
        WHERE se.maintenance_picking = %s
          AND se.maintenance_work_order = %s
          AND se.company = %s
          AND se.purpose = 'Material Issue'
          AND se.docstatus IN (1, 2)
        ORDER BY se.posting_date, se.creation, sed.idx
        """,
        (
            picking.name,
            picking.maintenance_work_order,
            picking.company,
        ),
        as_dict=True,
    )

    totals = {
        row.name: 0.0
        for row in picking.items
    }

    history = []

    for row in rows:
        qty = flt(row.transfer_qty, 3)

        if row.docstatus == 1:
            totals[row.picking_item] = flt(
                totals[row.picking_item] + qty, 3
            )

        history.append({
            "stock_entry": row.stock_entry,
            "stock_entry_detail": row.stock_entry_detail,
            "posting_date": str(row.posting_date),
            "status": (
                "Submitted" if row.docstatus == 1
                else "Cancelled"
            ),
            "picking_item": row.picking_item,
            "material_request_item": row.material_request_item,
            "maintenance_part": row.maintenance_work_order_part,
            "item_code": row.item_code,
            "warehouse": row.warehouse,
            "quantity": qty,
        })

    lines = []

    for row in picking.items:
        lines.append({
            "picking_item": row.name,
            "item_code": row.item_code,
            "required": flt(row.required_qty, 3),
            "issued": flt(totals[row.name], 3),
        })

    return {
        "picking": picking.name,
        "material_request": picking.material_request,
        "work_order": picking.maintenance_work_order,
        "issues": history,
        "lines": lines,
    }
