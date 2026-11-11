import frappe
from frappe import _
from frappe.utils import today


@frappe.whitelist(methods=["POST"])
def accept_external_service(work_order, service_row, notes):
    """Record operational acceptance, without recognising actual cost."""

    if not work_order or not service_row:
        frappe.throw(_("Work Order and service row are required."))

    if not (notes or "").strip():
        frappe.throw(_("Acceptance observations are required."))

    # Serialize changes against the same Work Order.
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order not found."))

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("write")

    roles = set(frappe.get_roles(frappe.session.user))

    if (
        frappe.session.user != wo.supervisor
        and "System Manager" not in roles
    ):
        frappe.throw(
            _("Only the assigned supervisor may accept this service."),
            frappe.PermissionError,
        )

    if wo.docstatus == 2 or wo.status not in (
        "Approved", "In Progress", "Completed"
    ):
        frappe.throw(_("Work Order is not open for acceptance."))

    matches = [
        row
        for row in wo.get("custom_external_services") or []
        if row.name == service_row
    ]

    if len(matches) != 1:
        frappe.throw(_("External Service row not found."))

    row = matches[0]

    if row.acceptance_status == "Accepted":
        frappe.throw(_("This service has already been accepted."))

    if not row.material_request:
        frappe.throw(_("Service procurement has not been requested."))

    mr = frappe.get_doc("Material Request", row.material_request)

    if (
        mr.docstatus != 1
        or mr.company != wo.company
        or mr.maintenance_work_order != wo.name
        or mr.material_request_type != "Purchase"
        or mr.custom_maintenance_procurement_type != "External Service"
    ):
        frappe.throw(_("A submitted service Purchase Request is required."))

    linked_lines = [
        line for line in mr.items
        if line.custom_maintenance_external_service == row.name
        and line.item_code == row.service_item
    ]

    if len(linked_lines) != 1:
        frappe.throw(_("Purchase Request service reference mismatch."))

    # Find submitted standard Purchase Orders linked to this service.
    orders = frappe.db.sql(
        """
        SELECT DISTINCT po.name
        FROM `tabPurchase Order` po
        INNER JOIN `tabPurchase Order Item` poi
            ON poi.parent = po.name
        WHERE po.docstatus = 1
          AND po.company = %s
          AND poi.material_request = %s
          AND poi.material_request_item = %s
          AND poi.item_code = %s
        """,
        (
            wo.company,
            mr.name,
            linked_lines[0].name,
            row.service_item,
        ),
        as_dict=True,
    )

    if not orders:
        frappe.throw(
            _("A submitted Purchase Order is required before acceptance.")
        )

    if len(orders) > 1:
        frappe.throw(
            _("Multiple Purchase Orders exist for this service. "
              "Review the procurement before acceptance.")
        )

    row.purchase_order = orders[0].name
    row.acceptance_status = "Accepted"
    row.accepted_on = today()
    row.custom_accepted_by = frappe.session.user
    row.custom_acceptance_notes = notes.strip()
    row.procurement_status = "Ordered"

    # No change to external_services_total or Maintenance Cost Record.
    wo.flags.maintenance_service_system_update = True
    wo.save()

    return {
        "work_order": wo.name,
        "service_row": row.name,
        "purchase_order": row.purchase_order,
        "acceptance_status": "Accepted",
        "accepted_on": row.accepted_on,
        "accepted_by": row.custom_accepted_by,
        "financial_status": row.financial_status or "Not Received",
    }
