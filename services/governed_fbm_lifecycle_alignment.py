"""Align existing marketplace lifecycle truth with FBM and the notification bell.

This module does not create another order, shipment, worker, poller or marketplace
write path. MarketplaceOrder remains the persisted marketplace authority. An
FBMShipment may override marketplace carrier/journey facts only when its existing
purchase_key proves BT38 created that shipment.
"""
from __future__ import annotations

import os
from datetime import datetime
from html import escape
from types import SimpleNamespace

from flask import jsonify
from flask_login import login_required
from sqlalchemy import tuple_


_AMAZON_BUY_SHIPPING_APPROVAL_ENV = "AMAZON_BUY_SHIPPING_APPROVED"

_PICKUP_STATES = {
    "accepted",
    "carrier_accepted",
    "collected",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
}
_MOVEMENT_STATES = {"in_transit", "out_for_delivery", "delivered"}
_TERMINAL_ISSUE_STATES = {
    "cancel_requested",
    "cancelled",
    "returned",
    "refunded",
    "case_open",
    "dispute",
    "chargeback",
}


def _status(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _amazon_buy_shipping_approved() -> bool:
    return _status(os.getenv(_AMAZON_BUY_SHIPPING_APPROVAL_ENV)) in {
        "1",
        "true",
        "yes",
        "approved",
        "enabled",
    }


def bt38_owns_shipment(shipment) -> bool:
    """Use the existing deterministic purchase_key as shipment ownership proof."""
    if shipment is None:
        return False
    provider = _status(getattr(shipment, "provider", None))
    purchase_key = str(getattr(shipment, "purchase_key", None) or "").strip().lower()
    if provider == "packlink":
        return purchase_key.startswith("packlink_")
    if provider == "amazon_buy_shipping":
        return purchase_key.startswith("amazon_buy_shipping:")
    if provider == "manual":
        return purchase_key.startswith("manual:")
    return False


def _marketplace_proxy(order):
    """Expose persisted MarketplaceOrder shipment facts through the existing view contract."""
    status = _status(getattr(order, "status", None))
    tracking = str(getattr(order, "tracking_number", None) or "").strip() or None
    carrier = str(getattr(order, "carrier", None) or "").strip() or None
    shipped_at = getattr(order, "shipped_at", None)
    if not any((tracking, carrier, shipped_at, status in _PICKUP_STATES | _MOVEMENT_STATES | {"shipped", "partially_shipped"})):
        return None

    changed_at = getattr(order, "updated_at", None) or shipped_at or getattr(order, "created_at", None)
    return SimpleNamespace(
        id=None,
        provider="marketplace",
        provider_shipment_id=None,
        provider_carrier_id=None,
        provider_service_id=None,
        purchase_key=None,
        purchase_status=None,
        carrier=carrier,
        service=None,
        tracking_number=tracking,
        label_url=None,
        label_format=None,
        label_purchased_at=(shipped_at or changed_at) if (tracking or shipped_at) else None,
        handover_due_at=None,
        carrier_accepted_at=changed_at if status in _PICKUP_STATES else None,
        first_movement_at=changed_at if status in _MOVEMENT_STATES else None,
        delivered_at=changed_at if status == "delivered" else None,
        status=status or "marketplace",
        marketplace_confirmed_at=shipped_at,
        marketplace_confirmation_status="marketplace_authoritative",
        mapping_review=None,
        provider_cases=[],
        _bt38_marketplace_owned=True,
    )


def _lifecycle_label(status: str) -> str:
    labels = {
        "pending": "Pending",
        "unshipped": "Confirmed",
        "order": "Confirmed",
        "confirmed": "Confirmed",
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
    return labels.get(status, status.replace("_", " ").title() if status else "Confirmed")


def _patch_fbm_page_module() -> None:
    import services.governed_fbm_page_alignment as page

    if getattr(page, "_bt38_marketplace_lifecycle_patched", False):
        return

    original_eligible = page._workspace_fbm_eligible
    original_shipment_map = page._shipment_map
    original_shipping_mode = page._workspace_shipping_mode
    original_provider_options = page._workspace_provider_options
    original_render_template = page.render_template

    def aligned_eligible(row, profile=None):
        if not original_eligible(row, profile):
            return False
        if page._platform(row).strip().lower() == "amazon" and _status(getattr(row, "status", None)) == "pending":
            return False
        return True

    def aligned_shipment_map(rows):
        existing = original_shipment_map(rows)
        result = {}
        for row in rows:
            if row.store_id is None or not row.marketplace_order_id:
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            shipment = existing.get(key)
            if bt38_owns_shipment(shipment):
                result[key] = shipment
                continue
            marketplace = _marketplace_proxy(row)
            if marketplace is not None:
                result[key] = marketplace
        return result

    def aligned_shipping_mode(row, platform, profile):
        mode = dict(original_shipping_mode(row, platform, profile))
        normalized = str(platform or "").strip().lower()
        status = _status(getattr(row, "status", None))
        if normalized == "amazon" and not _amazon_buy_shipping_approved():
            mode["marketplace_buy_shipping"] = False
            mode["recommended"] = "Packlink / connected carrier" if not mode.get("prime_locked") else "Amazon Buy Shipping pending approval"
            mode["reason"] = (
                "Amazon Buy Shipping is capability-gated until production approval is confirmed. Existing Amazon order/tracking reads remain available."
            )
        if status in _TERMINAL_ISSUE_STATES:
            mode["marketplace_buy_shipping"] = False
            mode["external_provider"] = False
            mode["manual"] = False
            mode["recommended"] = _lifecycle_label(status)
            mode["reason"] = "This marketplace lifecycle state is persisted for visibility; new postage actions are held."
        return mode

    def aligned_provider_options(row, profile):
        options = [dict(option) for option in original_provider_options(row, profile)]
        status = _status(getattr(row, "status", None))
        for option in options:
            provider = _status(option.get("provider"))
            if provider == "amazon_buy_shipping" and not _amazon_buy_shipping_approved():
                option["available"] = False
                option["recommended"] = False
                option["message"] = "Amazon Buy Shipping is pending production approval. Marketplace tracking/readback remains available."
            if status in _TERMINAL_ISSUE_STATES:
                option["available"] = False
                option["recommended"] = False
                option["message"] = f"{_lifecycle_label(status)}: new shipping actions are held for this order."
        return options

    def aligned_render_template(template_name, *args, **kwargs):
        html = original_render_template(template_name, *args, **kwargs)
        if template_name != "fbm.html":
            return html
        for item in list(kwargs.get("orders") or []):
            order = item.get("order") if isinstance(item, dict) else None
            if order is None or getattr(order, "id", None) is None:
                continue
            status = _status(getattr(order, "status", None)) or "confirmed"
            marker = f'<tr class="fbm-order-row" data-order-id="{int(order.id)}">'
            replacement = (
                f'<tr class="fbm-order-row" data-order-id="{int(order.id)}" '
                f'data-lifecycle-status="{escape(status, quote=True)}" '
                'data-order-authority="marketplace_order">'
            )
            html = html.replace(marker, replacement, 1)
        return html

    page._workspace_fbm_eligible = aligned_eligible
    page._shipment_map = aligned_shipment_map
    page._workspace_shipping_mode = aligned_shipping_mode
    page._workspace_provider_options = aligned_provider_options
    page.render_template = aligned_render_template
    page._bt38_marketplace_lifecycle_patched = True


def _patch_webhook_lifecycle() -> None:
    import services.governed_webhook_execution as execution

    if getattr(execution, "_bt38_marketplace_lifecycle_patched", False):
        return

    original_extract = execution._extract_order_lifecycle_values

    def aligned_extract(payload, *, business_event=None):
        values = dict(original_extract(payload, business_event=business_event))
        flattened = " ".join(str(value).lower() for value in execution._flatten_values(payload))
        raw = str(values.get("raw_status") or "").strip().upper().replace("_", "").replace(" ", "")
        inferred = None

        exact = {
            "PICKEDUP": "picked_up",
            "COLLECTED": "picked_up",
            "CARRIERACCEPTED": "picked_up",
            "ACCEPTED": "picked_up",
            "INTRANSIT": "in_transit",
            "OUTFORDELIVERY": "out_for_delivery",
            "DELIVERED": "delivered",
            "RETURNREQUESTED": "return_requested",
            "RETURNED": "returned",
            "REFUNDREQUESTED": "refund_requested",
            "REFUNDED": "refunded",
            "REPLACEMENTREQUESTED": "replacement_requested",
            "REPLACEMENT": "replacement",
        }
        inferred = exact.get(raw)

        if inferred is None and business_event == "tracking":
            if any(token in flattened for token in ("out for delivery", "out_for_delivery")):
                inferred = "out_for_delivery"
            elif any(token in flattened for token in ("in transit", "in_transit")):
                inferred = "in_transit"
            elif any(token in flattened for token in ("picked up", "picked_up", "collected", "carrier accepted", "received by carrier")):
                inferred = "picked_up"
        if business_event == "delivery":
            inferred = "delivered"
        elif business_event == "return":
            if "replacement" in flattened:
                inferred = "replacement_requested" if "request" in flattened else "replacement"
            elif "refund" in flattened:
                inferred = "refund_requested" if "request" in flattened else "refunded"
            elif "returned" in flattened or "return complete" in flattened:
                inferred = "returned"
            else:
                inferred = "return_requested"
        elif business_event == "case":
            if "chargeback" in flattened:
                inferred = "chargeback"
            elif "dispute" in flattened:
                inferred = "dispute"
            else:
                inferred = "case_open"

        if inferred:
            values["recognized"] = True
            values["status"] = inferred
            values["terminal"] = True
            changed_at = values.get("changed_at") or datetime.utcnow()
            if inferred in _PICKUP_STATES | _MOVEMENT_STATES | {"shipped", "partially_shipped"}:
                values["shipped_at"] = values.get("shipped_at") or changed_at
        return values

    execution._extract_order_lifecycle_values = aligned_extract
    execution._bt38_marketplace_lifecycle_patched = True


def _wrap_provider_routes(app) -> None:
    if getattr(app, "_bt38_fbm_provider_authority_wrapped", False):
        return

    from extensions import db
    from fbm_models import FBMShipment

    packlink_endpoint = "governed_fbm.packlink_shipment_status"
    if packlink_endpoint in app.view_functions:
        original_packlink_status = app.view_functions[packlink_endpoint]

        @login_required
        def guarded_packlink_status(shipment_id: int):
            shipment = db.session.get(FBMShipment, shipment_id)
            if not bt38_owns_shipment(shipment) or _status(getattr(shipment, "provider", None)) != "packlink":
                return jsonify({
                    "success": False,
                    "message": "This shipment is marketplace-authoritative; BT38 will not query the Packlink provider path for it.",
                }), 409
            return original_packlink_status(shipment_id)

        app.view_functions[packlink_endpoint] = guarded_packlink_status

    for endpoint in (
        "governed_fbm.amazon_rates",
        "governed_fbm.amazon_purchase",
    ):
        if endpoint not in app.view_functions:
            continue
        original = app.view_functions[endpoint]

        @login_required
        def guarded_amazon_action(order_id: int, _original=original):
            if not _amazon_buy_shipping_approved():
                return jsonify({
                    "success": False,
                    "message": "Amazon Buy Shipping is pending production approval. No rate/purchase action was attempted.",
                }), 409
            return _original(order_id)

        app.view_functions[endpoint] = guarded_amazon_action

    app._bt38_fbm_provider_authority_wrapped = True


def _wrap_notification_bell(app) -> None:
    if getattr(app, "_bt38_marketplace_bell_lifecycle_wrapped", False):
        return

    endpoint = "governed.governed_ui_notifications"
    if endpoint not in app.view_functions:
        return

    original = app.view_functions[endpoint]

    @login_required
    def lifecycle_notifications():
        response = original()
        if isinstance(response, tuple):
            return response
        payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return response

        records = list(payload.get("records") or [])
        identities = []
        for record in records:
            key = str(record.get("event_key") or "")
            if record.get("log_type") != "marketplace_sale" or not key.startswith("order:"):
                continue
            parts = key.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                store_id = int(parts[1])
            except (TypeError, ValueError):
                continue
            order_id = parts[2]
            identities.append((store_id, order_id))

        latest_by_key = {}
        if identities:
            from extensions import db
            from models import MarketplaceOrder

            rows = (
                db.session.query(MarketplaceOrder)
                .filter(tuple_(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id).in_(sorted(set(identities))))
                .order_by(MarketplaceOrder.updated_at.desc(), MarketplaceOrder.id.desc())
                .all()
            )
            for row in rows:
                key = (int(row.store_id), str(row.marketplace_order_id))
                if key not in latest_by_key:
                    latest_by_key[key] = row

        for record in records:
            key = str(record.get("event_key") or "")
            if record.get("log_type") != "marketplace_sale" or not key.startswith("order:"):
                continue
            parts = key.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                store_id = int(parts[1])
            except (TypeError, ValueError):
                continue
            order_id = parts[2]
            line_identity = parts[3]
            row = latest_by_key.get((store_id, order_id))
            if row is None:
                continue
            status = _status(getattr(row, "status", None)) or "confirmed"
            label = _lifecycle_label(status)
            product_title = str(record.get("title") or record.get("sku") or order_id).strip()
            record["lifecycle_status"] = status
            record["status_label"] = label
            record["title"] = f"{label} · {product_title}"
            record["message"] = record["title"]
            record["event_key"] = f"order:{store_id}:{order_id}:{line_identity}:{status}"
            updated_at = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
            if updated_at is not None:
                record["created_at"] = updated_at.isoformat()

        records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        payload["records"] = records
        payload["latest_event_at"] = records[0].get("created_at") if records else None
        return jsonify(payload)

    app.view_functions[endpoint] = lifecycle_notifications
    app._bt38_marketplace_bell_lifecycle_wrapped = True


def install_governed_fbm_lifecycle_alignment(app) -> None:
    """Install one DB-first alignment over the already-registered governed paths."""
    if getattr(app, "_bt38_fbm_lifecycle_alignment_installed", False):
        return

    _patch_webhook_lifecycle()
    _patch_fbm_page_module()
    _wrap_provider_routes(app)
    _wrap_notification_bell(app)

    app._bt38_fbm_lifecycle_alignment_installed = True
    app.logger.info(
        "BT38 FBM lifecycle alignment installed: MarketplaceOrder DB truth -> FBM/bell, ownership-gated provider reads, Amazon pending/Buy Shipping gates"
    )
