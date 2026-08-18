"""Hydrate Amazon shipping classification into BT38 DB on demand.

This is not an order import path. MarketplaceOrder already exists and remains
source of truth. We only persist Amazon shipping facts required to govern FBM
routing (Prime/SFP, MFN, service level, latest ship time), plus the current
Amazon delivery address and shipped/unshipped state needed by the shipping desk.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from extensions import db
from fbm_models import FBMOrderProfile
from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials


PROFILE_CACHE_TTL = timedelta(minutes=5)


class AmazonOrderProfileError(RuntimeError):
    pass


def get_or_refresh_amazon_profile(order: Any, *, force: bool = False) -> FBMOrderProfile:
    store = getattr(order, "store", None)
    if store is None:
        raise AmazonOrderProfileError("Amazon store is missing from this DB order.")

    existing = FBMOrderProfile.query.filter_by(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
    ).first()

    now = datetime.utcnow()
    checked_at = getattr(existing, "checked_at", None) if existing is not None else None
    cache_fresh = bool(checked_at and (now - checked_at) < PROFILE_CACHE_TTL)
    order_has_destination = bool(
        _text(getattr(order, "ship_to_postcode", None))
        and _text(getattr(order, "ship_to_country", None))
    )

    # Shipping facts must not remain permanently stale just because a profile
    # row already exists. Reuse only a recent profile when the MarketplaceOrder
    # also has the minimum destination data required by the shipping desk.
    if existing is not None and not force and cache_fresh and order_has_destination:
        return existing

    creds = getattr(store, "amazon_credentials", None)
    if not creds or not getattr(creds, "is_valid", lambda: False)():
        raise AmazonOrderProfileError("Amazon credentials are not configured for this store.")

    payload = _fetch_order(store, str(order.marketplace_order_id))
    is_prime = _bool(payload.get("IsPrime"))
    is_premium = _bool(payload.get("IsPremiumOrder"))
    fulfillment = _text(payload.get("FulfillmentChannel"))
    service_level = _text(payload.get("ShipmentServiceLevelCategory") or payload.get("ShipServiceLevel"))
    latest_ship = _parse_iso(payload.get("LatestShipDate"))

    _hydrate_marketplace_order(order, payload)

    profile = existing or FBMOrderProfile(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
        platform="amazon",
    )
    profile.is_prime = is_prime
    profile.is_premium = is_premium
    profile.fulfillment_channel = fulfillment
    profile.shipment_service_level = service_level
    profile.latest_ship_at = latest_ship
    profile.checked_at = now
    profile.last_error = None
    db.session.add(profile)
    db.session.commit()
    return profile


def _hydrate_marketplace_order(order: Any, payload: dict[str, Any]) -> None:
    """Persist current Amazon delivery/shipping facts onto the existing order.

    This deliberately does not create orders, mutate inventory, or submit any
    marketplace action. It only refreshes fields Amazon already owns.
    """
    address = payload.get("ShippingAddress") or {}
    if isinstance(address, dict):
        name = _text(address.get("Name"))
        address1 = _text(address.get("AddressLine1"))
        address2 = _text(address.get("AddressLine2"))
        address3 = _text(address.get("AddressLine3"))
        city = _text(address.get("City"))
        postcode = _text(address.get("PostalCode"))
        country = _text(address.get("CountryCode"))

        address_parts = [value for value in (address1, address2, address3) if value]
        if name:
            order.ship_to_name = name
        if address_parts:
            order.ship_to_address = ", ".join(address_parts)
        if city:
            order.ship_to_city = city
        if postcode:
            order.ship_to_postcode = postcode
        if country:
            order.ship_to_country = country.upper()[:2]

    status = (_text(payload.get("OrderStatus")) or "").upper()
    if status == "SHIPPED":
        order.shipped_at = (
            _parse_iso(payload.get("LastUpdateDate"))
            or _parse_iso(payload.get("LatestShipDate"))
            or order.shipped_at
            or datetime.utcnow()
        )
    elif status in {"UNSHIPPED", "PARTIALLYSHIPPED", "PENDING"}:
        order.shipped_at = None

    order.updated_at = datetime.utcnow()


def _fetch_order(store: Any, order_id: str) -> dict[str, Any]:
    try:
        from sp_api.api import Orders
        from sp_api.base import Marketplaces
    except Exception as exc:
        raise AmazonOrderProfileError("Installed amazon-sp-api library does not expose Orders API.") from exc

    creds = getattr(store, "amazon_credentials", None)
    normalized = {
        "refresh_token": creds.refresh_token,
        "lwa_app_id": creds.lwa_app_id,
        "lwa_client_secret": creds.lwa_client_secret,
        "seller_id": creds.seller_id,
        "marketplace_id": creds.marketplace_id,
        "aws_access_key_id": getattr(creds, "aws_access_key_id", None),
        "aws_secret_access_key": getattr(creds, "aws_secret_access_key", None),
        "role_arn": getattr(creds, "aws_user_arn", None),
    }
    client = Orders(
        credentials=_sp_api_credentials(normalized),
        marketplace=_marketplace_for_id(creds.marketplace_id, Marketplaces),
    )
    response = client.get_order(order_id)
    payload = getattr(response, "payload", None)
    if payload is None and hasattr(response, "json"):
        payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    if not isinstance(payload, dict):
        raise AmazonOrderProfileError("Amazon Orders API returned an unexpected order payload.")
    return payload


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_iso(value: Any):
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
