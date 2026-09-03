"""Restore Ready to dispatch landing and remove broad UI rereads.

This remains a presentation/read alignment over the existing governed workflow.
It adds no worker, poller, marketplace/provider call, stock write or second event
path. The bell reads only when explicitly opened, and an in-session committed
signal must never refetch the whole /fbm HTML document.
"""
from __future__ import annotations

from flask import jsonify, make_response, request
from flask_login import login_required
from sqlalchemy import or_


_BELL_SHIPMENT_LOG_TYPES = {
    "fbm_label_assigned",
    "fbm_marketplace_dispatch_confirmed",
    "fbm_carrier_accepted",
    "fbm_in_transit",
    "fbm_delivered",
}


def _status(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _lifecycle_label(status: str) -> str:
    labels = {
        "pending": "Sale",
        "unshipped": "Sale",
        "order": "Sale",
        "confirmed": "Sale",
        "partially_shipped": "Partially dispatched",
        "shipped": "Dispatched",
        "accepted": "Picked up",
        "carrier_accepted": "Picked up",
        "collected": "Picked up",
        "picked_up": "Picked up",
        "in_transit": "In transit",
        "out_for_delivery": "Out for delivery",
        "delivered": "Delivered",
        "return_requested": "Return requested",
        "returned": "Returned",
        "refund_requested": "Refund requested",
        "refunded": "Refunded",
        "replacement_requested": "Replacement requested",
        "replacement": "Replacement",
        "case_open": "Issue / case",
        "dispute": "Dispute",
        "chargeback": "Chargeback",
        "cancel_requested": "Cancellation requested",
        "cancelled": "Cancelled",
    }
    return labels.get(status, status.replace("_", " ").title() if status else "Sale")


def _align_ready_landing_html(html: str) -> str:
    html = html.replace(
        "var sessionDefaults={tab:'pending',search:'',dirty:false};",
        "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};",
    )
    html = html.replace(
        "(saved.tab&&labels[saved.tab]?saved.tab:'pending')",
        "((saved.tab&&labels[saved.tab]&&saved.tab!=='pending')?saved.tab:'ready_dispatch')",
    )
    html = html.replace(
        "addWorkflowButton(tabBar,'pending','Pending');\n  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');",
        "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');\n  addWorkflowButton(tabBar,'pending','Pending');",
    )

    # The global bell is an explicit shortcut. A normal page open/wake must not
    # hit Neon merely to hydrate the bell; the existing committed signal lights
    # it and the drawer performs the persisted read only when the user opens it.
    html = html.replace("hydrateBellAfterWake();", "stale = true;")
    return html


def _lean_bell_reader():
    """Read only commercial lifecycle fields needed by the visible bell."""
    from extensions import db
    from fbm_models import FBMShipment
    from models import MarketplaceOrder, Store, WarehouseStock

    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 50))
    probe = min(150, max(limit, limit * 3))

    # Select only visible bell columns. Do not joined-load Store credentials,
    # addresses, listing rows, SyncLog audit rows or full Warehouse objects.
    order_rows = (
        db.session.query(
            MarketplaceOrder.id,
            MarketplaceOrder.store_id,
            MarketplaceOrder.marketplace_order_id,
            MarketplaceOrder.marketplace_order_item_id,
            MarketplaceOrder.sku,
            MarketplaceOrder.quantity,
            MarketplaceOrder.status,
            MarketplaceOrder.created_at,
            MarketplaceOrder.updated_at,
            Store.platform,
            WarehouseStock.product_name,
        )
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .outerjoin(WarehouseStock, WarehouseStock.id == MarketplaceOrder.warehouse_stock_id)
        .order_by(MarketplaceOrder.updated_at.desc(), MarketplaceOrder.id.desc())
        .limit(probe)
        .all()
    )

    records = []
    for row in order_rows:
        order_id = str(row.marketplace_order_id or "").strip()
        sku = str(row.sku or "").strip()
        status = _status(row.status) or "confirmed"
        label = _lifecycle_label(status)
        product_title = str(row.product_name or sku or order_id or "Order").strip()
        line_identity = str(row.marketplace_order_item_id or sku or row.id)
        changed_at = row.created_at if label == "Sale" else (row.updated_at or row.created_at)
        records.append({
            "event_key": f"order:{row.store_id}:{order_id}:{line_identity}:{status}",
            "id": f"order:{row.id}",
            "log_type": "marketplace_sale",
            "platform": row.platform or "Marketplace",
            "title": f"{label} · {product_title}",
            "sku": sku,
            "quantity": int(row.quantity or 0),
            "order_id": order_id,
            "lifecycle_status": status,
            "status_label": label,
            "message": f"{label} · {product_title}",
            "created_at": changed_at.isoformat() if changed_at else None,
        })

    shipment_rows = (
        db.session.query(
            FBMShipment.id,
            FBMShipment.marketplace_order_id,
            FBMShipment.carrier,
            FBMShipment.provider,
            FBMShipment.label_purchased_at,
            FBMShipment.marketplace_confirmed_at,
            FBMShipment.carrier_accepted_at,
            FBMShipment.first_movement_at,
            FBMShipment.delivered_at,
            Store.platform,
        )
        .join(Store, Store.id == FBMShipment.store_id)
        .filter(or_(
            FBMShipment.label_purchased_at.isnot(None),
            FBMShipment.marketplace_confirmed_at.isnot(None),
            FBMShipment.carrier_accepted_at.isnot(None),
            FBMShipment.first_movement_at.isnot(None),
            FBMShipment.delivered_at.isnot(None),
        ))
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .limit(probe)
        .all()
    )
    milestones = (
        ("fbm_label_assigned", "Dispatched", "label_purchased_at"),
        ("fbm_marketplace_dispatch_confirmed", "Marketplace dispatch confirmed", "marketplace_confirmed_at"),
        ("fbm_carrier_accepted", "Picked up", "carrier_accepted_at"),
        ("fbm_in_transit", "In transit", "first_movement_at"),
        ("fbm_delivered", "Delivered", "delivered_at"),
    )
    for row in shipment_rows:
        order_id = str(row.marketplace_order_id or "").strip()
        carrier = str(row.carrier or row.provider or "").strip()
        for log_type, label, field in milestones:
            changed_at = getattr(row, field)
            if changed_at is None:
                continue
            records.append({
                "event_key": f"shipment:{row.id}:{log_type}:{changed_at.isoformat()}",
                "id": f"shipment:{row.id}:{log_type}",
                "log_type": log_type,
                "platform": row.platform or "Marketplace",
                "title": label,
                "order_id": order_id,
                "carrier": carrier,
                "shipment_id": row.id,
                "message": f"{label} · Order {order_id}" if order_id else label,
                "created_at": changed_at.isoformat(),
            })

    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    seen = set()
    unique = []
    for record in records:
        log_type = str(record.get("log_type") or "").strip().lower()
        platform = str(record.get("platform") or "").strip().lower()
        order_id = str(record.get("order_id") or "").strip()
        sku = str(record.get("sku") or "").strip()
        quantity = str(record.get("quantity") or "").strip()
        lifecycle = str(record.get("lifecycle_status") or "").strip().lower()
        if log_type == "marketplace_sale":
            key = f"sale:{platform}:{order_id}:{sku}:{quantity}:{lifecycle}"
        elif log_type in _BELL_SHIPMENT_LOG_TYPES:
            key = str(record.get("event_key") or "")
        else:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(record)
        if len(unique) >= limit:
            break

    return jsonify({
        "success": True,
        "records": unique,
        "latest_event_at": unique[0].get("created_at") if unique else None,
    })


