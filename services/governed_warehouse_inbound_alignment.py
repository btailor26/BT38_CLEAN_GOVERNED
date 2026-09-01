"""Governed Warehouse inbound alignment.

This module intentionally wires the existing WarehouseStock / PurchaseOrder /
WarehouseReceipt authorities without restoring retired routes or mobile stock
writes.  Phase 1 is read-only: expose the existing expected-inbound state to
the governed Warehouse surface before any receiving mutation is enabled.
"""

from flask import jsonify
from flask_login import login_required


def install_governed_warehouse_inbound_alignment(app):
    """Install read-only Warehouse inbound visibility on the existing app."""
    endpoint = "governed_warehouse_expected_inbound"
    rule = "/governed/warehouse/expected-inbound"

    if endpoint in app.view_functions:
        return

    @app.get(rule, endpoint=endpoint)
    @login_required
    def governed_warehouse_expected_inbound():
        from models import PurchaseOrder, PurchaseOrderItem, WarehouseStock

        orders = (
            PurchaseOrder.query
            .filter(PurchaseOrder.status.in_(["sent", "partially_received"]))
            .order_by(PurchaseOrder.expected_date.asc(), PurchaseOrder.id.asc())
            .all()
        )

        rows = []
        for order in orders:
            for item in list(getattr(order, "items", None) or []):
                ordered = int(getattr(item, "ordered_quantity", 0) or 0)
                received = int(getattr(item, "received_quantity", 0) or 0)
                damaged = int(getattr(item, "damaged_quantity", 0) or 0)
                remaining = max(0, ordered - received - damaged)
                if remaining <= 0:
                    continue

                stock = (
                    WarehouseStock.query
                    .filter(WarehouseStock.sku == item.sku)
                    .filter(WarehouseStock.is_active == True)  # noqa: E712
                    .filter(WarehouseStock.is_deleted == False)  # noqa: E712
                    .order_by(WarehouseStock.id.asc())
                    .first()
                )

                rows.append({
                    "purchase_order_id": order.id,
                    "po_number": order.po_number,
                    "status": order.status,
                    "expected_date": (
                        order.expected_date.isoformat()
                        if getattr(order, "expected_date", None)
                        else None
                    ),
                    "sku": item.sku,
                    "product_name": item.product_name,
                    "ordered_quantity": ordered,
                    "received_quantity": received,
                    "damaged_quantity": damaged,
                    "remaining_quantity": remaining,
                    "unit_cost": float(getattr(item, "unit_cost", 0) or 0),
                    "warehouse_stock_id": getattr(stock, "id", None),
                    "master_product_group_id": getattr(stock, "master_product_group_id", None),
                    "warehouse_on_order_quantity": int(
                        getattr(stock, "on_order_quantity", 0) or 0
                    ) if stock else None,
                    "location": getattr(stock, "location", None) if stock else None,
                })

        return jsonify({
            "success": True,
            "ok": True,
            "governed": True,
            "read_only": True,
            "truth_sources": [
                "PurchaseOrder",
                "PurchaseOrderItem",
                "WarehouseStock",
            ],
            "count": len(rows),
            "expected_inbound": rows,
        }), 200
