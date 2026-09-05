"""Publish exact successful webhook order events to the existing bell event path.

This closes a narrow gap where a sale/order webhook could be committed without
setting stock/page-change flags. In that case the existing webhook after-request
publisher stayed silent and the zero-query bell had nothing to display.

Rules:
- no DB read
- no marketplace/provider read
- no polling
- no second event queue
- no duplicate publish when the existing committed-change predicate already fires
- exact order identity is required
"""
from __future__ import annotations

from flask import g, request


def _exact_order_scope(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("notification_result")
    if not isinstance(result, dict):
        return {}

    order_id = str(
        result.get("order_id")
        or result.get("marketplace_order_id")
        or ""
    ).strip()
    seller_sku = str(
        result.get("seller_sku")
        or result.get("sku")
        or ""
    ).strip()

    # Some exact order import results retain identity one level below the
    # top-level governed result. Read only the already-returned payload.
    order_intake = result.get("order_intake")
    if isinstance(order_intake, dict):
        if not order_id:
            order_id = str(
                order_intake.get("order_id")
                or order_intake.get("marketplace_order_id")
                or ""
            ).strip()
        if not seller_sku:
            seller_sku = str(
                order_intake.get("seller_sku")
                or order_intake.get("sku")
                or ""
            ).strip()

    if not order_id or not seller_sku:
        return {}

    status = str(result.get("status") or "").strip().lower()
    if status in {
        "unresolved",
        "order_import_failed",
        "processing_failed",
        "failed",
        "error",
    }:
        return {}

    scope = {
        "event_type": result.get("event_type") or result.get("business_event") or "order_received",
        "order_id": order_id,
        "seller_sku": seller_sku,
        "store_id": result.get("store_id"),
        "quantity": result.get("quantity"),
        "status": result.get("status"),
        "lifecycle_status": result.get("status"),
        "product_title": result.get("product_title") or result.get("title"),
        "fulfillment_type": result.get("fulfillment_type"),
    }
    return {key: value for key, value in scope.items() if value not in (None, "")}


def install_governed_webhook_bell_event_alignment(app) -> None:
    """Install one fallback publisher for successful exact order webhooks."""
    if getattr(app, "_bt38_webhook_bell_event_alignment_installed", False):
        return

    from services import governed_ui_event_signal as ui

    @app.after_request
    def _publish_exact_webhook_order_if_needed(response):
        path = request.path.rstrip("/") or "/"
        platform = ui._WEBHOOK_PATHS.get(path)
        if request.method != "POST" or not platform:
            return response
        if response.status_code >= 400:
            return response

        payload = response.get_json(silent=True)
        if not isinstance(payload, dict):
            return response
        if payload.get("status") == "processing_failed":
            return response

        # Existing path already publishes when its committed-change predicate
        # succeeds. This fallback only handles successful order events that were
        # committed without stock/page-change flags.
        if ui._response_has_committed_change(payload):
            return response

        scope = _exact_order_scope(payload)
        if not scope:
            return response

        record_id = getattr(g, "bt38_notification_record_id", None)
        if record_id is None:
            record_id = payload.get("notification_record_id")
        if record_id is None:
            return response

        ui.publish_webhook_ui_event(
            platform=platform,
            notification_record_id=int(record_id),
            scope=scope,
        )
        return response

    app._bt38_webhook_bell_event_alignment_installed = True
    app.logger.info(
        "BT38 webhook bell event alignment installed: exact successful order events publish even when stock is unchanged; zero DB/API bell reads"
    )
