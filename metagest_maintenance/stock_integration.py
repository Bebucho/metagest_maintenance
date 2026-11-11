# Copyright (c) 2026, TeorLogico
# Metagest Maintenance - Stock Integration

import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_stock_balance import get_material_balance


def validate_stock_entry(doc, method=None):
    """Validate maintenance references on warehouse issues."""

    wo_name = doc.get("maintenance_work_order")

    if not wo_name:
        return

    if doc.purpose == "Material Receipt":
        validate_maintenance_return_draft(doc)
        return

    if doc.purpose != "Material Issue":
        frappe.throw(
            _("Maintenance Stock Entries must use Material Issue.")
        )

    wo = frappe.get_doc("Maintenance Work Order", wo_name)

    if doc.company != wo.company:
        frappe.throw(_("Stock Entry and Work Order companies differ."))

    if wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Cannot issue materials to a closed Work Order."))

    for row in doc.items:
        part_name = row.get("maintenance_work_order_part")

        if not part_name:
            frappe.throw(
                _("Maintenance part reference missing on row {0}.")
                .format(row.idx)
            )

        part = frappe.db.get_value(
            "Maintenance Work Order Part",
            part_name,
            ["parent", "item_code", "warehouse"],
            as_dict=True,
        )

        if not part or part.parent != wo.name:
            frappe.throw(
                _("Invalid maintenance part reference on row {0}.")
                .format(row.idx)
            )

        if (
            part.item_code != row.item_code
            or part.warehouse != row.s_warehouse
        ):
            frappe.throw(
                _("Item or warehouse differs from the Work Order on row {0}.")
                .format(row.idx)
            )


def reconcile_maintenance_issues(work_order, exclude_stock_entry=None):
    """Synchronise net issued quantities from submitted stock transactions."""

    if not work_order:
        return

    from metagest_maintenance.maintenance_stock_balance import (
        get_material_balance,
    )

    wo = frappe.get_doc("Maintenance Work Order", work_order)

    for part in wo.get("parts_and_materials") or []:
        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
            exclude_stock_entry=exclude_stock_entry,
        )

        frappe.db.set_value(
            "Maintenance Work Order Part",
            part.name,
            {
                "gross_issued_quantity": balance["gross_issued"],
                "returned_quantity": balance["returned"],
                "issued_quantity": balance["net_issued"],
            },
            update_modified=False,
        )


def on_stock_entry_submit(doc, method=None):
    if doc.get("maintenance_work_order"):
        reconcile_maintenance_issues(doc.maintenance_work_order)

    if doc.purpose == "Material Issue" and doc.get("maintenance_picking"):
        picking = frappe.get_doc(
            "Maintenance Picking", doc.maintenance_picking
        )

        if picking.status != "Confirmed":
            frappe.throw(_("Picking is not Confirmed."))

        for row in doc.items:
            picking_item_name = row.get("maintenance_picking_item")

            if not picking_item_name:
                frappe.throw(_("Picking Item reference is required."))

            part = frappe.db.get_value(
                "Maintenance Picking Item",
                picking_item_name,
                ["parent", "stock_entry"],
                as_dict=True,
            )

            if not part or part.parent != picking.name:
                frappe.throw(_("Invalid Picking Item reference."))

            # Legacy reference: retain the FIRST issue.
            # Complete issuance history comes from Stock Entry Detail.
            if not part.stock_entry:
                frappe.db.set_value(
                    "Maintenance Picking Item",
                    picking_item_name,
                    "stock_entry",
                    doc.name,
                    update_modified=False,
                )

        mr = frappe.get_doc(
            "Material Request", picking.material_request
        )

        remaining = 0.0
        total_issued = 0.0

        for row in mr.items:
            balance = get_material_balance(
                picking.maintenance_work_order,
                row.maintenance_work_order_part,
                row.item_code,
                row.warehouse,
            )

            outstanding = max(
                flt(row.qty, 3)
                - flt(balance["gross_issued"], 3),
                0,
            )

            remaining += outstanding
            total_issued += flt(balance["gross_issued"], 3)

        # Clear the current issuance cycle. The Picking remains
        # the persistent fulfilment document for future issues.
        for row in picking.items:
            frappe.db.set_value(
                "Maintenance Picking Item",
                row.name,
                {
                    "picked_qty": 0,
                    "available_qty": 0,
                },
                update_modified=False,
            )

        new_status = (
            "Issued" if remaining <= 0.0005
            else "Partially Issued" if total_issued > 0.0005
            else "Draft"
        )

        frappe.db.set_value(
            "Maintenance Picking",
            picking.name,
            "status",
            new_status,
            update_modified=False,
        )


