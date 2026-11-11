import frappe
from frappe import _
from frappe.utils import flt


def block_accepted_service_po_cancellation(doc, method=None):
    """Do not cancel a PO supporting an accepted maintenance service."""

    accepted = frappe.get_all(
        "Maintenance Work Order External Service",
        filters={
            "purchase_order": doc.name,
            "acceptance_status": "Accepted",
        },
        fields=["name", "parent"],
        limit_page_length=1,
    )

    if accepted:
        frappe.throw(
            _("Purchase Order {0} supports an accepted service "
              "on Work Order {1}. Reverse the acceptance "
              "through an authorised process before cancellation.")
            .format(doc.name, accepted[0].parent)
        )


@frappe.whitelist()
def get_external_service_receipt_preview(work_order):
    """Permission-controlled preview for users."""
    wo = frappe.get_doc("Maintenance Work Order", work_order)
    wo.check_permission("read")
    return _get_external_service_receipt_preview(work_order)


def _get_external_service_receipt_preview(work_order):
    """Internal reconciliation of submitted Purchase Receipts."""
    wo = frappe.get_doc("Maintenance Work Order", work_order)

    results = []

    for service in wo.get("custom_external_services") or []:
        result = {
            "service_row": service.name,
            "service_item": service.service_item,
            "purchase_request": service.material_request,
            "purchase_order": service.purchase_order,
            "acceptance_status": service.acceptance_status,
            "estimated_cost": flt(service.estimated_cost, 2),
            "received_quantity": 0.0,
            "actual_cost": 0.0,
            "receipt_documents": [],
            "process_status": "Not Requested",
        }

        if not service.material_request:
            results.append(result)
            continue

        mr = frappe.get_doc(
            "Material Request", service.material_request
        )

        if (
            mr.company != wo.company
            or mr.maintenance_work_order != wo.name
            or mr.material_request_type != "Purchase"
            or mr.custom_maintenance_procurement_type
                != "External Service"
        ):
            frappe.throw(
                _("Inconsistent service Purchase Request {0}.")
                .format(mr.name)
            )

        mr_lines = [
            line for line in mr.items
            if line.custom_maintenance_external_service == service.name
            and line.item_code == service.service_item
        ]

        if len(mr_lines) != 1:
            frappe.throw(
                _("Invalid service-line reference on {0}.")
                .format(mr.name)
            )

        # Standard ERPNext PO lines retain the Material Request Item ID.
        orders = frappe.db.sql(
            """
            SELECT
                poi.name AS po_item,
                poi.parent AS po_name,
                poi.qty AS ordered_qty,
                po.docstatus,
                po.company,
                po.supplier
            FROM `tabPurchase Order Item` poi
            INNER JOIN `tabPurchase Order` po
                ON po.name = poi.parent
            WHERE poi.material_request = %s
              AND poi.material_request_item = %s
              AND poi.item_code = %s
              AND po.docstatus = 1
              AND po.company = %s
            """,
            (
                mr.name,
                mr_lines[0].name,
                service.service_item,
                wo.company,
            ),
            as_dict=True,
        )

        if not orders:
            result["process_status"] = "Awaiting Purchase Order"
            results.append(result)
            continue

        if service.purchase_order and any(
            order.po_name != service.purchase_order
            for order in orders
        ):
            frappe.throw(
                _("Purchase Order reference mismatch for service {0}.")
                .format(service.name)
            )

        order_ids = [order.po_item for order in orders]
        receipts = frappe.db.sql(
            """
            SELECT
                pri.parent AS receipt,
                pri.purchase_order,
                pri.purchase_order_item,
                pri.item_code,
                pri.qty,
                pri.base_net_amount,
                pr.company
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr
                ON pr.name = pri.parent
            WHERE pr.docstatus = 1
              AND pr.company = %(company)s
              AND pri.purchase_order_item IN %(order_ids)s
            """,
            {
                "order_ids": tuple(order_ids),
                "company": wo.company,
            },
            as_dict=True,
        )

        seen_receipts = set()

        for receipt in receipts:
            if receipt.item_code != service.service_item:
                frappe.throw(
                    _("Receipt item mismatch for service {0}.")
                    .format(service.name)
                )

            result["received_quantity"] += flt(receipt.qty, 3)
            result["actual_cost"] += flt(
                receipt.base_net_amount, 2
            )

            seen_receipts.add(receipt.receipt)

        result["received_quantity"] = flt(
            result["received_quantity"], 3
        )
        result["actual_cost"] = flt(
            result["actual_cost"], 2
        )
        result["receipt_documents"] = sorted(seen_receipts)

        ordered = sum(flt(o.ordered_qty, 3) for o in orders)
        received = result["received_quantity"]

        if received >= ordered - 0.0005:
            result["process_status"] = "Received"
        elif received > 0:
            result["process_status"] = "Partially Received"
        elif service.acceptance_status == "Accepted":
            result["process_status"] = "Accepted - Awaiting Receipt"
        else:
            result["process_status"] = "Ordered - Awaiting Acceptance"

        results.append(result)

    return {
        "work_order": wo.name,
        "services": results,
        "external_services_actual_total": flt(
            sum(r["actual_cost"] for r in results), 2
        ),
    }
