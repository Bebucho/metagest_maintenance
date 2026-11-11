import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


@frappe.whitelist()
def request_materials(work_order):
    if not work_order:
        frappe.throw(_("Maintenance Work Order is required."))

    # Serialise creation requests for this particular Work Order.
    frappe.db.sql(
        "SELECT name FROM `tabMaintenance Work Order` "
        "WHERE name = %s FOR UPDATE",
        (work_order,),
    )

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("write")
    frappe.has_permission("Material Request", "create", throw=True)

    if wo.docstatus == 2 or wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Cannot request materials for a closed or cancelled Work Order."))

    if not wo.company:
        frappe.throw(_("Set the company before requesting materials."))

    # One initial Material Request per Work Order.
    existing = frappe.get_all(
        "Material Request",
        filters={
            "maintenance_work_order": wo.name,
            "docstatus": ["!=", 2],
        },
        fields=["name"],
        limit_page_length=1,
    )

    if existing:
        return {
            "name": existing[0].name,
            "created": False,
        }

    request_date = getdate(nowdate())
    if wo.date_planned and getdate(wo.date_planned) > request_date:
        request_date = getdate(wo.date_planned)

    mr = frappe.new_doc("Material Request")
    mr.company = wo.company
    mr.material_request_type = "Material Issue"
    mr.schedule_date = request_date
    mr.maintenance_work_order = wo.name

    count = 0

    for row in wo.get("parts_and_materials") or []:
        qty = flt(row.required_quantity)

        if qty < 0:
            frappe.throw(_("Negative quantity on row {0}.").format(row.idx))

        if not qty:
            continue

        if not row.item_code or not row.warehouse:
            frappe.throw(
                _("Item and warehouse are required on row {0}.").format(row.idx)
            )

        warehouse = frappe.get_doc("Warehouse", row.warehouse)

        if (
            warehouse.company != wo.company
            or warehouse.is_group
            or warehouse.disabled
        ):
            frappe.throw(
                _("Invalid warehouse on maintenance row {0}.").format(row.idx)
            )

        item = frappe.get_doc("Item", row.item_code)

        if not item.is_stock_item or item.disabled:
            frappe.throw(
                _("Item {0} is not an active stock item.").format(row.item_code)
            )

        mr.append("items", {
            "item_code": row.item_code,
            "item_name": item.item_name,
            "qty": qty,
            "warehouse": row.warehouse,
            "schedule_date": request_date,
            "uom": item.stock_uom,
            "stock_uom": item.stock_uom,
            "conversion_factor": 1,
            "maintenance_work_order_part": row.name,
        })
        count += 1

    if not count:
        frappe.throw(_("No material requirements found in this Work Order."))

    # Temporary diagnostic: inspect warehouses before insertion.
    for row in mr.items:
        frappe.logger("maintenance").info(
            "MR DEBUG item=%s warehouse=%r from_warehouse=%r",
            row.item_code,
            row.warehouse,
            row.from_warehouse,
        )

    mr.insert()

    return {
        "name": mr.name,
        "created": True,
    }
