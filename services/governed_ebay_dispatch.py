"""Governed eBay dispatch execution for MCF orders.

Uses Trading API CompleteSale so an order can be marked shipped immediately
when Amazon accepts the MCF request, then enriched later with carrier/tracking.
No calls occur on import or page load; callers must pass the governed push gate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from xml.etree import ElementTree
import json
import os

import requests

from extensions import db
from models import MarketplaceOrder, Store

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_TRADING_URL = "https://api.ebay.com/ws/api.dll"
EBAY_COMPATIBILITY_LEVEL = "1231"


def _credentials(store: Store) -> dict[str, Any]:
    raw = store.api_key or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}
    return {}


def _access_token(store: Store) -> str:
    creds = _credentials(store)
    refresh_token = creds.get("refresh_token")
    client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("client_id")
    client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError("missing_ebay_credentials_for_dispatch")

    response = requests.post(
        EBAY_TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ebay_dispatch_token_refresh_failed:{response.status_code}:{response.text[:500]}")
    token = (response.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("ebay_dispatch_token_missing")
    return token


def _xml_text(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _tracking_details_xml(
    *,
    carrier: str | None,
    tracking_number: str | None,
    tracking_details: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, str]]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    source = list(tracking_details or [])
    if not source and (carrier or tracking_number):
        source = [{
            "carrier": carrier or "Other",
            "tracking_number": tracking_number or "",
        }]

    for detail in source:
        number = str(
            detail.get("tracking_number")
            or detail.get("trackingNumber")
            or ""
        ).strip()
        if not number or number in seen:
            continue

        seen.add(number)
        normalized.append({
            "carrier": str(
                detail.get("carrier")
                or detail.get("carrierCode")
                or carrier
                or "Other"
            ).strip() or "Other",
            "tracking_number": number,
        })

    if not normalized:
        return "", []

    detail_xml = "".join(
        "<ShipmentTrackingDetails>"
        f"<ShippingCarrierUsed>{_xml_text(detail['carrier'])}</ShippingCarrierUsed>"
        f"<ShipmentTrackingNumber>{_xml_text(detail['tracking_number'])}</ShipmentTrackingNumber>"
        "</ShipmentTrackingDetails>"
        for detail in normalized
    )
    return f"<Shipment>{detail_xml}</Shipment>", normalized


def complete_sale(
    order: MarketplaceOrder,
    *,
    carrier: str | None = None,
    tracking_number: str | None = None,
    tracking_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark an eBay order shipped and optionally send all package tracking.

    This function performs the marketplace call only. The caller is responsible
    for passing the governed runtime guard before invoking it. For split MCF
    shipments, callers pass the complete accumulated package set so eBay keeps
    existing tracking details while adding newly received packages.
    """
    store = order.store
    if store is None or "ebay" not in str(store.platform or "").lower():
        return {"success": False, "error": "source_order_is_not_ebay"}

    token = _access_token(store)
    shipment_xml, normalized_tracking = _tracking_details_xml(
        carrier=carrier,
        tracking_number=tracking_number,
        tracking_details=tracking_details,
    )

    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<CompleteSaleRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<OrderID>{_xml_text(order.marketplace_order_id)}</OrderID>"
        "<Shipped>true</Shipped>"
        f"{shipment_xml}"
        "</CompleteSaleRequest>"
    )

    response = requests.post(
        EBAY_TRADING_URL,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "text/xml",
            "X-EBAY-API-CALL-NAME": "CompleteSale",
            "X-EBAY-API-SITEID": "3",
            "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_COMPATIBILITY_LEVEL,
            "X-EBAY-API-IAF-TOKEN": token,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        return {"success": False, "error": f"ebay_complete_sale_http_{response.status_code}:{response.text[:700]}"}

    try:
        root = ElementTree.fromstring(response.text)
        ack = root.findtext("{urn:ebay:apis:eBLBaseComponents}Ack") or ""
        errors = [
            node.findtext("{urn:ebay:apis:eBLBaseComponents}LongMessage")
            or node.findtext("{urn:ebay:apis:eBLBaseComponents}ShortMessage")
            or "Unknown eBay error"
            for node in root.findall("{urn:ebay:apis:eBLBaseComponents}Errors")
        ]
    except Exception:
        return {"success": False, "error": f"ebay_complete_sale_invalid_xml:{response.text[:700]}"}

    if ack not in {"Success", "Warning"}:
        return {"success": False, "error": "; ".join(errors) or f"ebay_complete_sale_ack_{ack}"}

    order.shipped_at = order.shipped_at or datetime.utcnow()
    primary_tracking = normalized_tracking[0] if normalized_tracking else None
    if primary_tracking:
        order.carrier = primary_tracking["carrier"]
        order.tracking_number = primary_tracking["tracking_number"]
    else:
        if carrier:
            order.carrier = carrier
        if tracking_number:
            order.tracking_number = tracking_number
    order.updated_at = datetime.utcnow()
    db.session.commit()

    return {
        "success": True,
        "ack": ack,
        "warnings": errors if ack == "Warning" else [],
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
        "tracking_details": normalized_tracking,
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
    }
