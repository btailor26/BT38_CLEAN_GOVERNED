"""Read Amazon-owned FBM package lifecycle/tracking into existing MarketplaceOrder rows.

Orders API v2026-01-01 exposes FBM order/package truth through includedData=PACKAGES.
This helper is read-only against Amazon and updates existing merchant-fulfilled
BT38 order rows from Amazon's current marketplace lifecycle/tracking truth. It
does not create orders, mutate inventory, buy postage, confirm shipment, start
a scheduler, or touch MCF.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from amazon_service_live_patch import _load_credentials
from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_EU_ENDPOINT = "https://sellingpartnerapi-eu.amazon.com"

_PACKAGE_LIFECYCLE = {
    "PENDING": "pending",
    "UNSHIPPED": "unshipped",
    "PARTIALLYSHIPPED": "partially_shipped",
    "SHIPPED": "shipped",
    "FULFILLED": "shipped",
    "DISPATCHED": "shipped",
    "PICKEDUPBYCARRIER": "picked_up",
    "CHECKEDINTOCARRIERHUB": "in_transit",
    "INTRANSIT": "in_transit",
    "OUTFORDELIVERY": "out_for_delivery",
    "DELIVERED": "delivered",
    "CANCELED": "cancelled",
    "CANCELLED": "cancelled",
}
_JOURNEY_RANK = {
    "partially_shipped": 0,
    "shipped": 1,
    "picked_up": 2,
    "accepted": 2,
    "carrier_accepted": 2,
    "collected": 2,
    "in_transit": 3,
    "out_for_delivery": 4,
    "delivered": 5,
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
_ROUTINE_PRE_DISPATCH_STATES = {
    "",
    "order",
    "failed",
    "processed",
    "pending",
    "unshipped",
    "stock_applied_pending_reconcile",
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


def _order_fulfillment_status(order_payload: dict[str, Any]) -> str | None:
    """Read v2026 fulfillment.status first, with legacy keys only as compatibility fallback."""
    fulfillment = order_payload.get("fulfillment")
    if isinstance(fulfillment, dict):
        raw_status = (
            fulfillment.get("status")
            or fulfillment.get("fulfillmentStatus")
        )
        if _text(raw_status):
            return _text(raw_status)
    return _text(
        order_payload.get("orderStatus")
        or order_payload.get("OrderStatus")
        or order_payload.get("fulfillmentStatus")
    ) or None


def _order_lifecycle(order_payload: dict[str, Any]) -> str | None:
    """Use Amazon's explicit fulfillment status as lifecycle authority when available."""
    return _PACKAGE_LIFECYCLE.get(_status_key(_order_fulfillment_status(order_payload)))


def _can_advance_lifecycle(current: Any, incoming: str | None) -> bool:
    incoming_value = _text(incoming).lower()
    current_value = _text(current).lower().replace("-", "_").replace(" ", "_")
    if not incoming_value or current_value == incoming_value:
        return False
    if current_value in _PROTECTED_ISSUE_STATES:
        return False
    # Exact Amazon pre-dispatch truth may repair routine local processing states
    # such as failed/processed/order. Pending never regresses an already exact
    # Unshipped state; Unshipped may advance Pending when Amazon clears payment.
    if incoming_value == "pending":
        return current_value in (_ROUTINE_PRE_DISPATCH_STATES - {"unshipped"})
    if incoming_value == "unshipped":
        return current_value in _ROUTINE_PRE_DISPATCH_STATES
    # Explicit Amazon cancellation is terminal marketplace lifecycle truth. It
    # must clear stale routine states such as Processed/Unshipped, but it must
    # not overwrite a completed Delivered journey or a newer protected issue.
    if incoming_value == "cancelled":
        return current_value != "delivered"
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


def _request_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "x-amz-access-token": access_token,
        "x-amz-date": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "user-agent": "BT38/1.0 (Language=Python)",
    }


