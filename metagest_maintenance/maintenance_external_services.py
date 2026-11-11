import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


@frappe.whitelist(methods=["POST"])
def request_external_service(work_order, service_row):
    """Create or retrieve procurement for one Work Order service."""

    if not work_order or not service_row:
        frappe.throw(_("Work Order and service row are required."))

    frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (work_order,),
    )

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("write")

    if wo.docstatus == 2 or wo.status not in (
        "Approved", "In Progress"
    ):
        frappe.throw(
            _("Work Order must be Approved or In Progress.")
        )

    if not wo.company:
        frappe.throw(_("Work Order company is required."))

    frappe.has_permission(
        "Material Request", "create", throw=True
    )

    rows = [
        row for row in wo.get("custom_external_services") or []
        if row.name == service_row
    ]

    if len(rows) != 1:
        frappe.throw(
            _("External service row does not belong to this Work Order.")
        )

    row = rows[0]

    if not row.service_item:
        frappe.throw(_("Service Item is required."))

    if not (row.description or "").strip():
        frappe.throw(_("Scope of Service is required."))

    item = frappe.get_doc("Item", row.service_item)

    if (
        item.disabled
        or item.is_stock_item
        or not item.is_purchase_item
    ):
        frappe.throw(
            _("Only active purchasable non-stock services are allowed.")
        )

    quantity = flt(row.quantity, 3)
    rate = flt(row.estimated_rate, 2)

    if quantity <= 0:
        frappe.throw(_("Service quantity must be positive."))

    if rate <= 0:
        frappe.throw(_("Estimated unit rate must be positive."))

    # An already linked request must not be duplicated.
    if row.material_request:
        mr = frappe.get_doc("Material Request", row.material_request)

        if (
            mr.maintenance_work_order != wo.name
            or mr.company != wo.company
            or mr.material_request_type != "Purchase"
            or mr.custom_maintenance_procurement_type
                != "External Service"
        ):
            frappe.throw(
                _("Linked Purchase Request is inconsistent.")
            )

        return {
            "name": mr.name,
            "created": False,
            "already_exists": True,
        }

    # Recover an existing request linked through its line.
    matched_items = frappe.get_all(
        "Material Request Item",
        filters={
            "custom_maintenance_external_service": row.name,
        },
        fields=["parent"],
        limit_page_length=20,
    )

    for matched in matched_items:
        existing_mr = frappe.get_doc(
            "Material Request", matched.parent
        )

        if existing_mr.docstatus != 2:
            if (
                existing_mr.maintenance_work_order != wo.name
                or existing_mr.company != wo.company
                or existing_mr.custom_maintenance_procurement_type
                    != "External Service"
            ):
                frappe.throw(
                    _("Existing procurement reference is inconsistent.")
                )

            row.material_request = existing_mr.name
            row.procurement_status = "Requested"
            wo.flags.maintenance_service_system_update = True
            wo.save()

            return {
                "name": existing_mr.name,
                "created": False,
                "already_exists": True,
            }

    # Older requests created through the independent dialog
    # cannot be associated with a new row automatically.
    legacy_drafts = frappe.get_all(
        "Material Request",
        filters={
            "maintenance_work_order": wo.name,
            "company": wo.company,
            "material_request_type": "Purchase",
            "custom_maintenance_procurement_type": "External Service",
            "docstatus": 0,
        },
        fields=["name"],
    )

    for existing in legacy_drafts:
        linked_items = frappe.get_all(
            "Material Request Item",
            filters={
                "parent": existing.name,
                "custom_maintenance_external_service": ["is", "set"],
            },
            limit_page_length=1,
        )

        if not linked_items:
            frappe.throw(
                _("Unlinked legacy External Service request {0} "
                  "requires review before creating a new request.")
                .format(existing.name)
            )

    request_date = getdate(nowdate())

    if wo.date_planned and getdate(wo.date_planned) > request_date:
        request_date = getdate(wo.date_planned)

    mr = frappe.new_doc("Material Request")
    mr.company = wo.company
    mr.material_request_type = "Purchase"
    mr.schedule_date = request_date
    mr.maintenance_work_order = wo.name
    mr.custom_maintenance_procurement_type = "External Service"
    mr.custom_maintenance_picking = None

    mr.append("items", {
        "item_code": item.name,
        "item_name": item.item_name,
        "description": row.description,
        "qty": quantity,
        "uom": item.stock_uom,
        "stock_uom": item.stock_uom,
        "conversion_factor": 1,
        "schedule_date": request_date,
        "rate": rate,
        "custom_maintenance_external_service": row.name,
    })

    mr.insert()

    row.material_request = mr.name
    row.procurement_status = "Requested"
    row.estimated_cost = flt(quantity * rate, 2)

    # Save the operational reference; do not post or submit procurement.
    wo.flags.maintenance_service_system_update = True
    wo.save()

    return {
        "name": mr.name,
        "created": True,
        "service_row": row.name,
        "estimated_total": row.estimated_cost,
        "procurement_type": "External Service",
    }
