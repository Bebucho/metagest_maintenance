import frappe
from frappe import _
from frappe.utils import flt

from metagest_maintenance.maintenance_stock_balance import (
    get_material_balance,
)




def get_active_picking_commitments():
    """Count confirmed quantities not yet issued to maintenance."""

    records = frappe.db.sql(
        """
        SELECT
            pi.material_request_item,
            pi.item_code,
            mri.warehouse,
            pi.picked_qty
        FROM `tabMaintenance Picking Item` pi
        INNER JOIN `tabMaintenance Picking` p
            ON p.name = pi.parent
        INNER JOIN `tabMaterial Request Item` mri
            ON mri.name = pi.material_request_item
           AND mri.parent = p.material_request
        WHERE p.status = 'Confirmed'
          AND p.docstatus < 2
          AND pi.picked_qty > 0
          AND NOT EXISTS (
              SELECT 1
              FROM `tabStock Entry Detail` sed
              INNER JOIN `tabStock Entry` se
                  ON se.name = sed.parent
              WHERE sed.maintenance_picking_item = pi.name
                AND se.docstatus = 1
          )
        """,
        as_dict=True,
    )

    by_stock = {}
    by_request_item = {}

    for row in records:
        key = (row.item_code, row.warehouse)
        qty = flt(row.picked_qty, 3)

        by_stock[key] = flt(
            by_stock.get(key, 0) + qty, 3
        )

        by_request_item[row.material_request_item] = flt(
            by_request_item.get(row.material_request_item, 0)
            + qty, 3
        )

    return by_stock, by_request_item



def get_stock_verification_status(company, item_code, warehouse):
    """Validate the latest completed physical count against stock movements."""

    records = frappe.get_all(
        "Maintenance Stock Verification",
        filters={
            "company": company,
            "item_code": item_code,
            "warehouse": warehouse,
            "status": ["in", ["Verified", "Discrepancy", "Reconciled"]],
        },
        fields=[
            "name", "status", "count_date",
            "system_quantity", "counted_quantity",
            "stock_reconciliation",
        ],
        order_by="count_date desc, creation desc",
        limit_page_length=1,
    )

    if not records:
        return {
            "status": "Not Verified",
            "valid": False,
            "verification": None,
        }

    record = records[0]

    if record.status == "Discrepancy":
        return {
            "status": "Discrepancy",
            "valid": False,
            "verification": record.name,
        }

    if not record.count_date:
        return {
            "status": "Stale",
            "valid": False,
            "verification": record.name,
        }

    if record.status == "Reconciled":
        sr_name = record.stock_reconciliation

        if not sr_name or frappe.db.get_value(
            "Stock Reconciliation", sr_name, "docstatus"
        ) != 1:
            return {
                "status": "Stale",
                "valid": False,
                "verification": record.name,
            }

        # The adjustment's own Stock Ledger Entry is expected.
        # Only subsequent movements invalidate its physical count.
        anchor = frappe.db.sql(
            """
            SELECT MAX(creation)
            FROM `tabStock Ledger Entry`
            WHERE voucher_type = 'Stock Reconciliation'
              AND voucher_no = %s
              AND item_code = %s
              AND warehouse = %s
              AND is_cancelled = 0
            """,
            (sr_name, item_code, warehouse),
        )[0][0]

        if not anchor:
            return {
                "status": "Stale",
                "valid": False,
                "verification": record.name,
            }

    else:
        anchor = record.count_date

    movements = frappe.db.sql(
        """
        SELECT name
        FROM `tabStock Ledger Entry`
        WHERE item_code = %s
          AND warehouse = %s
          AND is_cancelled = 0
          AND creation > %s
        LIMIT 1
        """,
        (item_code, warehouse, anchor),
    )

    current_qty = frappe.db.get_value(
        "Bin",
        {"item_code": item_code, "warehouse": warehouse},
        "actual_qty",
    )

    if movements or abs(
        flt(current_qty, 3) - flt(record.counted_quantity, 3)
    ) > 0.0005:
        return {
            "status": "Stale",
            "valid": False,
            "verification": record.name,
        }

    return {
        "status": record.status,
        "valid": True,
        "verification": record.name,
    }


