"""Optional eBay shipment-tracking notification alignment.

ORDER_CONFIRMATION remains the required sale intake. ITEM_MARKED_SHIPPED is a
tracking accelerator: this module probes the current seller token against the
shipping topic and, when permitted, adds the subscription to BT38's existing
webhook destination. A token without commerce.shipping consent remains usable;
bounded Fulfillment API readback stays the recovery authority.
"""
from __future__ import annotations

from typing import Any

import requests

from services.governed_ebay_notification_registration import (
    NOTIFICATION_BASE_URL,
    _ensure_subscription,
    _headers,
    _safe_response_payload,
)


SHIPPING_TOPIC_ID = "ITEM_MARKED_SHIPPED"


def _topic_probe(*, access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{NOTIFICATION_BASE_URL}/topic/{SHIPPING_TOPIC_ID}",
        headers=_headers(access_token),
        timeout=30,
    )
    if response.status_code in {401, 403}:
        return {
            "ok": False,
            "authorization_required": True,
            "status_code": response.status_code,
            "schema_version": "",
        }
    if response.status_code != 200:
        return {
            "ok": False,
            "authorization_required": False,
            "status_code": response.status_code,
            "schema_version": "",
        }

    payload = _safe_response_payload(response)
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "authorization_required": False,
            "status_code": response.status_code,
            "schema_version": "",
        }

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
            return {
                "ok": True,
                "authorization_required": False,
                "status_code": response.status_code,
                "schema_version": version,
            }

    return {
        "ok": False,
        "authorization_required": False,
        "status_code": response.status_code,
        "schema_version": "",
    }


def ensure_ebay_shipping_notification_alignment(
    *,
    store: Any,
    access_token: str,
    destination_id: str | None,
) -> dict[str, Any]:
    """Subscribe to ITEM_MARKED_SHIPPED when the current seller token permits it."""
    del store  # kept in the public contract for store-scoped callers/auditing

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

    probe = _topic_probe(access_token=access_token)
    if probe.get("authorization_required"):
        return {
            "success": True,
            "ok": True,
            "enabled": False,
            "topic_id": SHIPPING_TOPIC_ID,
            "authorization_required": True,
            "reauthorization_required": True,
            "reason": "commerce_shipping_scope_not_granted",
            "status_code": probe.get("status_code"),
            "marketplace_write_started": False,
        }

    schema_version = str(probe.get("schema_version") or "").strip()
    if not probe.get("ok") or not schema_version:
        return {
            "success": False,
            "ok": False,
            "enabled": False,
            "topic_id": SHIPPING_TOPIC_ID,
            "authorization_required": False,
            "reason": "shipping_topic_schema_unavailable",
            "status_code": probe.get("status_code"),
            "marketplace_write_started": False,
        }

    try:
        subscription_id, created = _ensure_subscription(
            access_token=access_token,
            destination_id=str(destination_id),
            topic_id=SHIPPING_TOPIC_ID,
            schema_version=schema_version,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 401" in message or "HTTP 403" in message or "Insufficient permissions" in message:
            return {
                "success": True,
                "ok": True,
                "enabled": False,
                "topic_id": SHIPPING_TOPIC_ID,
                "authorization_required": True,
                "reauthorization_required": True,
                "reason": "commerce_shipping_scope_not_granted",
                "error": message,
                "marketplace_write_started": False,
            }
        raise

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
