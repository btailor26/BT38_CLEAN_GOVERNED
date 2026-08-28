"""Optional eBay shipment-tracking notification alignment.

ORDER_CONFIRMATION remains the required sale intake. ITEM_MARKED_SHIPPED is a
tracking accelerator: when the seller token has commerce.shipping consent this
module adds the subscription to the existing BT38 webhook destination. Legacy
seller tokens without that new grant remain usable and bounded exact
Fulfillment API readback remains the recovery authority.
"""
from __future__ import annotations

from typing import Any

import requests

from services.governed_ebay_notification_registration import (
    NOTIFICATION_BASE_URL,
    _decode_store_credentials,
    _ensure_subscription,
    _headers,
    _safe_response_payload,
)
from services.governed_ebay_oauth_scopes import EBAY_COMMERCE_SHIPPING_SCOPE


SHIPPING_TOPIC_ID = "ITEM_MARKED_SHIPPED"


def _topic_schema_version(*, access_token: str) -> str:
    response = requests.get(
        f"{NOTIFICATION_BASE_URL}/topic/{SHIPPING_TOPIC_ID}",
        headers=_headers(access_token),
        timeout=30,
    )
    if response.status_code != 200:
        return ""
    payload = _safe_response_payload(response)
    if not isinstance(payload, dict):
        return ""
    supported = payload.get("supportedPayloads") or []
    for row in supported:
        if not isinstance(row, dict):
            continue
        formats = row.get("format") or []
        if isinstance(formats, str):
            formats = [formats]
        if "JSON" not in {str(value).upper() for value in formats}:
            continue
        version = str(row.get("schemaVersion") or "").strip()
        if version:
            return version
    return ""


def ensure_ebay_shipping_notification_alignment(
    *,
    store: Any,
    access_token: str,
    destination_id: str | None,
) -> dict[str, Any]:
    """Add ITEM_MARKED_SHIPPED only when this seller token has the grant."""
    creds = _decode_store_credentials(store)
    granted = set(str(creds.get("oauth_granted_scope") or "").split())
    if EBAY_COMMERCE_SHIPPING_SCOPE not in granted:
        return {
            "success": True,
            "ok": True,
            "enabled": False,
            "topic_id": SHIPPING_TOPIC_ID,
            "authorization_required": True,
            "reauthorization_required": True,
            "reason": "commerce_shipping_scope_not_granted",
            "marketplace_write_started": False,
        }
    if not destination_id:
        return {
            "success": False,
            "ok": False,
            "enabled": False,
            "topic_id": SHIPPING_TOPIC_ID,
            "authorization_required": False,
            "reason": "existing_notification_destination_missing",
            "marketplace_write_started": False,
        }

    schema_version = _topic_schema_version(access_token=access_token)
    if not schema_version:
        return {
            "success": False,
            "ok": False,
            "enabled": False,
            "topic_id": SHIPPING_TOPIC_ID,
            "authorization_required": False,
            "reason": "shipping_topic_schema_unavailable",
            "marketplace_write_started": False,
        }

    subscription_id, created = _ensure_subscription(
        access_token=access_token,
        destination_id=str(destination_id),
        topic_id=SHIPPING_TOPIC_ID,
        schema_version=schema_version,
    )
    return {
        "success": True,
        "ok": True,
        "enabled": True,
        "topic_id": SHIPPING_TOPIC_ID,
        "schema_version": schema_version,
        "subscription_id": subscription_id,
        "subscription_created": created,
        "authorization_required": False,
        "reauthorization_required": False,
        "marketplace_write_started": False,
    }
