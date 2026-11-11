import frappe
from frappe import _
from frappe.utils import today


@frappe.whitelist()
def overdue_work_orders():
    """Count overdue maintenance Work Orders visible to this user."""

    if not frappe.has_permission(
        "Maintenance Work Order", "read"
    ):
        frappe.throw(
            _("Maintenance Work Order read permission required."),
            frappe.PermissionError,
        )

    filters = [
        ["Maintenance Work Order", "date_planned", "<", today()],
        ["Maintenance Work Order", "date_planned", "is", "set"],
        [
            "Maintenance Work Order",
            "status",
            "not in",
            ["Completed", "Closed", "Cancelled"],
        ],
    ]

    results = frappe.get_list(
        "Maintenance Work Order",
        filters=filters,
        fields=["count(name) as total"],
        order_by=None,
    )

    total = results[0].total if results else 0

    return {
        "value": int(total or 0),
        "fieldtype": "Int",
        "route": ["List", "Maintenance Work Order"],
        "route_options": {
            "date_planned": ["<", today()],
            "status": [
                "not in",
                ["Completed", "Closed", "Cancelled"],
            ],
        },
    }
