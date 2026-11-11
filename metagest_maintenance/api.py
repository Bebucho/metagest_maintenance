
import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_part_availability(item_code, warehouse, company):
    # Validar permissões
    frappe.has_permission("Item", "read", throw=True)
    frappe.has_permission("Warehouse", "read", throw=True)
    frappe.has_permission("Bin", "read", throw=True)

    if not item_code or not warehouse or not company:
        frappe.throw(_("Item, Warehouse and Company are required"))

    item = frappe.get_doc("Item", item_code)
    wh = frappe.get_doc("Warehouse", warehouse)

    if wh.company != company or wh.is_group or wh.disabled:
        frappe.throw(_("Invalid warehouse for the selected company"))

    if not item.is_stock_item:
        frappe.throw(_("Selected item does not maintain stock"))

    bin_data = frappe.db.get_value(
        "Bin",
        {"item_code": item_code, "warehouse": warehouse},
        ["actual_qty", "reserved_qty", "valuation_rate"],
        as_dict=True,
    )

    actual = flt(bin_data.actual_qty) if bin_data else 0
    reserved = flt(bin_data.reserved_qty) if bin_data else 0
    rate = flt(bin_data.valuation_rate) if bin_data else 0

    return {
        "item_name": item.item_name,
        "uom": item.stock_uom,
        "actual_qty": actual,
        "reserved_qty": reserved,
        "available_qty": max(actual - reserved, 0),
        "valuation_rate": rate,
        "has_bin": bool(bin_data),
    }
