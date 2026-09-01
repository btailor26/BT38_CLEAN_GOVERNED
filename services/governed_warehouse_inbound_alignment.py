"""Governed Warehouse inbound alignment.

Wire existing WarehouseStock, PurchaseOrder and barcode identities without
restoring retired PO routes or the legacy mobile stock writer.  This stage is
read-only: expected inbound and scan resolution are exposed first; inventory
mutation remains deliberately disabled until the confirmation transaction is
wired and tested.
"""

from flask import jsonify
from flask_login import login_required


def _active_stock_query(WarehouseStock):
    return (
        WarehouseStock.query
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
    )


def _authority_stock(stock, WarehouseStock):
    """Resolve one physical Warehouse authority for a scanned member."""
    group_id = getattr(stock, "master_product_group_id", None)
    if not group_id:
        return stock

    group_rows = (
        _active_stock_query(WarehouseStock)
        .filter(WarehouseStock.master_product_group_id == group_id)
        .order_by(WarehouseStock.id.asc())
        .all()
    )
    if not group_rows:
        return stock
    return group_rows[0]


def install_governed_warehouse_inbound_alignment(app):
    """Install read-only inbound visibility and barcode identity resolution."""
    expected_endpoint = "governed_warehouse_expected_inbound"
    scan_endpoint = "governed_warehouse_scan_resolve"

    if expected_endpoint not in app.view_functions:
        @app.get(
            "/governed/warehouse/expected-inbound",
            endpoint=expected_endpoint,
        )
        @login_required
        def governed_warehouse_expected_inbound():
            from models import PurchaseOrder, WarehouseStock

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
                        _active_stock_query(WarehouseStock)
                        .filter(WarehouseStock.sku == item.sku)
                        .order_by(WarehouseStock.id.asc())
                        .first()
                    )
                    authority = (
                        _authority_stock(stock, WarehouseStock)
                        if stock is not None
                        else None
                    )

                    rows.append({
                        "purchase_order_id": order.id,
                        "purchase_order_item_id": getattr(item, "id", None),
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
                        "matched_warehouse_stock_id": getattr(stock, "id", None),
                        "warehouse_stock_id": getattr(authority, "id", None),
                        "master_product_group_id": getattr(
                            authority, "master_product_group_id", None
                        ),
                        "warehouse_on_order_quantity": int(
                            getattr(authority, "on_order_quantity", 0) or 0
                        ) if authority else None,
                        "location": getattr(authority, "location", None) if authority else None,
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

    if scan_endpoint not in app.view_functions:
        @app.get(
            "/governed/warehouse/scan/<path:identity>",
            endpoint=scan_endpoint,
        )
        @login_required
        def governed_warehouse_scan_resolve(identity):
            """Resolve SKU/EAN/UPC/FNSKU/carton identity without changing stock."""
            from models import (
                AmazonFBAListing,
                MarketplaceListing,
                ProductPackMapping,
                WarehouseStock,
            )

            value = str(identity or "").strip()
            if not value:
                return jsonify({
                    "success": False,
                    "ok": False,
                    "governed": True,
                    "read_only": True,
                    "error": "missing_identity",
                }), 400

            stock = (
                _active_stock_query(WarehouseStock)
                .filter(
                    (WarehouseStock.sku == value)
                    | (WarehouseStock.barcode == value)
                )
                .order_by(WarehouseStock.id.asc())
                .first()
            )
            identity_type = None
            units_per_scan = 1

            if stock is not None:
                identity_type = (
                    "product_barcode"
                    if str(getattr(stock, "barcode", "") or "") == value
                    else "sku"
                )

            if stock is None:
                listing = (
                    MarketplaceListing.query
                    .filter(
                        (MarketplaceListing.external_sku == value)
                        | (MarketplaceListing.barcode == value)
                        | (MarketplaceListing.fnsku == value)
                    )
                    .order_by(MarketplaceListing.id.asc())
                    .first()
                )
                if listing is not None and getattr(listing, "warehouse_stock_id", None):
                    stock = _active_stock_query(WarehouseStock).filter(
                        WarehouseStock.id == listing.warehouse_stock_id
                    ).first()
                    if str(getattr(listing, "fnsku", "") or "") == value:
                        identity_type = "fnsku"
                    elif str(getattr(listing, "barcode", "") or "") == value:
                        identity_type = "marketplace_barcode"
                    else:
                        identity_type = "marketplace_sku"

            if stock is None:
                fba = (
                    AmazonFBAListing.query
                    .filter(AmazonFBAListing.fnsku == value)
                    .order_by(AmazonFBAListing.id.asc())
                    .first()
                )
                if fba is not None:
                    seller_sku = str(getattr(fba, "seller_sku", "") or "").strip()
                    if seller_sku:
                        stock = (
                            _active_stock_query(WarehouseStock)
                            .filter(WarehouseStock.sku == seller_sku)
                            .order_by(WarehouseStock.id.asc())
                            .first()
                        )
                        if stock is not None:
                            identity_type = "fnsku"

            if stock is None:
                pack = (
                    ProductPackMapping.query
                    .filter(
                        (ProductPackMapping.master_barcode == value)
                        | (ProductPackMapping.single_barcode == value)
                    )
                    .filter(ProductPackMapping.is_active == True)  # noqa: E712
                    .order_by(ProductPackMapping.id.asc())
                    .first()
                )
                if pack is not None:
                    stock = (
                        _active_stock_query(WarehouseStock)
                        .filter(WarehouseStock.sku == pack.single_sku)
                        .order_by(WarehouseStock.id.asc())
                        .first()
                    )
                    if stock is not None:
                        if str(getattr(pack, "master_barcode", "") or "") == value:
                            identity_type = "master_carton"
                            units_per_scan = max(
                                1, int(getattr(pack, "units_per_carton", 1) or 1)
                            )
                        else:
                            identity_type = "pack_product_barcode"

            if stock is None:
                return jsonify({
                    "success": False,
                    "ok": False,
                    "governed": True,
                    "read_only": True,
                    "error": "identity_not_found",
                    "identity": value,
                }), 404

            authority = _authority_stock(stock, WarehouseStock)
            return jsonify({
                "success": True,
                "ok": True,
                "governed": True,
                "read_only": True,
                "identity": value,
                "identity_type": identity_type,
                "units_per_scan": units_per_scan,
                "matched_warehouse_stock_id": stock.id,
                "warehouse_stock_id": authority.id,
                "master_product_group_id": getattr(
                    authority, "master_product_group_id", None
                ),
                "sku": authority.sku,
                "product_name": authority.product_name,
                "location": authority.location,
                "available_quantity": int(
                    getattr(authority, "available_quantity", 0) or 0
                ),
                "on_order_quantity": int(
                    getattr(authority, "on_order_quantity", 0) or 0
                ),
                "message": "Identity resolved. No stock has been changed.",
            }), 200
