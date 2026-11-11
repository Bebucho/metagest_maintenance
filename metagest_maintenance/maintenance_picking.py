import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_stock_balance import (
    get_material_balance,
)


@frappe.whitelist()
def prepare_picking(material_request):

    if not material_request:
        frappe.throw(_("Material Request is required."))

    roles = set(frappe.get_roles(frappe.session.user))

    if not roles.intersection({"Stock User", "Stock Manager"}):
        frappe.throw(
            _("Only Warehouse personnel can prepare picking."),
            frappe.PermissionError,
        )

    frappe.has_permission(
        "Maintenance Picking", "create", throw=True
    )

    mr = frappe.get_doc("Material Request", material_request)
    mr.check_permission("read")

    if mr.docstatus != 1:
        frappe.throw(_("Material Request must be submitted."))

    if mr.material_request_type != "Material Issue":
        frappe.throw(_("Only Material Issue requests are supported."))

    if not mr.get("maintenance_work_order"):
        frappe.throw(_("Maintenance Work Order link is missing."))

    # Serialize picking creation for this Work Order.
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (mr.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order not found."))

    wo = frappe.get_doc(
        "Maintenance Work Order",
        mr.maintenance_work_order,
    )
    wo.check_permission("read")

    if wo.company != mr.company:
        frappe.throw(_("Company mismatch."))

    if wo.docstatus == 2 or wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Work Order is closed or cancelled."))

    # Calculate demand before looking for previous picking documents.
    from metagest_maintenance.maintenance_fulfilment import (
        get_fulfilment_preview,
    )

    preview = get_fulfilment_preview(mr.name)

    pending = [
        line for line in preview["items"]
        if flt(line["outstanding"]) > 0
    ]

    if not pending:
        return {
            "name": None,
            "created": False,
            "message": "No outstanding materials to pick.",
        }

    # Reopen only an unfinished picking.
    existing = frappe.get_all(
        "Maintenance Picking",
        filters={
            "material_request": mr.name,
            "status": ["in", ["Draft", "Prepared", "Confirmed", "Partially Issued"]],
        },
        fields=["name", "status"],
        order_by="creation desc",
        limit_page_length=1,
    )

    if existing:
        return {
            "name": existing[0].name,
            "created": False,
            "status": existing[0].status,
        }

    allocation = {
        line["material_request_item"]: line
        for line in preview["items"]
    }

    warehouses = {
        row.warehouse
        for row in mr.items
        if flt(row.qty) > 0
    }

    if len(warehouses) != 1:
        frappe.throw(
            _("This picking version supports one warehouse per request.")
        )

    warehouse = next(iter(warehouses))
    wh = frappe.get_doc("Warehouse", warehouse)

    if wh.company != wo.company or wh.is_group or wh.disabled:
        frappe.throw(_("Invalid warehouse."))

    picking = frappe.new_doc("Maintenance Picking")

    picking.company = wo.company
    picking.maintenance_work_order = wo.name
    picking.material_request = mr.name
    picking.warehouse = warehouse
    picking.assigned_to = frappe.session.user
    picking.status = "Draft"



    parts = {p.name: p for p in wo.parts_and_materials}

    for row in mr.items:

        if flt(row.qty) <= 0:
            continue

        part = parts.get(
            row.get("maintenance_work_order_part")
        )

        if not part:
            frappe.throw(_("Invalid Work Order material line."))

        if (
            part.item_code != row.item_code
            or part.warehouse != row.warehouse
        ):
            frappe.throw(_("Material Request line mismatch."))

        line = allocation.get(row.name)

        if not line:
            frappe.throw(_("Missing warehouse allocation."))

        outstanding = flt(line["outstanding"], 3)

        if outstanding <= 0:
            continue

        # Allocation already accounts for stock shared by request lines.
        available = flt(line["pickable"], 3)

        picking.append("items", {
            "item_code": row.item_code,
            "material_request_item": row.name,
            "maintenance_work_order_part": part.name,
            "required_qty": outstanding,
            "available_qty": available,
            "picked_qty": 0,
        })

    if not picking.items:
        return {
            "name": None,
            "created": False,
            "message": "No outstanding materials to pick.",
        }

    picking.insert()

    return {
        "name": picking.name,
        "created": True,
    }


def confirm_picking(picking_name):
    """Validate physical picking before warehouse issue."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    roles = set(frappe.get_roles(frappe.session.user))

    if not roles.intersection({"Stock User", "Stock Manager"}):
        frappe.throw(
            _("Only authorised Warehouse personnel may confirm picking."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("write")

    # Serialize warehouse operations against the same Work Order.
    frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (picking.maintenance_work_order,),
    )

    # Reload after acquiring the lock.
    picking.reload()

    if picking.status not in ("Draft", "Prepared", "Partially Issued"):
        frappe.throw(_("This picking cannot be confirmed again."))

    wo = frappe.get_doc(
        "Maintenance Work Order",
        picking.maintenance_work_order,
    )

    if wo.docstatus == 2 or wo.status in ("Closed", "Cancelled"):
        frappe.throw(_("Maintenance Work Order is closed."))

    mr = frappe.get_doc("Material Request", picking.material_request)

    if (
        mr.docstatus != 1
        or mr.material_request_type != "Material Issue"
        or mr.maintenance_work_order != wo.name
        or mr.company != wo.company
        or picking.company != wo.company
    ):
        frappe.throw(_("Invalid originating Material Request."))

    if not picking.items:
        frappe.throw(_("Picking has no material lines."))

    parts = {p.name: p for p in wo.parts_and_materials}
    request_rows = {r.name: r for r in mr.items}

    picked_by_item = {}
    available_by_item = {}

    total_picked = 0

    for row in picking.items:
        part = parts.get(row.maintenance_work_order_part)
        mr_row = request_rows.get(row.material_request_item)

        if not part or not mr_row:
            frappe.throw(_("Invalid picking source reference."))

        if (
            row.item_code != part.item_code
            or row.item_code != mr_row.item_code
            or part.warehouse != picking.warehouse
            or mr_row.warehouse != picking.warehouse
            or mr_row.maintenance_work_order_part != part.name
        ):
            frappe.throw(_("Picking material references do not match."))

        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
        )

        outstanding = max(
            flt(mr_row.qty) - flt(balance["gross_issued"]),
            0,
        )

        picked = flt(row.picked_qty, 3)

        if picked < 0 or picked > outstanding + 0.0005:
            frappe.throw(
                _("Picked quantity exceeds outstanding requirement for {0}.")
                .format(row.item_code)
            )

        total_picked += picked

        picked_by_item[row.item_code] = (
            picked_by_item.get(row.item_code, 0) + picked
        )

        if row.item_code not in available_by_item:
            bin_data = frappe.db.get_value(
                "Bin",
                {
                    "item_code": row.item_code,
                    "warehouse": picking.warehouse,
                },
                ["actual_qty", "reserved_qty"],
                as_dict=True,
            )

            actual = flt(bin_data.actual_qty) if bin_data else 0
            reserved = flt(bin_data.reserved_qty) if bin_data else 0

            available_by_item[row.item_code] = max(
                actual - reserved, 0
            )

    if total_picked <= 0:
        frappe.throw(_("Enter at least one picked quantity."))

    for item_code, picked in picked_by_item.items():
        if picked > available_by_item[item_code] + 0.0005:
            frappe.throw(
                _("Insufficient warehouse stock for {0}.").format(item_code)
            )

    picking.status = "Confirmed"
    picking.flags.maintenance_confirming = True
    picking.save()

    return {
        "name": picking.name,
        "status": picking.status,
        "total_picked": total_picked,
    }


def create_material_issue(picking_name):
    """Create one draft Material Issue from a confirmed picking."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    if not set(frappe.get_roles()).intersection(
        {"Stock User", "Stock Manager"}
    ):
        frappe.throw(
            _("Warehouse permission is required."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("read")
    frappe.has_permission("Stock Entry", "create", throw=True)

    # Serialize actions affecting the same Work Order.
    frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (picking.maintenance_work_order,),
    )

    picking.reload()

    # A draft Stock Entry blocks another issuance cycle.
    # Submitted and cancelled entries remain in issuance history
    # but must not prevent subsequent partial fulfilments.
    existing_draft = frappe.db.get_value(
        "Stock Entry",
        {
            "maintenance_picking": picking.name,
            "docstatus": 0,
        },
        "name",
    )

    if existing_draft:
        frappe.throw(
            _("Draft Stock Entry {0} already exists for this Picking. "
              "Resolve it before issuing again.").format(existing_draft)
        )

    if picking.status != "Confirmed":
        frappe.throw(_("Picking must be confirmed before issuing."))

    wo = frappe.get_doc(
        "Maintenance Work Order",
        picking.maintenance_work_order,
    )

    mr = frappe.get_doc(
        "Material Request",
        picking.material_request,
    )

    if wo.status in ("Closed", "Cancelled") or wo.docstatus == 2:
        frappe.throw(_("Work Order is closed or cancelled."))

    if (
        mr.docstatus != 1
        or mr.material_request_type != "Material Issue"
        or mr.maintenance_work_order != wo.name
        or mr.company != wo.company
        or picking.company != wo.company
    ):
        frappe.throw(_("Invalid maintenance material request."))

    warehouse = frappe.get_doc("Warehouse", picking.warehouse)

    if (
        warehouse.company != wo.company
        or warehouse.disabled
        or warehouse.is_group
    ):
        frappe.throw(_("Invalid issuing warehouse."))

    parts = {p.name: p for p in wo.parts_and_materials}
    request_items = {r.name: r for r in mr.items}

    se = frappe.new_doc("Stock Entry")
    se.company = picking.company
    se.stock_entry_type = "Material Issue"
    se.purpose = "Material Issue"
    se.maintenance_work_order = wo.name
    se.maintenance_picking = picking.name

    totals = {}
    seen_request_items = set()

    for row in picking.items:
        qty = flt(row.picked_qty, 3)

        if qty < 0:
            frappe.throw(_("Negative picked quantity is not allowed."))

        if qty == 0:
            continue

        if row.material_request_item in seen_request_items:
            frappe.throw(_("Duplicate Material Request Item in picking."))

        seen_request_items.add(row.material_request_item)

        part = parts.get(row.maintenance_work_order_part)
        mr_item = request_items.get(row.material_request_item)

        if not part or not mr_item:
            frappe.throw(_("Invalid picking source references."))

        if (
            part.item_code != row.item_code
            or mr_item.item_code != row.item_code
            or part.warehouse != picking.warehouse
            or mr_item.warehouse != picking.warehouse
            or mr_item.maintenance_work_order_part != part.name
        ):
            frappe.throw(_("Picking and material request do not match."))

        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
        )

        outstanding = max(
            flt(mr_item.qty) - flt(balance["gross_issued"]),
            0,
        )

        if qty > outstanding + 0.0005:
            frappe.throw(
                _("Picked quantity exceeds outstanding demand for {0}.")
                .format(row.item_code)
            )

        item = frappe.get_doc("Item", row.item_code)

        if not item.is_stock_item or item.disabled:
            frappe.throw(_("Invalid stock item {0}.").format(row.item_code))

        se.append("items", {
            "item_code": row.item_code,
            "qty": qty,
            "s_warehouse": picking.warehouse,
            "uom": item.stock_uom,
            "stock_uom": item.stock_uom,
            "conversion_factor": 1,
            "material_request": mr.name,
            "material_request_item": mr_item.name,
            "maintenance_work_order_part": part.name,
            "maintenance_picking_item": row.name,
        })

        totals[row.item_code] = totals.get(row.item_code, 0) + qty

    if not se.items:
        frappe.throw(_("No picked materials available for issue."))

    for item_code, total in totals.items():
        bin_data = frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": picking.warehouse,
            },
            ["actual_qty", "reserved_qty"],
            as_dict=True,
        )

        actual = flt(bin_data.actual_qty) if bin_data else 0
        reserved = flt(bin_data.reserved_qty) if bin_data else 0

        if total > max(actual - reserved, 0) + 0.0005:
            frappe.throw(
                _("Insufficient stock to issue {0}.").format(item_code)
            )

    # Draft only: normal ERPNext validation still applies.
    se.insert()

    return {
        "name": se.name,
        "created": True,
        "quantity": sum(flt(r.qty) for r in se.items),
    }


