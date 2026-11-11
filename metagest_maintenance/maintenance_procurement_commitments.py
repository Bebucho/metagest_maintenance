import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_procurement_commitments(work_order):
    """Read outstanding procurement commitments by maintenance part."""

    if not work_order:
        frappe.throw(_("Maintenance Work Order is required."))

    if not {"Stock User", "Stock Manager"}.intersection(
        frappe.get_roles(frappe.session.user)
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("read")

    if not wo.company:
        frappe.throw(_("Company is required."))

    requests = frappe.db.sql("""
        SELECT
            mr.name AS material_request,
            mr.docstatus,
            mri.name AS request_item,
            mri.maintenance_work_order_part AS part,
            mri.item_code,
            mri.warehouse,
            mri.qty,
            mri.received_qty
        FROM `tabMaterial Request` mr
        INNER JOIN `tabMaterial Request Item` mri
            ON mri.parent = mr.name
        WHERE mr.maintenance_work_order = %s
          AND mr.company = %s
          AND mr.material_request_type = 'Purchase'
          AND COALESCE(
              mr.custom_maintenance_procurement_type, ''
          ) IN ('', 'Materials')
          AND mr.docstatus IN (0, 1)
    """, (wo.name, wo.company), as_dict=True)

    orders = frappe.db.sql("""
        SELECT
            poi.material_request_item AS request_item,
            poi.parent AS purchase_order,
            poi.qty,
            poi.received_qty
        FROM `tabPurchase Order Item` poi
        INNER JOIN `tabPurchase Order` po
            ON po.name = poi.parent
        INNER JOIN `tabMaterial Request` mr
            ON mr.name = poi.material_request
        WHERE mr.maintenance_work_order = %s
          AND mr.company = %s
          AND mr.material_request_type = 'Purchase'
          AND COALESCE(
              mr.custom_maintenance_procurement_type, ''
          ) IN ('', 'Materials')
          AND mr.docstatus = 1
          AND po.docstatus = 1
          AND po.company = %s
          AND poi.material_request_item IS NOT NULL
          AND poi.material_request_item != ''
    """, (wo.name, wo.company, wo.company), as_dict=True)

    orders_by_item = {}

    for order in orders:
        orders_by_item.setdefault(
            order.request_item, []
        ).append(order)

    valid_parts = {
        row.name: row
        for row in wo.get("parts_and_materials") or []
    }

    results = {}

    for row in requests:
        part = valid_parts.get(row.part)

        if (
            not part
            or part.item_code != row.item_code
            or part.warehouse != row.warehouse
        ):
            frappe.throw(
                _("Invalid procurement reference on {0}.").format(
                    row.material_request
                )
            )

        key = row.part

        if key not in results:
            results[key] = {
                "maintenance_part": key,
                "item_code": row.item_code,
                "warehouse": row.warehouse,
                "draft_request": 0.0,
                "submitted_remaining": 0.0,
                "open_purchase_orders": 0.0,
                "unordered_balance": 0.0,
                "documents": [],
            }

        result = results[key]
        quantity = flt(row.qty, 3)

        if row.docstatus == 0:
            result["draft_request"] += quantity
            continue

        remaining = max(
            quantity - flt(row.received_qty, 3), 0
        )

        open_po = sum(
            max(
                flt(po.qty, 3) - flt(po.received_qty, 3),
                0,
            )
            for po in orders_by_item.get(row.request_item, [])
        )

        if open_po > remaining + 0.0005:
            frappe.throw(
                _("Purchase Order commitments exceed the "
                  "outstanding request quantity for {0}.").format(
                    row.request_item
                )
            )

        unordered = max(remaining - open_po, 0)

        result["submitted_remaining"] += remaining
        result["open_purchase_orders"] += open_po
        result["unordered_balance"] += unordered

        result["documents"].append({
            "material_request": row.material_request,
            "request_item": row.request_item,
            "remaining": flt(remaining, 3),
            "open_po": flt(open_po, 3),
            "unordered": flt(unordered, 3),
        })

    for result in results.values():
        for field in (
            "draft_request",
            "submitted_remaining",
            "open_purchase_orders",
            "unordered_balance",
        ):
            result[field] = flt(result[field], 3)

    return {
        "work_order": wo.name,
        "items": list(results.values()),
    }