def before_stock_entry_cancel(doc, method=None):
    """Prevent cancellation that leaves consumed materials unsupported."""

    if (
        not doc.get("maintenance_work_order")
        or doc.purpose != "Material Issue"
    ):
        return

    from metagest_maintenance.maintenance_stock_balance import (
        get_material_balance,
    )

    # Use the same Work Order lock as maintenance returns.
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (doc.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order does not exist."))

    # A submitted material return depends on its original issue.
    dependent_returns = frappe.db.sql(
        """
        SELECT DISTINCT se.name
        FROM `tabStock Entry Detail` d
        INNER JOIN `tabStock Entry` se
            ON se.name = d.parent
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Receipt'
          AND d.against_stock_entry = %s
        """,
        (doc.name,),
    )

    if dependent_returns:
        names = ", ".join(row[0] for row in dependent_returns)
        frappe.throw(
            _(
                "Cannot cancel material issue {0}. "
                "Submitted material returns depend on it: {1}. "
                "Cancel those returns first."
            ).format(doc.name, names)
        )

    wo = frappe.get_doc(
        "Maintenance Work Order",
        doc.maintenance_work_order,
    )

    for part in wo.get("parts_and_materials") or []:
        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
            exclude_stock_entry=doc.name,
        )

        consumed = flt(part.consumed_quantity)
        remaining = flt(balance["net_issued"])

        if remaining + 0.0005 < consumed:
            frappe.throw(
                _(
                    "Cannot cancel material issue {0}. "
                    "Item {1} has {2} consumed, "
                    "but only {3} would remain issued."
                ).format(
                    doc.name,
                    part.item_code,
                    consumed,
                    remaining,
                )
            )


def on_stock_entry_cancel(doc, method=None):
    if doc.get("maintenance_work_order"):
        reconcile_maintenance_issues(
            doc.maintenance_work_order,
            exclude_stock_entry=doc.name,
        )

    if doc.purpose == "Material Issue" and doc.get("maintenance_picking"):
        picking = frappe.get_doc(
            "Maintenance Picking", doc.maintenance_picking
        )

        mr = frappe.get_doc(
            "Material Request", picking.material_request
        )

        for row in doc.items:
            if not row.get("maintenance_picking_item"):
                continue

            current = frappe.db.get_value(
                "Maintenance Picking Item",
                row.maintenance_picking_item,
                "stock_entry",
            )

            if current == doc.name:
                frappe.db.set_value(
                    "Maintenance Picking Item",
                    row.maintenance_picking_item,
                    "stock_entry",
                    None,
                    update_modified=False,
                )

        remaining = 0.0
        total_issued = 0.0

        for row in mr.items:
            balance = get_material_balance(
                picking.maintenance_work_order,
                row.maintenance_work_order_part,
                row.item_code,
                row.warehouse,
                exclude_stock_entry=doc.name,
            )

            remaining += max(
                flt(row.qty, 3)
                - flt(balance["gross_issued"], 3),
                0,
            )
            total_issued += flt(balance["gross_issued"], 3)

        frappe.db.set_value(
            "Maintenance Picking",
            picking.name,
            "status",
            (
                "Issued" if remaining <= 0.0005
                else "Partially Issued" if total_issued > 0.0005
                else "Draft"
            ),
            update_modified=False,
        )


# METAGEST MAINTENANCE RETURN VALIDATION

