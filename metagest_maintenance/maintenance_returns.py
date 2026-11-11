import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def request_material_return(work_order):
    if not work_order:
        frappe.throw(_("Maintenance Work Order is required."))

    frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (work_order,),
    )

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("write")
    frappe.has_permission("Stock Entry", "create", throw=True)

    if wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Cannot return materials from a closed Work Order."))

    # First implementation: one material line per return.
    parts = [
        p for p in wo.parts_and_materials
        if flt(p.issued_quantity) > flt(p.consumed_quantity)
    ]

    if len(parts) != 1:
        frappe.throw(
            _("This version requires exactly one material line with an unused balance.")
        )

    part = parts[0]

    # Reopen an existing draft instead of creating duplicates.
    existing = frappe.get_all(
        "Stock Entry",
        filters={
            "maintenance_work_order": wo.name,
            "purpose": "Material Receipt",
            "docstatus": 0,
        },
        fields=["name"],
        limit_page_length=1,
    )

    if existing:
        return {"name": existing[0].name, "created": False}

    issues = frappe.db.sql(
        """
        SELECT
            se.name AS issue,
            sed.name AS issue_detail,
            sed.transfer_qty AS qty,
            sed.basic_rate,
            sed.expense_account,
            sed.cost_center,
            sed.uom,
            sed.stock_uom,
            sed.conversion_factor
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.maintenance_work_order = %(wo)s
          AND sed.maintenance_work_order_part = %(part)s
          AND sed.item_code = %(item)s
          AND sed.s_warehouse = %(warehouse)s
        ORDER BY se.creation DESC
        """,
        {
            "wo": wo.name,
            "part": part.name,
            "item": part.item_code,
            "warehouse": part.warehouse,
        },
        as_dict=True,
    )

    if len(issues) != 1:
        frappe.throw(
            _("This first return workflow requires exactly one submitted issue for the material.")
        )

    issue = issues[0]

    unused = flt(
        flt(part.issued_quantity) - flt(part.consumed_quantity),
        3,
    )

    if unused <= 0 or unused > flt(issue.qty):
        frappe.throw(_("Invalid quantity available for return."))

    se = frappe.new_doc("Stock Entry")
    se.company = wo.company
    se.stock_entry_type = "Material Receipt"
    se.purpose = "Material Receipt"
    se.posting_date = nowdate()
    se.maintenance_work_order = wo.name

    se.append("items", {
        "item_code": part.item_code,
        "qty": unused,
        "t_warehouse": part.warehouse,
        "uom": issue.uom,
        "stock_uom": issue.stock_uom,
        "conversion_factor": flt(issue.conversion_factor) or 1,
        "basic_rate": flt(issue.basic_rate),
        "set_basic_rate_manually": 1,
        "expense_account": issue.expense_account,
        "cost_center": issue.cost_center,
        "maintenance_work_order_part": part.name,
        "against_stock_entry": issue.issue,
        "ste_detail": issue.issue_detail,
    })

    se.insert()

    return {
        "name": se.name,
        "created": True,
        "quantity": unused,
        "original_issue": issue.issue,
    }
