"""Align eBay Notification API line identity before governed order intake.

ORDER_CONFIRMATION supplies orderLineItemId and listingId as separate facts. The
existing generic webhook importer recognizes lineItemId/orderItemId but not
orderLineItemId, so it can fall back to MarketplaceListing.external_listing_id
and create a second sparse MarketplaceOrder row keyed by the listing ID.

This wrapper copies only eBay's explicit orderLineItemId into the already
supported marketplace_order_item_id field before the existing governed executor
runs. It creates no order/import path, makes no marketplace read/write and does
not touch stock itself.
"""
from __future__ import annotations

from typing import Any

import services.governed_webhook_execution as _execution


_ORIGINAL = _execution.process_marketplace_notification
_INSTALLED = False


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if str(current_key).lower() == str(key).lower() and current_value not in (None, ""):
                return current_value
            found = _deep_get(current_value, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for current_value in value:
            found = _deep_get(current_value, key)
            if found not in (None, ""):
                return found
    return None


def _aligned_process_marketplace_notification(
    *,
    marketplace: str,
    payload: dict,
    actor: str = "marketplace_webhook",
    notification_record_id: int | None = None,
):
    aligned_payload = dict(payload or {})
    if str(marketplace or aligned_payload.get("marketplace") or "").strip().lower() == "ebay":
        explicit_line_id = _deep_get(aligned_payload, "orderLineItemId")
        existing_line_id = (
            aligned_payload.get("marketplace_order_item_id")
            or aligned_payload.get("order_item_id")
            or aligned_payload.get("orderItemId")
            or aligned_payload.get("line_item_id")
            or aligned_payload.get("lineItemId")
        )
        if explicit_line_id not in (None, "") and existing_line_id in (None, ""):
            aligned_payload["marketplace_order_item_id"] = str(explicit_line_id).strip()

    return _ORIGINAL(
        marketplace=marketplace,
        payload=aligned_payload,
        actor=actor,
        notification_record_id=notification_record_id,
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _execution.process_marketplace_notification = _aligned_process_marketplace_notification
    _INSTALLED = True


install()
