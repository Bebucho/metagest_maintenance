import frappe
from frappe import _

from metagest_maintenance.maintenance_service_receipts import (
    _get_external_service_receipt_preview,
)


def validate_external_service_closure(wo):
    """Block closure when an external service process is incomplete."""

    services = [
        row
        for row in wo.get("custom_external_services") or []
        if row.service_item
    ]

    if not services:
        return

    preview = _get_external_service_receipt_preview(wo.name)

    statuses = {
        result["service_row"]: result
        for result in preview["services"]
    }

    for row in services:
        result = statuses.get(row.name)

        if not result:
            frappe.throw(
                _("External service {0} is missing from "
                  "procurement reconciliation.").format(row.idx)
            )

        if not row.material_request:
            frappe.throw(
                _("External service {0} has no Purchase Request.")
                .format(row.idx)
            )

        if row.acceptance_status != "Accepted":
            frappe.throw(
                _("External service {0} has not been accepted.")
                .format(row.idx)
            )

        if result["process_status"] != "Received":
            frappe.throw(
                _("External service {0} is financially incomplete: "
                  "{1}.").format(
                    row.idx,
                    result["process_status"],
                )
            )
