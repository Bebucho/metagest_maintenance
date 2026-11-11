import frappe
from frappe import _
from frappe.utils import flt, nowdate

from metagest_maintenance.maintenance_service_receipts import (
    _get_external_service_receipt_preview,
)


def sync_external_service_costs(work_order):
    """Rebuild analytical costs from submitted receipts only.

    Does not create Stock Entries, Journal Entries or GL Entries.
    Called internally from Purchase Receipt document events.
    """

    frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (work_order,),
    )

    wo = frappe.get_doc("Maintenance Work Order", work_order)
    preview = _get_external_service_receipt_preview(work_order)

    rows = {
        row.name: row
        for row in wo.get("custom_external_services") or []
    }

    total = 0.0

    for result in preview["services"]:
        row = rows.get(result["service_row"])
        if not row:
            frappe.throw(_("External service row missing."))

        row.received_quantity = flt(
            result["received_quantity"], 3
        )
        row.actual_cost = flt(result["actual_cost"], 2)

        status = result["process_status"]

        if status == "Received":
            row.procurement_status = "Received"
            row.financial_status = "Final"
        elif status == "Partially Received":
            row.procurement_status = "Partially Received"
            row.financial_status = "Partially Received"
        else:
            row.financial_status = "Not Received"
            if status == "Not Requested":
                row.procurement_status = "Not Requested"
            elif status == "Awaiting Purchase Order":
                row.procurement_status = "Requested"
            else:
                row.procurement_status = "Ordered"

        total += row.actual_cost

    wo.external_services_total = flt(total, 2)

    # The existing Work Order controller recomputes total cost.
    # This is an internal derived-value update, not a user edit.
    wo.flags.maintenance_service_system_update = True
    wo.save(ignore_permissions=True)

    record_name = wo.maintenance_cost_record

    if record_name:
        if not frappe.db.exists(
            "Maintenance Cost Record", record_name
        ):
            frappe.throw(
                _("Linked Maintenance Cost Record is missing.")
            )
        record = frappe.get_doc(
            "Maintenance Cost Record", record_name
        )
        if record.asset and record.asset != wo.asset:
            frappe.throw(
                _("Maintenance Cost Record asset mismatch.")
            )
    elif total > 0:
        if not wo.asset:
            frappe.throw(
                _("An Asset is required for definitive cost reporting.")
            )
        record = frappe.new_doc("Maintenance Cost Record")
    else:
        record = None

    if record:
        record.asset = wo.asset
        record.maintenance_type = wo.maintenance_type
        record.asset_repair = wo.asset_repair
        record.maintenance_log = wo.maintenance_log
        record.maintenance_date = (
            wo.completion_date
            or wo.start_date
            or wo.date_planned
            or nowdate()
        )
        record.parts_cost = flt(wo.parts_total_cost, 2)
        record.labour_cost = flt(wo.labour_total_cost, 2)
        record.external_services_cost = flt(total, 2)
        record.total_cost = flt(
            record.parts_cost
            + record.labour_cost
            + record.external_services_cost,
            2,
        )

        if record.is_new():
            record.insert(ignore_permissions=True)
            wo.db_set(
                "maintenance_cost_record",
                record.name,
                update_modified=False,
            )
        else:
            record.save(ignore_permissions=True)

    return {
        "work_order": wo.name,
        "external_services_actual_total": flt(total, 2),
        "maintenance_cost_record": (
            record.name if record else None
        ),
    }


def on_purchase_receipt_change(doc, method=None):
    """Synchronise only maintenance services linked through PO items."""

    work_orders = frappe.db.sql(
        """
        SELECT DISTINCT mr.maintenance_work_order
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Order Item` poi
            ON poi.name = pri.purchase_order_item
           AND poi.parent = pri.purchase_order
        INNER JOIN `tabMaterial Request Item` mri
            ON mri.name = poi.material_request_item
           AND mri.parent = poi.material_request
        INNER JOIN `tabMaterial Request` mr
            ON mr.name = mri.parent
        WHERE pri.parent = %s
          AND mr.company = %s
          AND mr.material_request_type = 'Purchase'
          AND mr.custom_maintenance_procurement_type = 'External Service'
          AND mri.custom_maintenance_external_service IS NOT NULL
          AND mri.custom_maintenance_external_service != ''
        """,
        (doc.name, doc.company),
        as_dict=True,
    )

    for row in work_orders:
        if row.maintenance_work_order:
            sync_external_service_costs(
                row.maintenance_work_order
            )