def validate_maintenance_return_draft(doc):
    wo = frappe.get_doc(
        "Maintenance Work Order",
        doc.maintenance_work_order
    )

    if doc.company != wo.company:
        frappe.throw(_("Return company differs from Work Order company."))

    if len(doc.items) != 1:
        frappe.throw(_("Maintenance return must contain one material line."))

    row = doc.items[0]

    if row.s_warehouse or not row.t_warehouse:
        frappe.throw(_("Material return must use a receiving warehouse only."))

    if not row.maintenance_work_order_part:
        frappe.throw(_("Maintenance part reference is required."))

    part = frappe.db.get_value(
        "Maintenance Work Order Part",
        row.maintenance_work_order_part,
        ["parent", "item_code", "warehouse", "issued_quantity", "consumed_quantity"],
        as_dict=True,
    )

    if not part or part.parent != wo.name:
        frappe.throw(_("Invalid maintenance material reference."))

    if row.item_code != part.item_code or row.t_warehouse != part.warehouse:
        frappe.throw(_("Return item or warehouse does not match the Work Order."))

    if not row.against_stock_entry or not row.ste_detail:
        frappe.throw(_("Original Stock Entry reference is required."))

    original = frappe.db.get_value(
        "Stock Entry Detail",
        row.ste_detail,
        [
            "parent", "item_code", "s_warehouse",
            "maintenance_work_order_part", "transfer_qty"
        ],
        as_dict=True,
    )

    if (
        not original
        or original.parent != row.against_stock_entry
        or original.item_code != row.item_code
        or original.s_warehouse != row.t_warehouse
        or original.maintenance_work_order_part != row.maintenance_work_order_part
    ):
        frappe.throw(_("Return does not match the original warehouse issue."))

    issue = frappe.db.get_value(
        "Stock Entry",
        row.against_stock_entry,
        ["docstatus", "purpose", "company", "maintenance_work_order"],
        as_dict=True,
    )

    if (
        not issue
        or issue.docstatus != 1
        or issue.purpose != "Material Issue"
        or issue.company != wo.company
        or issue.maintenance_work_order != wo.name
    ):
        frappe.throw(_("The original maintenance issue is not valid."))

    unused = flt(part.issued_quantity) - flt(part.consumed_quantity)

    if flt(row.qty) <= 0 or flt(row.transfer_qty or row.qty) > unused + 0.0005:
        frappe.throw(_("Return quantity exceeds unused materials."))


def block_maintenance_return_submission(doc, method=None):
    """Enforce return limits immediately before submission."""

    if (
        not doc.get("maintenance_work_order")
        or doc.purpose != "Material Receipt"
    ):
        return

    # Serialize returns against the same Maintenance Work Order.
    # The database releases this lock on commit or rollback.
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (doc.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order does not exist."))

    # Revalidate after acquiring the lock, using current DB values.
    validate_maintenance_return_draft(doc)

    from metagest_maintenance.maintenance_stock_balance import (
        get_material_balance,
    )

    wo = frappe.get_doc(
        "Maintenance Work Order",
        doc.maintenance_work_order,
    )

    for row in doc.items:
        part = next(
            (
                p for p in wo.parts_and_materials
                if p.name == row.maintenance_work_order_part
            ),
            None,
        )

        if not part:
            frappe.throw(_("Invalid maintenance material reference."))

        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
        )

        returning = flt(row.transfer_qty or row.qty, 3)
        unconsumed = flt(
            balance["net_issued"] - flt(part.consumed_quantity),
            3,
        )

        if returning <= 0 or returning > unconsumed + 0.0005:
            frappe.throw(
                _("Return quantity exceeds the unconsumed material balance.")
            )

        # Prevent returns exceeding the referenced original issue.
        previous_returns = frappe.db.sql(
            """
            SELECT COALESCE(SUM(d.transfer_qty), 0)
            FROM `tabStock Entry Detail` d
            INNER JOIN `tabStock Entry` se
                ON se.name = d.parent
            WHERE se.docstatus = 1
              AND se.purpose = 'Material Receipt'
              AND d.against_stock_entry = %(issue)s
              AND d.ste_detail = %(detail)s
              AND se.name != %(current)s
            """,
            {
                "issue": row.against_stock_entry,
                "detail": row.ste_detail,
                "current": doc.name,
            },
        )[0][0]

        original_qty = frappe.db.get_value(
            "Stock Entry Detail",
            row.ste_detail,
            "transfer_qty",
        )

        if (
            flt(previous_returns)
            + returning
            > flt(original_qty) + 0.0005
        ):
            frappe.throw(
                _("Cumulative returns exceed the original warehouse issue.")
            )
