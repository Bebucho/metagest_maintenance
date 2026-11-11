import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_stock_balance import (
    get_material_balance,
)


def validate_picking_issue_submission(doc, method=None):
    """Validate a maintenance issue linked to confirmed picking."""

    if doc.purpose != "Material Issue":
        return

    # Protect all new issues originating from maintenance requisitions.
    maintenance_request = False

    for row in doc.items:
        mr_name = row.get("material_request")

        if mr_name and frappe.db.get_value(
            "Material Request",
            mr_name,
            "maintenance_work_order",
        ):
            maintenance_request = True
            break

        if row.get("maintenance_work_order_part"):
            maintenance_request = True
            break

    is_maintenance = (
        bool(doc.get("maintenance_work_order"))
        or bool(doc.get("maintenance_picking"))
        or maintenance_request
    )

    if not is_maintenance:
        return

    if not doc.get("maintenance_work_order"):
        frappe.throw(
            _("Maintenance Work Order reference is required.")
        )

    if not doc.get("maintenance_picking"):
        frappe.throw(
            _("Maintenance Material Issues must originate "
              "from a Confirmed Maintenance Picking.")
        )

    if not set(frappe.get_roles()).intersection(
        {"Stock User", "Stock Manager"}
    ):
        frappe.throw(
            _("Warehouse permission is required."),
            frappe.PermissionError,
        )

    # Serialize all maintenance movements on the same Work Order.
    locked = frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (doc.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order not found."))

    picking = frappe.get_doc(
        "Maintenance Picking", doc.maintenance_picking
    )
    picking.check_permission("read")

    wo = frappe.get_doc(
        "Maintenance Work Order", doc.maintenance_work_order
    )
    mr = frappe.get_doc(
        "Material Request", picking.material_request
    )

    if picking.status != "Confirmed":
        frappe.throw(_("Picking must be Confirmed before issue."))

    if wo.docstatus == 2 or wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Work Order is closed or cancelled."))

    if (
        picking.maintenance_work_order != wo.name
        or picking.company != doc.company
        or wo.company != doc.company
        or mr.company != doc.company
        or mr.docstatus != 1
        or mr.material_request_type != "Material Issue"
        or mr.maintenance_work_order != wo.name
    ):
        frappe.throw(_("Invalid picking or material request references."))

    # Multiple issues are permitted against the same Picking.
    # The Work Order row lock, Confirmed status, current picked
    # quantities and outstanding-demand checks protect each cycle.
    # After successful submission, the Picking is reset or closed.

    parts = {p.name: p for p in wo.parts_and_materials}
    mr_rows = {r.name: r for r in mr.items}
    pick_rows = {r.name: r for r in picking.items}

    expected = {
        r.name: r for r in picking.items
        if flt(r.picked_qty, 3) > 0
    }

    if not expected or len(doc.items) != len(expected):
        frappe.throw(_("Stock Entry lines differ from confirmed picking."))

    seen = set()
    totals = {}

    for row in doc.items:
        pick = pick_rows.get(row.get("maintenance_picking_item"))

        if not pick or pick.name not in expected or pick.name in seen:
            frappe.throw(_("Invalid or duplicated picking line."))

        seen.add(pick.name)

        part = parts.get(pick.maintenance_work_order_part)
        mr_row = mr_rows.get(pick.material_request_item)

        if not part or not mr_row:
            frappe.throw(_("Invalid maintenance source references."))

        if (
            row.item_code != pick.item_code
            or row.item_code != part.item_code
            or row.item_code != mr_row.item_code
            or row.s_warehouse != picking.warehouse
            or part.warehouse != picking.warehouse
            or mr_row.warehouse != picking.warehouse
            or row.get("maintenance_work_order_part") != part.name
            or row.get("material_request") != mr.name
            or row.get("material_request_item") != mr_row.name
            or mr_row.maintenance_work_order_part != part.name
        ):
            frappe.throw(_("Stock Entry differs from confirmed picking."))

        quantity = flt(row.transfer_qty or row.qty, 3)
        confirmed = flt(pick.picked_qty, 3)

        if quantity <= 0 or abs(quantity - confirmed) > 0.0005:
            frappe.throw(
                _("Issued quantity must equal confirmed picking for {0}.")
                .format(row.item_code)
            )

        balance = get_material_balance(
            wo.name, part.name, part.item_code, part.warehouse
        )

        outstanding = max(
            flt(mr_row.qty) - flt(balance["gross_issued"]), 0
        )

        if quantity > outstanding + 0.0005:
            frappe.throw(
                _("Issue exceeds outstanding requirement for {0}.")
                .format(row.item_code)
            )

        totals[row.item_code] = (
            totals.get(row.item_code, 0) + quantity
        )

    if seen != set(expected):
        frappe.throw(_("Not all confirmed picking lines are included."))

    # Lock stock balance rows and recheck stock at submission.
    for item_code in sorted(totals):
        bins = frappe.db.sql(
            """
            SELECT actual_qty, reserved_qty
            FROM `tabBin`
            WHERE item_code = %s AND warehouse = %s
            FOR UPDATE
            """,
            (item_code, picking.warehouse),
        )

        actual = flt(bins[0][0]) if bins else 0
        reserved = flt(bins[0][1]) if bins else 0
        available = max(actual - reserved, 0)

        if totals[item_code] > available + 0.0005:
            frappe.throw(
                _("Insufficient stock to issue {0}.").format(item_code)
            )