@frappe.whitelist()
def get_fulfilment_preview(material_request):

    if not material_request:
        frappe.throw(_("Material Request is required."))

    if not set(frappe.get_roles()).intersection(
        {"Stock User", "Stock Manager"}
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    mr = frappe.get_doc("Material Request", material_request)
    mr.check_permission("read")

    if mr.docstatus != 1 or mr.material_request_type != "Material Issue":
        frappe.throw(_("A submitted Material Issue Request is required."))

    if not mr.maintenance_work_order:
        frappe.throw(_("Maintenance Work Order reference missing."))

    wo = frappe.get_doc(
        "Maintenance Work Order",
        mr.maintenance_work_order,
    )
    wo.check_permission("read")

    if wo.company != mr.company:
        frappe.throw(_("Company mismatch."))

    parts = {p.name: p for p in wo.parts_and_materials}

    results = []
    demand = {}

    for row in mr.items:
        part = parts.get(row.maintenance_work_order_part)

        if (
            not part
            or part.item_code != row.item_code
            or part.warehouse != row.warehouse
        ):
            frappe.throw(_("Invalid maintenance material reference."))

        balance = get_material_balance(
            wo.name,
            part.name,
            part.item_code,
            part.warehouse,
        )

        required = flt(row.qty, 3)
        gross_issued = flt(balance["gross_issued"], 3)
        outstanding = max(required - gross_issued, 0)

        key = (row.item_code, row.warehouse)

        demand.setdefault(key, 0)
        demand[key] += outstanding

        results.append({
            "material_request_item": row.name,
            "maintenance_part": part.name,
            "item_code": row.item_code,
            "warehouse": row.warehouse,
            "required": required,
            "gross_issued": gross_issued,
            "outstanding": outstanding,
        })

    picking_commitments, picking_by_request_item = (
        get_active_picking_commitments()
    )

    remaining = {}

    for item_code, warehouse in demand:
        stock = frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": warehouse},
            ["actual_qty", "reserved_qty"],
            as_dict=True,
        )

        actual = flt(stock.actual_qty) if stock else 0
        reserved = flt(stock.reserved_qty) if stock else 0

        committed = picking_commitments.get(
            (item_code, warehouse), 0
        )

        remaining[(item_code, warehouse)] = max(
            actual - reserved - committed, 0
        )

    for row in results:
        key = (row["item_code"], row["warehouse"])

        available = remaining[key]

        committed = min(
            row["outstanding"],
            picking_by_request_item.get(
                row["material_request_item"], 0
            ),
        )

        total_committed = flt(
            picking_by_request_item.get(
                row["material_request_item"], 0
            ), 3
        )

        if total_committed > flt(row["outstanding"], 3) + 0.0005:
            frappe.throw(
                _("Confirmed Picking commitments exceed outstanding "
                  "demand for Material Request Item {0}.").format(
                    row["material_request_item"]
                )
            )

        unallocated = max(
            row["outstanding"] - committed, 0
        )

        pickable = min(unallocated, available)
        shortage = max(unallocated - pickable, 0)

        row["committed_picking"] = flt(committed, 3)
        row["unallocated_outstanding"] = flt(unallocated, 3)
        row["available"] = flt(available, 3)
        row["pickable"] = flt(pickable, 3)
        row["shortage"] = flt(shortage, 3)

        verification = get_stock_verification_status(
            wo.company,
            row["item_code"],
            row["warehouse"],
        )

        row["stock_verified"] = verification["valid"]
        row["verification_status"] = verification["status"]
        row["verification_document"] = verification["verification"]

        # Quantities remain informational until stock is verified.
        row["ready_for_fulfilment"] = (
            row["outstanding"] > 0
            and row["pickable"] > 0
        )

        remaining[key] -= pickable

    # Procurement commitments are allocated to the original
    # Maintenance Work Order Part, not merely to an item code.
    from metagest_maintenance.maintenance_procurement_commitments import (
        get_procurement_commitments,
    )

    procurement = get_procurement_commitments(wo.name)

    procurement_by_part = {
        item["maintenance_part"]: item
        for item in procurement["items"]
    }

    for row in results:
        commitment = procurement_by_part.get(
            row["maintenance_part"], {}
        )

        committed = flt(
            commitment.get("submitted_remaining", 0), 3
        )
        draft = flt(
            commitment.get("draft_request", 0), 3
        )
        shortage = flt(row["shortage"], 3)

        row["procurement_committed"] = committed
        row["procurement_draft"] = draft
        row["procurement_uncovered"] = flt(
            max(shortage - committed - draft, 0), 3
        )

        row["ready_for_procurement"] = (
            row["procurement_uncovered"] > 0
        )

    return {
        "material_request": mr.name,
        "maintenance_work_order": wo.name,
        "company": wo.company,
        "items": results,
        "requires_stock_verification": any(
            r["outstanding"] > 0 and not r["stock_verified"]
            for r in results
        ),
    }
