"""Read Amazon-owned FBM package tracking into existing MarketplaceOrder rows.

Orders API v2026-01-01 exposes FBM package tracking through includedData=PACKAGES.
This helper is read-only against Amazon and updates existing merchant-fulfilled
BT38 order rows from Amazon's current marketplace package truth. It does not
create orders, mutate inventory, buy postage, confirm shipment, start a scheduler,
or touch MCF.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from amazon_service_live_patch import _load_credentials
from extensions import db
from models import MarketplaceOrder


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_EU_ENDPOINT = "https://sellingpartnerapi-eu.amazon.com"

_PACKAGE_LIFECYCLE = {
    "SHIPPED": "shipped",
    "PICKEDUPBYCARRIER": "picked_up",
    "CHECKEDINTOCARRIERHUB": "in_transit",
    "INTRANSIT": "in_transit",
    "OUTFORDELIVERY": "out_for_delivery",
    "DELIVERED": "delivered",
}
_JOURNEY_RANK = {
    "shipped": 0,
    "picked_up": 1,
    "accepted": 1,
    "carrier_accepted": 1,
    "collected": 1,
    "in_transit": 2,
    "out_for_delivery": 3,
    "delivered": 4,
}
_PROTECTED_ISSUE_STATES = {
    "cancel_requested",
    "cancelled",
    "return_requested",
    "returned",
    "refund_requested",
    "refunded",
    "replacement_requested",
    "replacement",
    "case_open",
    "dispute",
    "chargeback",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_key(value: Any) -> str:
    return "".join(ch for ch in _text(value).upper() if ch.isalnum())


def _parse_iso(value: Any) -> datetime | None:
    text_value = _text(value)
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _package_lifecycle(package: dict[str, Any]) -> tuple[str | None, str | None]:
    package_status = package.get("packageStatus")
    if isinstance(package_status, dict):
        raw_status = _text(package_status.get("status"))
        detailed_status = _text(package_status.get("detailedStatus")) or None
    else:
        raw_status = _text(package_status)
        detailed_status = None
    return _PACKAGE_LIFECYCLE.get(_status_key(raw_status)), detailed_status


def _can_advance_lifecycle(current: Any, incoming: str | None) -> bool:
    incoming_value = _text(incoming).lower()
    current_value = _text(current).lower().replace("-", "_").replace(" ", "_")
    if not incoming_value or current_value == incoming_value:
        return False
    if current_value in _PROTECTED_ISSUE_STATES:
        return False
    incoming_rank = _JOURNEY_RANK.get(incoming_value)
    if incoming_rank is None:
        return False
    current_rank = _JOURNEY_RANK.get(current_value)
    return current_rank is None or incoming_rank >= current_rank


def _lwa_access_token(store: Any) -> str:
    credentials = _load_credentials(store)
    if not credentials.get("ok"):
        raise RuntimeError(str(credentials.get("reason") or "amazon_credentials_missing"))

    response = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": credentials["refresh_token"],
            "client_id": credentials["lwa_app_id"],
            "client_secret": credentials["lwa_client_secret"],
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"amazon_lwa_token_failed:{response.status_code}:{response.text[:500]}"
        )
    token = _text((response.json() or {}).get("access_token"))
    if not token:
        raise RuntimeError("amazon_lwa_token_missing_access_token")
    return token


def _package_truth(order_payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    packages = [row for row in (order_payload.get("packages") or []) if isinstance(row, dict)]
    tracked = [row for row in packages if _text(row.get("trackingNumber"))]
    if not tracked:
        return None, None

    unique_tracking = {_text(row.get("trackingNumber")) for row in tracked}
    if len(unique_tracking) != 1:
        # MarketplaceOrder currently has one scalar tracking field. Do not lose
        # package identity by collapsing a genuine multi-package Amazon order.
        return None, "multiple_amazon_packages_require_multi_tracking_storage"

    package = tracked[0]
    lifecycle_status, detailed_status = _package_lifecycle(package)
    package_status = package.get("packageStatus")
    raw_package_status = (
        _text(package_status.get("status"))
        if isinstance(package_status, dict)
        else _text(package_status)
    )
    return {
        "carrier": _text(package.get("carrier")) or None,
        "tracking_number": _text(package.get("trackingNumber")) or None,
        "shipped_at": _parse_iso(package.get("shipTime") or package.get("createdTime")),
        "package_reference_id": _text(package.get("packageReferenceId")) or None,
        "package_status": raw_package_status or None,
        "package_detailed_status": detailed_status,
        "lifecycle_status": lifecycle_status,
    }, None


def hydrate_amazon_tracking_for_order(
    *,
    store: Any,
    marketplace_order_id: str,
    source: str = "amazon_orders_2026_packages",
) -> dict[str, Any]:
    """Persist current Amazon package truth for one existing Amazon FBM order."""
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "amazon_order_id_missing"}

    rows = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store.id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .order_by(MarketplaceOrder.id)
        .all()
    )
    eligible = [
        row for row in rows
        if _text(getattr(row, "fulfillment_type", "")).upper() not in {"FBA", "AFN", "MCF"}
        and not _text(getattr(row, "status", "")).lower().startswith("mcf_")
    ]
    if not eligible:
        return {
            "success": True,
            "skipped": True,
            "reason": "existing_amazon_fbm_order_missing",
            "order_id": order_id,
            "marketplace_write_started": False,
        }

    access_token = _lwa_access_token(store)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    response = requests.get(
        f"{SP_API_EU_ENDPOINT}/orders/2026-01-01/orders/{quote(order_id, safe='')}",
        params={"includedData": "PACKAGES"},
        headers={
            "Accept": "application/json",
            "x-amz-access-token": access_token,
            "x-amz-date": now,
            "user-agent": "BT38/1.0 (Language=Python)",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        return {
            "success": False,
            "skipped": False,
            "reason": "amazon_package_tracking_read_failed",
            "status_code": response.status_code,
            "error": response.text[:1000],
            "order_id": order_id,
            "marketplace_write_started": False,
        }

    payload = response.json() or {}
    order_payload = payload.get("order") if isinstance(payload.get("order"), dict) else payload
    if not isinstance(order_payload, dict):
        return {
            "success": False,
            "skipped": False,
            "reason": "amazon_package_tracking_payload_invalid",
            "order_id": order_id,
            "marketplace_write_started": False,
        }

    returned_id = _text(order_payload.get("orderId"))
    if returned_id and returned_id != order_id:
        return {
            "success": False,
            "skipped": False,
            "reason": "amazon_package_tracking_identity_mismatch",
            "order_id": order_id,
            "returned_order_id": returned_id,
            "marketplace_write_started": False,
        }

    shipment, ambiguity = _package_truth(order_payload)
    if ambiguity:
        return {
            "success": True,
            "skipped": True,
            "reason": ambiguity,
            "order_id": order_id,
            "rows_considered": len(eligible),
            "marketplace_write_started": False,
        }
    if shipment is None:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_tracking_not_available",
            "order_id": order_id,
            "rows_considered": len(eligible),
            "marketplace_write_started": False,
        }

    updates = 0
    lifecycle_updates = 0
    for row in eligible:
        changed = False
        # Amazon is the marketplace authority for Amazon-owned package truth.
        # Correct stale persisted values as well as filling blanks. Journey state
        # advances only from Amazon's explicit packageStatus, never tracking or
        # shipped_at alone.
        if shipment.get("carrier") and _text(getattr(row, "carrier", None)) != _text(shipment["carrier"]):
            row.carrier = shipment["carrier"]
            changed = True
        if shipment.get("tracking_number") and _text(getattr(row, "tracking_number", None)) != _text(shipment["tracking_number"]):
            row.tracking_number = shipment["tracking_number"]
            changed = True
        if shipment.get("shipped_at") is not None and getattr(row, "shipped_at", None) != shipment["shipped_at"]:
            row.shipped_at = shipment["shipped_at"]
            changed = True
        lifecycle_status = shipment.get("lifecycle_status")
        if _can_advance_lifecycle(getattr(row, "status", None), lifecycle_status):
            row.status = lifecycle_status
            lifecycle_updates += 1
            changed = True
        if changed:
            row.updated_at = datetime.utcnow()
            updates += 1

    if updates:
        db.session.commit()

    return {
        "success": True,
        "skipped": False,
        "reason": None,
        "order_id": order_id,
        "rows_considered": len(eligible),
        "tracking_updates": updates,
        "lifecycle_updates": lifecycle_updates,
        "carrier": shipment.get("carrier"),
        "tracking_number": shipment.get("tracking_number"),
        "shipped_at": (
            shipment["shipped_at"].isoformat() if shipment.get("shipped_at") else None
        ),
        "package_reference_id": shipment.get("package_reference_id"),
        "package_status": shipment.get("package_status"),
        "package_detailed_status": shipment.get("package_detailed_status"),
        "lifecycle_status": shipment.get("lifecycle_status"),
        "source": source,
        "marketplace_write_started": False,
    }
