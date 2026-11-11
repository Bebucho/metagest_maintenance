import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_service_receipts import (
    get_external_service_receipt_preview,
)
from metagest_maintenance.maintenance_procurement_commitments import (
    get_procurement_commitments,
)

WAREHOUSE_ROLES = {"Stock User", "Stock Manager"}


def _check_access(require_warehouse=False):
    if not frappe.has_permission(
        "Maintenance Work Order", "read"
    ):
        frappe.throw(
            _("Maintenance Work Order permission required."),
            frappe.PermissionError,
        )

    if require_warehouse and not WAREHOUSE_ROLES.intersection(
        frappe.get_roles()
    ):
        frappe.throw(
            _("Warehouse role required for procurement indicators."),
            frappe.PermissionError,
        )


def _visible_work_orders():
    """Use Frappe permission-aware queries, with pagination."""
    start = 0

    while True:
        batch = frappe.get_list(
            "Maintenance Work Order",
            fields=["name"],
            order_by="name asc",
            limit_start=start,
            limit_page_length=200,
        )

        if not batch:
            break

        for row in batch:
            yield row.name

        start += len(batch)

        if len(batch) < 200:
            break


def _service_exceptions(work_order):
    """Return unfinished service rows using existing reconciliation."""

    preview = get_external_service_receipt_preview(work_order)

    pending_receipt = []
    incomplete = []

    for row in preview["services"]:
        status = row["process_status"]
        acceptance = row["acceptance_status"]

        # An accepted service with outstanding receiving.
        if acceptance == "Accepted" and status != "Received":
            pending_receipt.append(row["service_row"])

        # Any service not yet operationally and financially complete.
        if acceptance != "Accepted" or status != "Received":
            incomplete.append(row["service_row"])

    return pending_receipt, incomplete


def _material_exceptions(work_order):
    """Count distinct maintenance parts awaiting purchase action."""

    data = get_procurement_commitments(work_order)
    pending_parts = set()

    for row in data["items"]:
        if (
            flt(row["draft_request"], 3) > 0
            or flt(row["unordered_balance"], 3) > 0
        ):
            pending_parts.add(row["maintenance_part"])

    return pending_parts


@frappe.whitelist()
def accepted_services_awaiting_receipt():
    _check_access()
    total = 0

    for name in _visible_work_orders():
        pending, _ = _service_exceptions(name)
        total += len(pending)

    return {
        "value": total,
        "fieldtype": "Int",
        "route": ["List", "Maintenance Work Order"],
    }


@frappe.whitelist()
def materials_pending_purchase():
    _check_access(require_warehouse=True)
    parts = set()

    for name in _visible_work_orders():
        for part in _material_exceptions(name):
            parts.add((name, part))

    return {
        "value": len(parts),
        "fieldtype": "Int",
        "route": ["List", "Maintenance Work Order"],
    }


@frappe.whitelist()
def work_orders_with_incomplete_processes():
    _check_access(require_warehouse=True)
    incomplete_work_orders = set()

    for name in _visible_work_orders():
        _, incomplete_services = _service_exceptions(name)
        pending_parts = _material_exceptions(name)

        if incomplete_services or pending_parts:
            incomplete_work_orders.add(name)

    return {
        "value": len(incomplete_work_orders),
        "fieldtype": "Int",
        "route": ["List", "Maintenance Work Order"],
    }
