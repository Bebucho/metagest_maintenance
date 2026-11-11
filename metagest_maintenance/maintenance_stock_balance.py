import frappe
from frappe.utils import flt


def get_material_balance(
    work_order, part_name, item_code, warehouse,
    exclude_stock_entry=None
):
    args = {
        "wo": work_order,
        "part": part_name,
        "item": item_code,
        "warehouse": warehouse,
        "excluded": exclude_stock_entry or "",
    }

    gross = frappe.db.sql("""
        SELECT COALESCE(SUM(d.transfer_qty), 0)
        FROM `tabStock Entry Detail` d
        INNER JOIN `tabStock Entry` se ON se.name = d.parent
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.maintenance_work_order = %(wo)s
          AND d.maintenance_work_order_part = %(part)s
          AND d.item_code = %(item)s
          AND d.s_warehouse = %(warehouse)s
          AND se.name != %(excluded)s
    """, args)[0][0]

    returned = frappe.db.sql("""
        SELECT COALESCE(SUM(d.transfer_qty), 0)
        FROM `tabStock Entry Detail` d
        INNER JOIN `tabStock Entry` se ON se.name = d.parent
        INNER JOIN `tabStock Entry Detail` original
            ON original.name = d.ste_detail
           AND original.parent = d.against_stock_entry
        INNER JOIN `tabStock Entry` issue
            ON issue.name = original.parent
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Receipt'
          AND se.maintenance_work_order = %(wo)s
          AND d.maintenance_work_order_part = %(part)s
          AND d.item_code = %(item)s
          AND d.t_warehouse = %(warehouse)s
          AND issue.docstatus = 1
          AND issue.purpose = 'Material Issue'
          AND issue.maintenance_work_order = %(wo)s
          AND original.maintenance_work_order_part = %(part)s
          AND original.item_code = %(item)s
          AND original.s_warehouse = %(warehouse)s
          AND se.name != %(excluded)s
    """, args)[0][0]

    gross = flt(gross, 3)
    returned = flt(returned, 3)

    if returned > gross + 0.0005:
        frappe.throw(
            "Maintenance returns exceed the original material issues."
        )

    return {
        "gross_issued": gross,
        "returned": returned,
        "net_issued": flt(gross - returned, 3),
    }
