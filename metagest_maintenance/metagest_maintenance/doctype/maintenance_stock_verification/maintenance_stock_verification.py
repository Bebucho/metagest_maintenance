import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class MaintenanceStockVerification(Document):

    def validate(self):
        previous = self.get_doc_before_save()

        if previous and previous.status != "Draft":
            frappe.throw(
                _("Confirmed stock verifications cannot be edited.")
            )

        if not self.is_new() and self.status != "Draft":
            if not self.flags.get("maintenance_verifying"):
                frappe.throw(
                    _("Use the Verify Count action.")
                )

        if flt(self.counted_quantity) < 0:
            frappe.throw(
                _("Counted quantity cannot be negative.")
            )

        warehouse = frappe.get_doc("Warehouse", self.warehouse)

        if (
            warehouse.company != self.company
            or warehouse.is_group
            or warehouse.disabled
        ):
            frappe.throw(_("Invalid warehouse."))

        item = frappe.get_doc("Item", self.item_code)

        if not item.is_stock_item or item.disabled:
            frappe.throw(_("Invalid stock item."))


@frappe.whitelist()
def verify_count(name):

    if not name:
        frappe.throw(_("Stock Verification is required."))

    if not set(frappe.get_roles()).intersection(
        {"Stock User", "Stock Manager"}
    ):
        frappe.throw(
            _("Warehouse permission required."),
            frappe.PermissionError,
        )

    doc = frappe.get_doc("Maintenance Stock Verification", name)
    doc.check_permission("write")

    if doc.status != "Draft":
        frappe.throw(_("This count has already been verified."))

    # Lock the warehouse balance while capturing the system quantity.
    stock = frappe.db.sql(
        """
        SELECT actual_qty
        FROM `tabBin`
        WHERE item_code = %s
          AND warehouse = %s
        FOR UPDATE
        """,
        (doc.item_code, doc.warehouse),
    )

    system_qty = flt(stock[0][0], 3) if stock else 0
    counted_qty = flt(doc.counted_quantity, 3)

    doc.system_quantity = system_qty
    doc.difference = flt(counted_qty - system_qty, 3)
    doc.counted_by = frappe.session.user
    doc.count_date = now_datetime()

    doc.status = (
        "Verified"
        if abs(doc.difference) <= 0.0005
        else "Discrepancy"
    )

    doc.flags.maintenance_verifying = True
    doc.save()

    return {
        "name": doc.name,
        "status": doc.status,
        "system_quantity": doc.system_quantity,
        "counted_quantity": doc.counted_quantity,
        "difference": doc.difference,
    }


@frappe.whitelist(methods=["POST"])
def reconcile_stock_discrepancy(name, reason):
    """Post an authorised inventory correction from physical verification."""

    if "Stock Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw(
            _("Stock Manager authorisation required."),
            frappe.PermissionError,
        )

    if not name or not (reason or "").strip():
        frappe.throw(_("Verification and adjustment reason are required."))

    frappe.has_permission(
        "Stock Reconciliation", "create", throw=True
    )
    frappe.has_permission(
        "Stock Reconciliation", "submit", throw=True
    )

    # Lock the verification to prevent concurrent duplicate adjustments.
    frappe.db.sql(
        """
        SELECT name
        FROM `tabMaintenance Stock Verification`
        WHERE name = %s
        FOR UPDATE
        """,
        name,
    )

    verification = frappe.get_doc("Maintenance Stock Verification", name)
    verification.check_permission("write")

    if verification.status != "Discrepancy":
        frappe.throw(
            _("Only verified discrepancies can be reconciled.")
        )

    if verification.stock_reconciliation:
        frappe.throw(_("This verification is already reconciled."))

    # Lock and recheck the inventory position.
    stock = frappe.db.sql(
        """
        SELECT actual_qty, valuation_rate
        FROM `tabBin`
        WHERE item_code = %s
          AND warehouse = %s
        FOR UPDATE
        """,
        (verification.item_code, verification.warehouse),
        as_dict=True,
    )

    actual_qty = flt(stock[0].actual_qty, 3) if stock else 0
    valuation_rate = flt(stock[0].valuation_rate) if stock else 0

    if abs(actual_qty - flt(verification.system_quantity, 3)) > 0.0005:
        frappe.throw(
            _("Stock has changed since physical verification. "
              "Perform a new count.")
        )

    if valuation_rate <= 0:
        frappe.throw(
            _("A positive valuation rate is required. "
              "Stock Manager must review the item valuation.")
        )

    if abs(
        flt(verification.counted_quantity, 3) - actual_qty
    ) <= 0.0005:
        frappe.throw(_("No inventory discrepancy remains."))

    reconciliation = frappe.new_doc("Stock Reconciliation")
    reconciliation.company = verification.company
    reconciliation.purpose = "Stock Reconciliation"
    reconciliation.set_posting_time = 0

    reconciliation.append("items", {
        "item_code": verification.item_code,
        "warehouse": verification.warehouse,
        "qty": flt(verification.counted_quantity, 3),
        "valuation_rate": valuation_rate,
    })

    reconciliation.insert()
    reconciliation.submit()

    # Controlled transition to Reconciled.
    # Preserve the approved reason and posting evidence.
    frappe.db.set_value(
        "Maintenance Stock Verification",
        verification.name,
        {
            "adjustment_reason": reason.strip(),
            "adjustment_approved_by": frappe.session.user,
            "adjustment_date": now_datetime(),
            "stock_reconciliation": reconciliation.name,
            "status": "Reconciled",
        },
        update_modified=False,
    )

    return {
        "verification": verification.name,
        "status": "Reconciled",
        "stock_reconciliation": reconciliation.name,
        "previous_quantity": actual_qty,
        "adjusted_quantity": flt(verification.counted_quantity, 3),
        "valuation_rate": valuation_rate,
    }


def block_maintenance_reconciliation_cancel(doc, method=None):
    """Protect stock reconciliations created by maintenance verification."""

    linked_verification = frappe.db.get_value(
        "Maintenance Stock Verification",
        {"stock_reconciliation": doc.name},
        "name",
    )

    if linked_verification:
        frappe.throw(
            _(
                "Stock Reconciliation {0} belongs to Maintenance "
                "Stock Verification {1} and cannot be cancelled directly. "
                "A controlled maintenance reversal is required."
            ).format(doc.name, linked_verification)
        )
