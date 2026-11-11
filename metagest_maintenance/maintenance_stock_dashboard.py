import frappe
from frappe import _
from frappe.utils import flt


def _require_read(doctype):
    if not frappe.has_permission(doctype, ptype="read"):
        frappe.throw(
            _("Read permission required for {0}").format(doctype),
            frappe.PermissionError,
        )


def _documents(doctype, filters):
    start = 0

    while True:
        rows = frappe.get_list(
            doctype,
            filters=filters,
            fields=["name"],
            order_by="name asc",
            limit_start=start,
            limit_page_length=200,
        )

        for row in rows:
            yield row.name

        if len(rows) < 200:
            break

        start += len(rows)


def _result(value):
    return {
        "value": int(value),
        "fieldtype": "Int",
    }


@frappe.whitelist()
def pending_pickings():
    _require_read("Maintenance Picking")

    statuses = [
        "Draft",
        "Prepared",
        "Confirmed",
        "Partially Issued",
    ]

    count = sum(
        1 for _ in _documents(
            "Maintenance Picking",
            {"status": ["in", statuses]},
        )
    )

    return _result(count)


@frappe.whitelist()
def stock_discrepancies():
    _require_read("Maintenance Stock Verification")

    count = sum(
        1 for _ in _documents(
            "Maintenance Stock Verification",
            {"status": "Discrepancy"},
        )
    )

    return _result(count)


def _maintenance_purchase_order_lines():
    """
    Yield outstanding maintenance-linked Purchase Order items.

    Purchase Order Item.material_request supplies the link.
    Purchase Order Item.received_qty reflects submitted receipts.
    """

    _require_read("Purchase Order")
    _require_read("Material Request")

    item_meta = frappe.get_meta("Purchase Order Item")

    for field in ("material_request", "qty", "received_qty"):
        if not item_meta.has_field(field):
            frappe.throw(
                _("Required Purchase Order Item field missing: {0}")
                .format(field)
            )

    mr_meta = frappe.get_meta("Material Request")

    for field in (
        "maintenance_work_order",
        "custom_maintenance_picking",
        "custom_maintenance_procurement_type",
    ):
        if not mr_meta.has_field(field):
            frappe.throw(
                _("Required Material Request field missing: {0}")
                .format(field)
            )

    mr_cache = {}

    for name in _documents(
        "Purchase Order",
        {"docstatus": 1},
    ):
        po = frappe.get_doc("Purchase Order", name)

        if po.status in (
            "Closed", "Cancelled", "Completed",
        ):
            continue

        for line in po.items:
            mr_name = line.get("material_request")

            if not mr_name:
                continue

            if mr_name not in mr_cache:
                if not frappe.has_permission(
                    "Material Request",
                    ptype="read",
                    doc=mr_name,
                ):
                    # No disclosure of inaccessible procurement.
                    mr_cache[mr_name] = False
                else:
                    mr = frappe.db.get_value(
                        "Material Request",
                        mr_name,
                        [
                            "maintenance_work_order",
                            "custom_maintenance_picking",
                            "custom_maintenance_procurement_type",
                        ],
                        as_dict=True,
                    )

                    mr_cache[mr_name] = bool(
                        mr and (
                            mr.maintenance_work_order
                            or mr.custom_maintenance_picking
                            or mr.custom_maintenance_procurement_type
                                in ("Materials", "External Service")
                        )
                    )

            if not mr_cache[mr_name]:
                continue

            outstanding = (
                flt(line.qty, 6)
                - flt(line.received_qty, 6)
            )

            if outstanding > 0.000001:
                yield name, line.name, outstanding


@frappe.whitelist()
def pending_maintenance_purchase_orders():
    names = {
        po
        for po, line, quantity
        in _maintenance_purchase_order_lines()
    }

    return _result(len(names))


@frappe.whitelist()
def pending_maintenance_receipt_lines():
    lines = {
        (po, line)
        for po, line, quantity
        in _maintenance_purchase_order_lines()
    }

    return _result(len(lines))