@frappe.whitelist()
def confirm_and_issue_picking(picking_name):
    """Confirm picking and submit the corresponding stock issue."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    roles = set(frappe.get_roles(frappe.session.user))

    if not roles.intersection({"Stock User", "Stock Manager"}):
        frappe.throw(
            _("Warehouse permission is required."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("write")

    frappe.has_permission("Stock Entry", "create", throw=True)
    frappe.has_permission("Stock Entry", "submit", throw=True)

    # Existing function validates references, remaining demand,
    # quantities, stock and the Work Order lock.
    confirmation = confirm_picking(picking_name)

    # Only a new Stock Entry may be submitted in this cycle.
    issue = create_material_issue(picking_name)

    if not issue.get("created"):
        frappe.throw(
            _("An existing Stock Entry requires investigation "
              "before confirming another picking cycle.")
        )

    se = frappe.get_doc("Stock Entry", issue["name"])

    # ERPNext submission runs the maintenance submission guards
    # and updates the Stock Ledger and Work Order quantities.
    se.submit()

    # The submission hook calculates the persistent Picking status.
    status = frappe.db.get_value(
        "Maintenance Picking",
        picking_name,
        "status",
    )

    return {
        "picking": picking_name,
        "stock_entry": se.name,
        "stock_entry_docstatus": se.docstatus,
        "issued_quantity": flt(issue["quantity"], 3),
        "picking_status": status,
        "confirmation": confirmation,
    }


@frappe.whitelist()
def create_picking_stock_verification(picking_name, picking_item):
    """Create a physical stock count linked to a Picking line."""

    if not picking_name or not picking_item:
        frappe.throw(_("Picking and Picking Item are required."))

    if not {"Stock User", "Stock Manager"}.intersection(
        frappe.get_roles(frappe.session.user)
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("read")

    frappe.has_permission(
        "Maintenance Stock Verification",
        "create",
        throw=True,
    )

    if picking.status == "Issued":
        frappe.throw(_("Picking is already fulfilled."))

    lines = {
        row.name: row
        for row in picking.items
    }

    line = lines.get(picking_item)

    if not line:
        frappe.throw(_("Picking Item does not belong to this Picking."))

    # Reuse an existing draft count instead of creating duplicates.
    existing = frappe.get_all(
        "Maintenance Stock Verification",
        filters={
            "maintenance_picking": picking.name,
            "maintenance_picking_item": line.name,
            "status": "Draft",
        },
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )

    if existing:
        return {
            "name": existing[0].name,
            "created": False,
        }

    verification = frappe.new_doc("Maintenance Stock Verification")
    verification.company = picking.company
    verification.item_code = line.item_code
    verification.warehouse = picking.warehouse
    verification.maintenance_picking = picking.name
    verification.maintenance_picking_item = line.name
    verification.status = "Draft"

    # Initial suggestion only; operator must physically count.
    verification.counted_quantity = 0

    verification.insert()

    return {
        "name": verification.name,
        "created": True,
    }


@frappe.whitelist(methods=["POST"])
def refresh_picking_availability(picking_name):
    """Refresh availability without changing picked quantities."""

    if not {"Stock User", "Stock Manager"}.intersection(
        frappe.get_roles(frappe.session.user)
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("write")

    # Serialize refresh with maintenance issuance operations.
    frappe.db.sql(
        """
        SELECT name FROM `tabMaintenance Work Order`
        WHERE name = %s FOR UPDATE
        """,
        (picking.maintenance_work_order,),
    )

    picking.reload()

    if picking.status not in (
        "Draft", "Prepared", "Partially Issued"
    ):
        frappe.throw(_("Picking is not open for refresh."))

    from metagest_maintenance.maintenance_fulfilment import (
        get_fulfilment_preview,
    )

    preview = get_fulfilment_preview(
        picking.material_request
    )

    allocations = {
        row["material_request_item"]: row
        for row in preview["items"]
    }

    if len(allocations) != len(preview["items"]):
        frappe.throw(_("Duplicate Material Request references."))

    results = []

    for row in picking.items:
        allocation = allocations.get(row.material_request_item)

        if not allocation:
            frappe.throw(_("Picking material reference is missing."))

        if (
            allocation["item_code"] != row.item_code
            or allocation["warehouse"] != picking.warehouse
            or allocation["maintenance_part"]
                != row.maintenance_work_order_part
        ):
            frappe.throw(_("Picking material reference mismatch."))

        pickable = flt(allocation["pickable"], 3)
        picked = flt(row.picked_qty, 3)

        results.append({
            "picking_item": row.name,
            "item_code": row.item_code,
            "required": flt(allocation["required"], 3),
            "issued": flt(allocation["gross_issued"], 3),
            "outstanding": flt(allocation["outstanding"], 3),
            "available": pickable,
            "picked": picked,
            "shortage": flt(allocation["shortage"], 3),
            "verification_status":
                allocation["verification_status"],
            "stock_verified": bool(
                allocation["stock_verified"]
            ),
            "requires_attention": (
                picked > pickable + 0.0005
            ),
        })

    # All validations completed. Update informational fields only.
    for row in results:
        frappe.db.set_value(
            "Maintenance Picking Item",
            row["picking_item"],
            "available_qty",
            row["available"],
            update_modified=False,
        )

    return {
        "picking": picking.name,
        "status": picking.status,
        "items": results,
        "requires_attention": any(
            row["requires_attention"] for row in results
        ),
    }


@frappe.whitelist(methods=["POST"])
def create_picking_batch_verification(picking_name):
    """Create one complete physical stock count for a Picking."""

    if not picking_name:
        frappe.throw(_("Maintenance Picking is required."))

    if not {"Stock User", "Stock Manager"}.intersection(
        frappe.get_roles(frappe.session.user)
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    picking = frappe.get_doc("Maintenance Picking", picking_name)
    picking.check_permission("read")

    frappe.has_permission(
        "Maintenance Picking Verification",
        "create",
        throw=True,
    )

    # Serialize creation against other Picking operations.
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Work Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (picking.maintenance_work_order,),
    )

    if not locked:
        frappe.throw(_("Maintenance Work Order not found."))

    picking.reload()

    if picking.status not in (
        "Draft", "Prepared", "Partially Issued"
    ):
        frappe.throw(_("Picking is not open for verification."))

    mr = frappe.get_doc(
        "Material Request", picking.material_request
    )
    mr.check_permission("read")

    if (
        mr.docstatus != 1
        or mr.material_request_type != "Material Issue"
        or mr.company != picking.company
        or mr.maintenance_work_order != picking.maintenance_work_order
    ):
        frappe.throw(_("Invalid originating Material Request."))

    existing = frappe.get_all(
        "Maintenance Picking Verification",
        filters={
            "maintenance_picking": picking.name,
            "status": "Draft",
        },
        pluck="name",
        order_by="creation desc",
        limit_page_length=1,
    )

    if existing:
        return {
            "name": existing[0],
            "created": False,
        }

    mr_rows = {row.name: row for row in mr.items}

    quantities = {}

    for row in picking.items:
        request_row = mr_rows.get(row.material_request_item)

        if (
            not request_row
            or request_row.item_code != row.item_code
            or request_row.warehouse != picking.warehouse
            or request_row.maintenance_work_order_part
                != row.maintenance_work_order_part
        ):
            frappe.throw(_("Invalid Picking material reference."))

        key = (row.item_code, request_row.warehouse)

        quantities[key] = (
            quantities.get(key, 0)
            + flt(request_row.qty, 3)
        )

    if not quantities:
        frappe.throw(_("Picking contains no materials."))

    verification = frappe.new_doc(
        "Maintenance Picking Verification"
    )

    verification.company = picking.company
    verification.maintenance_picking = picking.name
    verification.maintenance_work_order = (
        picking.maintenance_work_order
    )
    verification.warehouse = picking.warehouse
    verification.status = "Draft"

    for (item_code, warehouse), required in sorted(
        quantities.items()
    ):
        bin_data = frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": warehouse,
            },
            ["actual_qty"],
            as_dict=True,
        )

        actual = (
            flt(bin_data.actual_qty, 3)
            if bin_data else 0
        )

        verification.append("items", {
            "item_code": item_code,
            "warehouse": warehouse,
            "required_qty": flt(required, 3),
            "system_quantity": actual,
            "counted_quantity": 0,
            "count_confirmed": 0,
            "difference": 0,
        })

    verification.insert()

    return {
        "name": verification.name,
        "created": True,
        "items": len(verification.items),
    }
