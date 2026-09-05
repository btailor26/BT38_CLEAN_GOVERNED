"""Hydrate Amazon shipping classification into BT38 DB on demand.

This is not an order import path. MarketplaceOrder already exists and remains
source of truth. We only persist Amazon shipping facts required to govern FBM
routing (Prime/SFP, MFN, service level, latest ship time), plus the current
Amazon delivery address and shipped/unshipped state needed by the shipping desk.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

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

    # Packlink requires a real delivery contact, not merely a postcode. A
    # partially hydrated Amazon row must therefore force another exact Amazon
    # address read even when its FBM profile cache is still fresh.
    order_has_destination = all(
        _text(getattr(order, field, None))
        for field in (
            "ship_to_name",
            "ship_to_address",
            "ship_to_city",
            "ship_to_postcode",
            "ship_to_country",
            "ship_to_phone",
        )
    )

    if existing is not None and not force and cache_fresh and order_has_destination:
        return existing

    creds = getattr(store, "amazon_credentials", None)
    if not creds or not getattr(creds, "is_valid", lambda: False)():
        raise AmazonOrderProfileError("Amazon credentials are not configured for this store.")

    payload, address_payload = _fetch_order(store, str(order.marketplace_order_id))
    raw_is_prime = _bool(payload.get("IsPrime"))
    is_premium = _bool(payload.get("IsPremiumOrder"))
    fulfillment = _text(payload.get("FulfillmentChannel"))
    service_level = _text(payload.get("ShipmentServiceLevelCategory") or payload.get("ShipServiceLevel"))
    is_prime = _prime_from_shipping_facts(raw_is_prime, service_level)
    latest_ship = _parse_iso(payload.get("LatestShipDate"))
    earliest_delivery = _parse_iso(payload.get("EarliestDeliveryDate"))
    latest_delivery = _parse_iso(payload.get("LatestDeliveryDate"))

    _hydrate_marketplace_order(order, payload, address_payload)

    # Orders v0 does not return FBM package tracking. Once Amazon reports the
    # order shipped/partially shipped, use the current Orders v2026-01-01
    # PACKAGES dataset to fill carrier/tracking on the existing DB order. This
    # is a readback only; profile hydration remains usable if package tracking is
    # not yet available or the new read is temporarily unavailable.
    order_status = (_text(payload.get("OrderStatus")) or "").upper()
    if order_status in {"SHIPPED", "PARTIALLYSHIPPED", "PARTIALLY_SHIPPED"}:
        try:
            from services.governed_amazon_tracking_readback import (
                hydrate_amazon_tracking_for_order,
            )
            hydrate_amazon_tracking_for_order(
                store=store,
                marketplace_order_id=str(order.marketplace_order_id),
                source="fbm_amazon_order_profile",
            )
        except Exception:
            # A failed readback may leave SQLAlchemy in PendingRollbackError.
            # Clear only this failed DB unit before continuing with profile
            # persistence; tracking remains best-effort and can retry later.
            db.session.rollback()

    # Re-resolve after any defensive rollback so we do not create a duplicate
    # profile when another exact event/request persisted it meanwhile.
    existing = FBMOrderProfile.query.filter_by(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
    ).first()
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

    # Keep the already-existing operational promise path aligned with the exact
    # Amazon order read. Event hydration writes these same marketplace-owned
    # fields; on-demand/profile hydration must not drop the delivery window.
    if any((service_level, latest_ship, earliest_delivery, latest_delivery)):
        db.session.execute(text("""
            INSERT INTO fbm_order_operational_state
              (store_id, marketplace_order_id, platform, shipping_service, ship_by_at,
               earliest_delivery_at, latest_delivery_at, marketplace_checked_at, created_at, updated_at)
            VALUES (:store_id,:order_id,'amazon',:service,:ship_by,:earliest,:latest,:now,:now,:now)
            ON CONFLICT (store_id, marketplace_order_id) DO UPDATE SET
              platform='amazon',
              shipping_service=COALESCE(EXCLUDED.shipping_service,fbm_order_operational_state.shipping_service),
              ship_by_at=COALESCE(EXCLUDED.ship_by_at,fbm_order_operational_state.ship_by_at),
              earliest_delivery_at=COALESCE(EXCLUDED.earliest_delivery_at,fbm_order_operational_state.earliest_delivery_at),
              latest_delivery_at=COALESCE(EXCLUDED.latest_delivery_at,fbm_order_operational_state.latest_delivery_at),
              marketplace_checked_at=EXCLUDED.marketplace_checked_at,
              updated_at=EXCLUDED.updated_at
        """), {
            "store_id": order.store_id,
            "order_id": str(order.marketplace_order_id),
            "service": service_level,
            "ship_by": latest_ship,
            "earliest": earliest_delivery,
            "latest": latest_delivery,
            "now": now,
        })

    db.session.commit()
    return profile


def get_amazon_delivery_promise(order: Any) -> dict[str, Any]:
    """Read Amazon's current customer delivery promise for one existing order.

    This is an on-demand marketplace read used by the tracking journey UI. It
    does not create a BT38 journey, poll in the background, or infer dates from
    Packlink. The returned promise is exactly the delivery window Amazon owns.
    """
    store = getattr(order, "store", None)
    if store is None:
        raise AmazonOrderProfileError("Amazon store is missing from this DB order.")
    creds = getattr(store, "amazon_credentials", None)
    if not creds or not getattr(creds, "is_valid", lambda: False)():
        raise AmazonOrderProfileError("Amazon credentials are not configured for this store.")

    payload, _ = _fetch_order(store, str(order.marketplace_order_id))
    earliest = _parse_iso(payload.get("EarliestDeliveryDate"))
    latest = _parse_iso(payload.get("LatestDeliveryDate"))
    return {
        "source": "amazon",
        "earliest_delivery_at": earliest.isoformat() if earliest else None,
        "latest_delivery_at": latest.isoformat() if latest else None,
        "order_status": _text(payload.get("OrderStatus")),
        "shipment_service_level": _text(payload.get("ShipmentServiceLevelCategory") or payload.get("ShipServiceLevel")),
    }


def _hydrate_marketplace_order(order: Any, payload: dict[str, Any], address_payload: dict[str, Any] | None = None) -> None:
    """Persist current Amazon delivery/shipping facts onto the existing order.

    This deliberately does not create orders, mutate inventory, or submit any
    marketplace action. It only refreshes fields Amazon already owns.
    """
    address = address_payload or payload.get("ShippingAddress") or {}
    if isinstance(address, dict):
        if isinstance(address.get("ShippingAddress"), dict):
            address = address["ShippingAddress"]

        name = _text(address.get("Name"))
        address1 = _text(address.get("AddressLine1"))
        address2 = _text(address.get("AddressLine2"))
        address3 = _text(address.get("AddressLine3"))
        city = _text(address.get("City")) or _text(address.get("District"))
        postcode = _text(address.get("PostalCode"))
        country = _text(address.get("CountryCode"))
        phone = _text(address.get("Phone"))
        email = _text(address.get("Email"))

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
        if phone:
            order.ship_to_phone = phone
        if email:
            order.ship_to_email = email

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


def _fetch_order(store: Any, order_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
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
    payload = _response_payload(response)
    if not isinstance(payload, dict):
        raise AmazonOrderProfileError("Amazon Orders API returned an unexpected order payload.")

    address_payload = None
    inline_address = payload.get("ShippingAddress")
    if isinstance(inline_address, dict) and _text(inline_address.get("PostalCode")):
        address_payload = inline_address
    else:
        # Orders v0 exposes the customer delivery address through the dedicated
        # getOrderAddress operation. This call may require Amazon's restricted
        # Direct-to-Consumer Delivery/Shipping role for PII access.
        method = getattr(client, "get_order_address", None)
        if method is not None:
            try:
                address_response = method(order_id)
                candidate = _response_payload(address_response)
                if isinstance(candidate, dict):
                    address_payload = candidate
            except Exception as exc:
                # The order facts remain usable when the account lacks the
                # restricted address role. Surface the address gap through the
                # existing destination validation rather than failing profile
                # classification.
                address_payload = None

    return payload, address_payload


def _response_payload(response: Any) -> dict[str, Any]:
    value = getattr(response, "payload", None)
    if value is None and isinstance(response, dict):
        value = response.get("payload", response)
    return value if isinstance(value, dict) else {}


def _prime_from_shipping_facts(raw_is_prime: bool | None, service_level: str | None) -> bool | None:
    if raw_is_prime is not None:
        return raw_is_prime
    text_value = str(service_level or "").strip().lower()
    if any(token in text_value for token in ("prime", "nextday", "same day", "sameday")):
        return True
    return None


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text_value = str(value or "").strip().lower()
    if text_value in {"true", "1", "yes"}:
        return True
    if text_value in {"false", "0", "no"}:
        return False
    return None


def _parse_iso(value: Any):
    value = _text(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