def _align_browser_pressure_response(response):
    if response.status_code != 200:
        return response

    content_type = str(response.content_type or "").lower()
    path = request.path.rstrip("/") or "/"

    if "text/html" in content_type:
        response.set_data(_align_ready_landing_html(response.get_data(as_text=True)))
        return response

    if path == "/static/js/fbm_tracking_journey.js" and "javascript" in content_type:
        body = response.get_data(as_text=True)
        body = body.replace(
            "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent);",
            "window.addEventListener('bt38-marketplace-event', function(){ document.documentElement.dataset.bt38FbmCommittedStateDirty='1'; });",
        )
        response.set_data(body)
    return response


def install_governed_fbm_ready_landing_alignment(app) -> None:
    endpoint = "governed_fbm.fbm_page"
    current = app.view_functions.get(endpoint)
    if current is not None and not getattr(current, "_bt38_ready_landing_alignment", False):
        @login_required
        def ready_landing_page():
            response = make_response(current())
            if response.status_code == 200 and "text/html" in str(response.content_type or "").lower():
                response.set_data(_align_ready_landing_html(response.get_data(as_text=True)))
            return response

        ready_landing_page._bt38_ready_landing_alignment = True
        app.view_functions[endpoint] = ready_landing_page

    bell_endpoint = "governed.governed_ui_notifications"
    if bell_endpoint in app.view_functions:
        _lean_bell_reader._bt38_lean_business_bell = True
        app.view_functions[bell_endpoint] = login_required(_lean_bell_reader)

    if not getattr(app, "_bt38_db_pressure_response_alignment", False):
        app.after_request(_align_browser_pressure_response)
        app._bt38_db_pressure_response_alignment = True

    app.logger.info(
        "BT38 UI read pressure aligned: Ready to dispatch first; no committed-event full /fbm reread; bell DB read only on explicit open with lean commercial columns"
    )
