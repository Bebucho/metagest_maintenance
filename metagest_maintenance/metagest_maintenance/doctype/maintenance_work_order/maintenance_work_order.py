# Copyright (c) 2026, TeorLogico
# Metagest Maintenance

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MaintenanceWorkOrder(Document):

    def validate(self):
        self.validate_external_service_integrity()
        self.validate_external_service_system_fields()
        self.validate_issued_quantities()
        self.calculate_costs()
        self.validate_consumption()
        self.validate_stock_availability()
        self.validate_closure()



    def validate_external_service_integrity(self):
        """Protect service identity and references after procurement."""

        previous = self.get_doc_before_save()

        if not previous:
            return

        old_rows = {
            row.name: row
            for row in (
                previous.get("custom_external_services") or []
            )
        }

        new_rows = {
            row.name: row
            for row in (
                self.get("custom_external_services") or []
            )
        }

        protected_fields = (
            "service_item",
            "description",
            "quantity",
            "estimated_rate",
        )

        for name, old in old_rows.items():
            # A reference may also exist on a Purchase Request Item
            # following an interrupted or legacy migration.
            linked = bool(
                old.material_request
                or old.purchase_order
                or frappe.db.exists(
                    "Material Request Item",
                    {
                        "custom_maintenance_external_service": name
                    },
                )
            )

            if not linked:
                continue

            current = new_rows.get(name)

            if not current:
                frappe.throw(
                    _("External service row {0} cannot be deleted "
                      "after procurement has begun.").format(
                        old.idx
                    )
                )

            for fieldname in protected_fields:
                before = old.get(fieldname)
                after = current.get(fieldname)

                if fieldname in ("quantity", "estimated_rate"):
                    different = (
                        abs(flt(before) - flt(after)) > 0.000001
                    )
                else:
                    different = (
                        (before or "") != (after or "")
                    )

                if different:
                    frappe.throw(
                        _("External service row {0}: {1} cannot "
                          "be changed after procurement has begun.")
                        .format(old.idx, fieldname)
                    )

            # Once linked, procurement references are immutable.
            # Initially empty PO references may still be populated
            # by the controlled Service Acceptance routine.
            if (
                old.material_request
                and current.material_request
                    != old.material_request
            ):
                frappe.throw(
                    _("External service row {0}: Purchase Request "
                      "reference cannot be changed.").format(
                        old.idx
                    )
                )

            if (
                old.purchase_order
                and current.purchase_order
                    != old.purchase_order
            ):
                frappe.throw(
                    _("External service row {0}: Purchase Order "
                      "reference cannot be changed.").format(
                        old.idx
                    )
                )


    def validate_external_service_system_fields(self):
        """Protect backend-managed maintenance service data."""

        previous = self.get_doc_before_save()

        # Only trusted internal service workflows set this flag.
        authorised = bool(
            self.flags.get("maintenance_service_system_update")
        )

        protected = (
            "material_request",
            "purchase_order",
            "supplier",
            "procurement_status",
            "acceptance_status",
            "acceptance_reference",
            "accepted_on",
            "custom_accepted_by",
            "custom_acceptance_notes",
            "received_quantity",
            "actual_cost",
            "financial_status",
        )

        numeric = {"received_quantity", "actual_cost"}

        old_rows = {
            row.name: row
            for row in (
                previous.get("custom_external_services") or []
            )
        } if previous else {}

        for row in self.get("custom_external_services") or []:

            # Estimates are derived values, not manually entered costs.
            expected_estimate = flt(
                flt(row.quantity, 3)
                * flt(row.estimated_rate, 2),
                2,
            )

            if (
                abs(
                    flt(row.estimated_cost, 2)
                    - expected_estimate
                ) > 0.01
            ):
                frappe.throw(
                    _("External service row {0}: estimated cost "
                      "does not match quantity and rate.")
                    .format(row.idx)
                )

            old = old_rows.get(row.name)

            if not old:
                if authorised:
                    continue

                defaults = {
                    "procurement_status": ("", "Not Requested"),
                    "acceptance_status": ("", "Not Started"),
                    "financial_status": ("", "Not Received"),
                }

                for fieldname in protected:
                    value = row.get(fieldname)

                    if fieldname in numeric:
                        invalid = abs(flt(value)) > 0.000001
                    elif fieldname in defaults:
                        invalid = (
                            (value or "")
                            not in defaults[fieldname]
                        )
                    else:
                        invalid = bool(value)

                    if invalid:
                        frappe.throw(
                            _("External service row {0}: {1} "
                              "is managed by the system.")
                            .format(row.idx, fieldname)
                        )

                continue

            if authorised:
                continue

            for fieldname in protected:
                before = old.get(fieldname)
                after = row.get(fieldname)

                if fieldname in numeric:
                    changed = (
                        abs(flt(before) - flt(after))
                        > 0.000001
                    )
                else:
                    changed = (before or "") != (after or "")

                if changed:
                    frappe.throw(
                        _("External service row {0}: {1} "
                          "can only be updated by the "
                          "authorised maintenance workflow.")
                        .format(row.idx, fieldname)
                    )

        # The Work Order total is also system-managed.
        if previous and not authorised:
            if (
                abs(
                    flt(self.external_services_total, 2)
                    - flt(previous.external_services_total, 2)
                ) > 0.01
            ):
                frappe.throw(
                    _("External Services Total is calculated "
                      "from submitted Purchase Receipts.")
                )

    def validate_issued_quantities(self):
        """Issued quantities must agree with submitted stock transactions."""

        from metagest_maintenance.maintenance_stock_balance import (
            get_material_balance,
        )

        for row in self.get("parts_and_materials") or []:
            balance = get_material_balance(
                self.name,
                row.name,
                row.item_code,
                row.warehouse,
            )

            expected = balance["net_issued"]
            entered = flt(row.issued_quantity, 3)

            if abs(expected - entered) > 0.0005:
                frappe.throw(
                    _("Row {0}: Net issued quantity must be {1}. "
                      "Manual changes are not permitted.").format(
                        row.idx, expected
                    )
                )

    def calculate_costs(self):
        parts_total = 0
        labour_total = 0

        for row in self.get("parts_and_materials") or []:
            qty = flt(row.consumed_quantity)
            rate = flt(row.unit_cost)

            if qty < 0 or rate < 0:
                frappe.throw(
                    _("Negative parts quantities or costs are not allowed.")
                )

            row.total_cost = flt(qty * rate, 2)
            parts_total += row.total_cost

        for row in self.get("labour") or []:
            hours = flt(row.hours_worked)
            rate = flt(row.hourly_rate)

            if hours < 0 or rate < 0:
                frappe.throw(
                    _("Negative labour hours or rates are not allowed.")
                )

            row.labour_cost = flt(hours * rate, 2)
            labour_total += row.labour_cost

        external = flt(self.external_services_total)

        if external < 0:
            frappe.throw(
                _("External services cost cannot be negative.")
            )

        self.parts_total_cost = flt(parts_total, 2)
        self.labour_total_cost = flt(labour_total, 2)
        self.total_maintenance_cost = flt(
            parts_total + labour_total + external, 2
        )

    def validate_consumption(self):
        for row in self.get("parts_and_materials") or []:
            required = flt(row.required_quantity)
            issued = flt(row.issued_quantity)
            consumed = flt(row.consumed_quantity)

            if required < 0 or issued < 0 or consumed < 0:
                frappe.throw(
                    _("Negative material quantities are not allowed "
                      "on row {0}.").format(row.idx)
                )

            if consumed > issued:
                frappe.throw(
                    _("Row {0}: consumed quantity ({1}) exceeds "
                      "issued quantity ({2}) for item {3}.").format(
                        row.idx,
                        consumed,
                        issued,
                        row.item_code
                    )
                )

    def validate_stock_availability(self):
        requirements = {}

        for row in self.get("parts_and_materials") or []:
            item_code = row.item_code
            warehouse = row.warehouse
            required = flt(row.required_quantity)

            if not item_code or not warehouse or required <= 0:
                continue

            key = (item_code, warehouse)
            # Only materials not yet issued require stock allocation.
            # issued_quantity is reconciled from submitted Stock Entries.
            issued = flt(row.issued_quantity)
            outstanding = max(required - issued, 0)

            requirements[key] = (
                requirements.get(key, 0) + outstanding
            )

        shortages = []

        for (item_code, warehouse), required in requirements.items():
            wh = frappe.db.get_value(
                "Warehouse",
                warehouse,
                ["company", "is_group", "disabled"],
                as_dict=True
            )

            if not wh:
                frappe.throw(
                    _("Warehouse {0} does not exist.").format(warehouse)
                )

            if (
                wh.company != self.company
                or wh.is_group
                or wh.disabled
            ):
                frappe.throw(
                    _("Warehouse {0} is not a valid stock warehouse "
                      "for company {1}.").format(
                        warehouse, self.company
                    )
                )

            bin_data = frappe.db.get_value(
                "Bin",
                {
                    "item_code": item_code,
                    "warehouse": warehouse
                },
                ["actual_qty", "reserved_qty"],
                as_dict=True
            )

            actual = flt(bin_data.actual_qty) if bin_data else 0
            reserved = flt(bin_data.reserved_qty) if bin_data else 0
            available = max(actual - reserved, 0)

            if required > available:
                shortages.append(
                    _(
                        "Item {0}, warehouse {1}: "
                        "required {2}, available {3}, shortage {4}."
                    ).format(
                        item_code,
                        warehouse,
                        required,
                        available,
                        flt(required - available, 3)
                    )
                )

        if shortages:
            frappe.msgprint(
                "\n".join(shortages),
                title=_("Maintenance Material Shortage"),
                indicator="orange"
            )

    def validate_closure(self):
        if self.status != "Closed":
            return

        from metagest_maintenance.maintenance_service_closure import (
            validate_external_service_closure,
        )
        validate_external_service_closure(self)

        if not self.asset:
            frappe.throw(
                _("Asset is required to close the work order.")
            )

        if not self.assigned_to:
            frappe.throw(
                _("Assigned technician is required.")
            )

        if not self.supervisor:
            frappe.throw(
                _("Supervisor is required.")
            )

        if not self.completion_date:
            frappe.throw(
                _("Completion date is required.")
            )

        if not (self.actions_performed or "").strip():
            frappe.throw(
                _("Actions performed must be documented.")
            )

        for row in self.get("table_beic") or []:
            if row.mandatory and not row.completed:
                frappe.throw(
                    _("Mandatory checklist task {0} is incomplete.")
                    .format(row.idx)
                )
