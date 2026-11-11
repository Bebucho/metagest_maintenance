# Copyright (c) 2026, TeorLogico and contributors

import frappe
from frappe import _
from frappe.model.document import Document


class MaintenancePicking(Document):

    def validate(self):
        previous = self.get_doc_before_save()

        if not previous:
            if self.status != "Draft":
                frappe.throw(_("New pickings must start as Draft."))
            return

        # The backend may reopen a partially fulfilled Picking
        # after a successfully submitted Stock Entry.
        if self.flags.get("maintenance_reopening"):
            if previous.status != "Confirmed":
                frappe.throw(
                    _("Only a confirmed Picking may be reopened.")
                )

            if self.status != "Draft":
                frappe.throw(
                    _("Partially fulfilled Picking must return to Draft.")
                )

            return

        if previous.status in ("Confirmed", "Issued"):
            frappe.throw(
                _("Confirmed or issued pickings cannot be edited.")
            )

        if previous.status == "Partially Issued":
            if self.status == "Confirmed":
                if not self.flags.get("maintenance_confirming"):
                    frappe.throw(_("Use Confirm & Issue Materials."))
            elif self.status != "Partially Issued":
                frappe.throw(_("Invalid Picking status transition."))

        if self.status == "Issued":
            frappe.throw(
                _("Picking cannot be marked Issued manually.")
            )

        if self.status == "Confirmed":
            if not self.flags.get("maintenance_confirming"):
                frappe.throw(
                    _("Use the Confirm Picking action.")
                )

            if previous.status not in ("Draft", "Prepared", "Partially Issued"):
                frappe.throw(_("Invalid picking status transition."))