def _legacy_exact_order_lifecycle(
    *,
    access_token: str,
    order_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Fallback to Amazon's exact v0 OrderStatus when v2026 package truth is absent.

    This remains an exact marketplace read. It never derives lifecycle from a
    carrier, tracking number, label purchase, local processed state, or age.
    """
    response = requests.get(
        f"{SP_API_EU_ENDPOINT}/orders/v0/orders/{quote(order_id, safe='')}",
        headers=_request_headers(access_token),
        timeout=30,
    )
    if response.status_code >= 400:
        return None, None, f"amazon_legacy_exact_order_read_failed:{response.status_code}:{response.text[:500]}"

    body = response.json() or {}
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    if not isinstance(payload, dict):
        return None, None, "amazon_legacy_exact_order_payload_invalid"

    returned_id = _text(payload.get("AmazonOrderId") or payload.get("orderId"))
    if returned_id and returned_id != order_id:
        return None, None, f"amazon_legacy_exact_order_identity_mismatch:{returned_id}"

    raw_status = _text(
        payload.get("OrderStatus")
        or payload.get("orderStatus")
        or payload.get("fulfillmentStatus")
    ) or None
    return _PACKAGE_LIFECYCLE.get(_status_key(raw_status)), raw_status, None


def _package_truth(order_payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Return lifecycle truth even when tracking is absent or multi-package."""
    packages = [row for row in (order_payload.get("packages") or []) if isinstance(row, dict)]
    order_lifecycle = _order_lifecycle(order_payload)

    package_lifecycles = []
    for package in packages:
        lifecycle, _ = _package_lifecycle(package)
        if lifecycle:
            package_lifecycles.append(lifecycle)

    lifecycle_status = order_lifecycle
    if lifecycle_status is None and package_lifecycles:
        lifecycle_status = max(
            package_lifecycles,
            key=lambda value: _JOURNEY_RANK.get(value, -1),
        )

    tracked = [row for row in packages if _text(row.get("trackingNumber"))]
    unique_tracking = {_text(row.get("trackingNumber")) for row in tracked}
    ambiguity = None
    package = None
    if len(unique_tracking) == 1:
        package = tracked[0]
    elif len(unique_tracking) > 1:
        ambiguity = "multiple_amazon_packages_require_multi_tracking_storage"
    elif len(packages) == 1:
        # One package can still carry lifecycle, carrier and shipTime when Amazon
        # has not supplied a tracking number.
        package = packages[0]

    if package is None and not lifecycle_status:
        return None, ambiguity

    detailed_status = None
    raw_package_status = None
    if package is not None:
        package_lifecycle, detailed_status = _package_lifecycle(package)
        if lifecycle_status is None:
            lifecycle_status = package_lifecycle
        package_status = package.get("packageStatus")
        raw_package_status = (
            _text(package_status.get("status"))
            if isinstance(package_status, dict)
            else _text(package_status)
        ) or None

    return {
        "carrier": _text(package.get("carrier")) or None if package is not None else None,
        "tracking_number": _text(package.get("trackingNumber")) or None if package is not None else None,
        # shipTime is shipment truth. package createdTime must never be invented
        # as a shipment timestamp.
        "shipped_at": _parse_iso(package.get("shipTime")) if package is not None else None,
        "package_reference_id": _text(package.get("packageReferenceId")) or None if package is not None else None,
        "package_status": raw_package_status,
        "package_detailed_status": detailed_status,
        "lifecycle_status": lifecycle_status,
        "order_status": _order_fulfillment_status(order_payload),
        "truth_source": "orders_2026",
    }, ambiguity


def _persist_marketplace_shipment(
    *,
    store_id: int,
    order_id: str,
    shipment: dict[str, Any],
    observed_at: datetime,
) -> tuple[FBMShipment | None, bool]:
    """Persist exact Amazon shipment identity/state without inventing milestone times."""
    tracking = _text(shipment.get("tracking_number"))
    if not tracking:
        return None, False

    existing = (
        FBMShipment.query
        .filter(
            FBMShipment.store_id == store_id,
            FBMShipment.marketplace_order_id == order_id,
            FBMShipment.tracking_number == tracking,
        )
        .order_by(FBMShipment.id.desc())
        .first()
    )
    if existing is not None and _text(existing.provider).lower() != "marketplace":
        return existing, False

    row = existing
    lifecycle = _text(shipment.get("lifecycle_status")).lower()
    provider_status = (
        _text(shipment.get("package_status"))
        or _text(shipment.get("package_detailed_status"))
        or _text(shipment.get("order_status"))
        or lifecycle
        or "marketplace"
    )

    if row is None:
        row = FBMShipment(
            store_id=store_id,
            marketplace_order_id=order_id,
            provider="marketplace",
            provider_shipment_id=_text(shipment.get("package_reference_id")) or None,
            carrier=_text(shipment.get("carrier")) or None,
            tracking_number=tracking,
            status=lifecycle or ("shipped" if shipment.get("shipped_at") is not None else "marketplace"),
            last_provider_status=provider_status,
            last_provider_checked_at=observed_at,
            marketplace_confirmation_status="marketplace_authoritative",
        )
        db.session.add(row)
        return row, True

    if shipment.get("package_reference_id") and _text(row.provider_shipment_id) != _text(shipment.get("package_reference_id")):
        row.provider_shipment_id = _text(shipment.get("package_reference_id"))
    if shipment.get("carrier") and _text(row.carrier) != _text(shipment.get("carrier")):
        row.carrier = _text(shipment.get("carrier"))
    if lifecycle and _can_advance_lifecycle(row.status, lifecycle):
        row.status = lifecycle
    if _text(row.last_provider_status) != provider_status:
        row.last_provider_status = provider_status
    row.last_provider_checked_at = observed_at
    row.marketplace_confirmation_status = "marketplace_authoritative"
    return row, True


def hydrate_amazon_tracking_for_order(
    *,
    store: Any,
    marketplace_order_id: str,
    source: str = "amazon_orders_2026_packages",
) -> dict[str, Any]:
    """Persist current Amazon lifecycle/tracking truth for one existing Amazon FBM order."""
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
    response = requests.get(
        f"{SP_API_EU_ENDPOINT}/orders/2026-01-01/orders/{quote(order_id, safe='')}",
        params={"includedData": "PACKAGES"},
        headers=_request_headers(access_token),
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

    observed_v2026_status = _order_fulfillment_status(order_payload)
    shipment, ambiguity = _package_truth(order_payload)
    legacy_status = None
    legacy_error = None

    if shipment is None:
        legacy_lifecycle, legacy_status, legacy_error = _legacy_exact_order_lifecycle(
            access_token=access_token,
            order_id=order_id,
        )
        if legacy_lifecycle:
            shipment = {
                "carrier": None,
                "tracking_number": None,
                "shipped_at": None,
                "package_reference_id": None,
                "package_status": None,
                "package_detailed_status": None,
                "lifecycle_status": legacy_lifecycle,
                "order_status": legacy_status,
                "truth_source": "orders_v0_exact_order_status",
            }

    if shipment is None:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_dispatch_truth_not_available",
            "order_id": order_id,
            "rows_considered": len(eligible),
            "tracking_ambiguity": ambiguity,
            "observed_v2026_status": observed_v2026_status,
            "legacy_order_status": legacy_status,
            "legacy_read_error": legacy_error,
            "marketplace_write_started": False,
        }

    updates = 0
    lifecycle_updates = 0
    for row in eligible:
        changed = False
        # Amazon is the authority for Amazon-owned lifecycle truth. Carrier and
        # tracking are optional enrichment; lifecycle never depends on them.
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

    observed_at = datetime.utcnow()
    shipment_row, shipment_persisted = _persist_marketplace_shipment(
        store_id=int(store.id),
        order_id=order_id,
        shipment=shipment,
        observed_at=observed_at,
    )

    if updates or shipment_persisted:
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
        "order_status": shipment.get("order_status"),
        "lifecycle_status": shipment.get("lifecycle_status"),
        "truth_source": shipment.get("truth_source"),
        "tracking_ambiguity": ambiguity,
        "observed_v2026_status": observed_v2026_status,
        "legacy_order_status": legacy_status,
        "legacy_read_error": legacy_error,
        "source": source,
        "marketplace_shipment_persisted": bool(shipment_persisted),
        "marketplace_shipment_id": getattr(shipment_row, "id", None),
        "marketplace_write_started": False,
    }
