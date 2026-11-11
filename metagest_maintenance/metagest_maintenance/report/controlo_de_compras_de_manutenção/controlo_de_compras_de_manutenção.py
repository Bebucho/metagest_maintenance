import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = frappe._dict(filters or {})

    for dt in ("Material Request", "Purchase Order"):
        if not frappe.has_permission(dt, "read"):
            frappe.throw(
                _("Read permission required: {0}").format(dt),
                frappe.PermissionError,
            )

    if (
        filters.get("from_date")
        and filters.get("to_date")
        and str(filters.from_date) > str(filters.to_date)
    ):
        frappe.throw(_("Invalid date range"))

    columns = [
        dict(fieldname="company", label="Empresa",
             fieldtype="Link", options="Company", width=150),
        dict(fieldname="work_order", label="Ordem de Trabalho",
             fieldtype="Link", options="Maintenance Work Order", width=170),
        dict(fieldname="asset", label="Activo",
             fieldtype="Link", options="Asset", width=140),
        dict(fieldname="procurement_type", label="Tipo de Aquisição",
             fieldtype="Data", width=135),
        dict(fieldname="material_request", label="Requisição",
             fieldtype="Link", options="Material Request", width=145),
        dict(fieldname="purchase_order", label="Ordem de Compra",
             fieldtype="Link", options="Purchase Order", width=145),
        dict(fieldname="supplier", label="Fornecedor",
             fieldtype="Link", options="Supplier", width=155),
        dict(fieldname="item_code", label="Artigo / Serviço",
             fieldtype="Link", options="Item", width=160),
        dict(fieldname="uom", label="Unidade",
             fieldtype="Data", width=80),
        dict(fieldname="requested_qty", label="Qtd. Requisitada",
             fieldtype="Float", width=125),
        dict(fieldname="ordered_qty", label="Qtd. Encomendada",
             fieldtype="Float", width=125),
        dict(fieldname="received_qty", label="Qtd. Recebida",
             fieldtype="Float", width=120),
        dict(fieldname="to_order", label="Por Encomendar",
             fieldtype="Float", width=115),
        dict(fieldname="to_receive", label="Por Recepcionar",
             fieldtype="Float", width=120),
        dict(fieldname="base_net_amount", label="Valor Encomendado",
             fieldtype="Currency", options="currency", width=145),
        dict(fieldname="outstanding_value", label="Valor Pendente",
             fieldtype="Currency", options="currency", width=145),
        dict(fieldname="currency", label="Moeda Base",
             fieldtype="Link", options="Currency", width=100),
        dict(fieldname="po_status", label="Estado OC",
             fieldtype="Data", width=145),
    ]

    def documents(dt, conditions):
        offset = 0
        while True:
            rows = frappe.get_list(
                dt,
                filters=conditions,
                fields=["name"],
                order_by="name asc",
                limit_start=offset,
                limit_page_length=200,
            )
            if not rows:
                break
            for row in rows:
                yield row.name
            offset += len(rows)

    data = []
    mr_items = {}
    mr_info = {}
    po_lines = {}

    conditions = {"docstatus": 1}
    if filters.get("company"):
        conditions["company"] = filters.company

    for name in documents("Material Request", conditions):
        mr = frappe.get_doc("Material Request", name)

        if mr.get("material_request_type") != "Purchase":
            continue

        if mr.get("status") == "Stopped":
            continue

        picking = mr.get("custom_maintenance_picking")
        wo_name = mr.get("maintenance_work_order")

        if not wo_name and picking:
            if not frappe.has_permission("Maintenance Picking", "read"):
                continue
            if not frappe.has_permission(
                "Maintenance Picking", "read", doc=picking
            ):
                continue
            wo_name = frappe.db.get_value(
                "Maintenance Picking", picking, "maintenance_work_order"
            )

        procurement_type = (
            mr.get("custom_maintenance_procurement_type")
            or ("Materials" if picking else "")
        )

        if not wo_name or procurement_type not in (
            "Materials", "External Service"
        ):
            continue

        if not frappe.has_permission(
            "Maintenance Work Order", "read", doc=wo_name
        ):
            continue

        wo = frappe.get_doc("Maintenance Work Order", wo_name)

        if filters.get("work_order") and wo_name != filters.work_order:
            continue
        if filters.get("asset") and wo.asset != filters.asset:
            continue
        if (
            filters.get("procurement_type")
            and procurement_type != filters.procurement_type
        ):
            continue

        if filters.get("from_date") and str(mr.transaction_date) < str(
            filters.from_date
        ):
            continue
        if filters.get("to_date") and str(mr.transaction_date) > str(
            filters.to_date
        ):
            continue

        currency = frappe.get_cached_value(
            "Company", mr.company, "default_currency"
        )

        info = {
            "company": mr.company,
            "work_order": wo_name,
            "asset": wo.asset,
            "procurement_type": procurement_type,
            "material_request": mr.name,
            "currency": currency,
        }

        mr_info[mr.name] = info

        for item in mr.items:
            mr_items[item.name] = (info, item)

    # Only submitted, accessible Purchase Orders enter commitments.
    for name in documents("Purchase Order", conditions):
        po = frappe.get_doc("Purchase Order", name)

        if po.status in ("Closed", "Cancelled"):
            continue

        for item in po.items:
            if item.material_request not in mr_info:
                continue

            item_ref = item.get("material_request_item")
            if item_ref not in mr_items:
                # Never associate a PO line with an unrelated MR row.
                continue

            info, mr_item = mr_items[item_ref]

            if item.material_request != mr_item.parent:
                continue

            qty = flt(item.qty, 6)
            received = min(qty, max(0, flt(item.received_qty, 6)))
            outstanding = max(0, qty - received)

            net_amount = flt(item.base_net_amount, 2)
            net_unit_rate = net_amount / qty if qty else 0

            row = dict(info)
            row.update({
                "purchase_order": po.name,
                "supplier": po.supplier,
                "item_code": item.item_code,
                "uom": item.uom,
                "requested_qty": flt(mr_item.qty, 6),
                "ordered_qty": qty,
                "received_qty": received,
                "to_order": 0,
                "to_receive": outstanding,
                "base_net_amount": net_amount,
                "outstanding_value": flt(
                    outstanding * net_unit_rate, 2
                ),
                "po_status": po.status,
            })

            data.append(row)
            po_lines[item_ref] = po_lines.get(item_ref, 0) + qty

    # Show requisition balances not yet ordered.
    for item_name, (info, item) in mr_items.items():
        requested = flt(item.qty, 6)

        # The standard MR ordered quantity also captures commitments
        # that may not be visible to this user.
        ordered = max(
            flt(item.ordered_qty, 6),
            po_lines.get(item_name, 0),
        )

        outstanding = max(0, requested - ordered)

        if outstanding <= 0.000001:
            continue

        row = dict(info)
        row.update({
            "purchase_order": None,
            "supplier": None,
            "item_code": item.item_code,
            "uom": item.uom,
            "requested_qty": requested,
            "ordered_qty": 0,
            "received_qty": 0,
            "to_order": outstanding,
            "to_receive": 0,
            "base_net_amount": 0,
            "outstanding_value": 0,
            "po_status": "Por Encomendar",
        })
        data.append(row)

    return columns, data
