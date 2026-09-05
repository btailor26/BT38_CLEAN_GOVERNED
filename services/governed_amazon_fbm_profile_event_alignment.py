"""Persist exact Amazon FBM shipping facts from the current Amazon webhook only.

This module is event-bound. It does not perform startup recovery, historical
backfill, page polling, marketplace-wide scans, or background Amazon reads.
Prime/program and delivery-promise facts are persisted only when they arrive on
the current governed Amazon notification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import request
from sqlalchemy import text

from extensions import db
from fbm_models import FBMOrderProfile


def _values(value: Any, key: str) -> list[Any]:
    out = []
    if isinstance(value, dict):
        for name, item in value.items():
            if str(name).lower() == key.lower():
                out.append(item)
            out.extend(_values(item, key))
    elif isinstance(value, list):
        for item in value:
            out.extend(_values(item, key))
    return out


def _first(payload: dict, *keys: str):
    for key in keys:
        for value in _values(payload, key):
            if value not in (None, "", []):
                return value
    return None


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _date(value: Any):
    value = _text(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _program_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for programs in _values(payload, "OrderPrograms") + _values(payload, "orderPrograms"):
        values = programs if isinstance(programs, (list, tuple, set)) else [programs]
        for item in values:
            if isinstance(item, dict):
                item = item.get("Name") or item.get("name") or item.get("Program") or item.get("program")
            name = str(item or "").strip().lower()
            if name:
                names.add(name)
    return names


def _prime(payload: dict) -> bool | None:
    if "prime" in _program_names(payload):
        return True
    raw = _first(payload, "IsPrime", "isPrime")
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower() if raw is not None else ""
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    return None


def _identity(payload: dict) -> tuple[int | None, str | None]:
    from models import MarketplaceOrder, Store

    order_id = _text(_first(payload, "AmazonOrderId", "amazonOrderId", "marketplace_order_id"))
    if not order_id:
        return None, None

    raw_store = _first(payload, "_bt38_store_id")
    try:
        if raw_store is not None:
            return int(raw_store), order_id
    except (TypeError, ValueError):
        pass

    row = (
        db.session.query(MarketplaceOrder.store_id)
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .filter(MarketplaceOrder.marketplace_order_id == order_id)
        .filter(Store.platform.ilike("%amazon%"))
        .order_by(MarketplaceOrder.id.desc())
        .first()
    )
    return (int(row[0]), order_id) if row else (None, order_id)


def _persist(payload: dict) -> bool:
    store_id, order_id = _identity(payload)
    if store_id is None or not order_id:
        return False

    is_prime = _prime(payload)
    program_names = _program_names(payload)
    is_premium = True if "premium" in program_names else None
    fulfillment = _text(_first(payload, "FulfillmentType", "FulfillmentChannel"))
    service = _text(_first(payload, "ShipmentServiceLevelCategory", "ShipServiceLevel"))
    ship_by = _date(_first(payload, "LatestShipDate", "latestShipDate"))
    earliest = _date(_first(payload, "EarliestDeliveryDate", "earliestDeliveryDate"))
    latest = _date(_first(payload, "LatestDeliveryDate", "latestDeliveryDate"))

    if not any((is_prime is not None, is_premium is not None, fulfillment, service, ship_by, earliest, latest)):
        return False

    now = datetime.utcnow()
    profile = FBMOrderProfile.query.filter_by(
        store_id=store_id,
        marketplace_order_id=order_id,
    ).first()
    if profile is None:
        profile = FBMOrderProfile(
            store_id=store_id,
            marketplace_order_id=order_id,
            platform="amazon",
            source="amazon_order_change",
        )

    if is_prime is not None:
        profile.is_prime = is_prime
    if is_premium is not None:
        profile.is_premium = is_premium
    if fulfillment:
        profile.fulfillment_channel = fulfillment
    if service:
        profile.shipment_service_level = service
    if ship_by:
        profile.latest_ship_at = ship_by
    profile.checked_at = now
    profile.last_error = None
    db.session.add(profile)

    if any((service, ship_by, earliest, latest)):
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
            "store_id": store_id,
            "order_id": order_id,
            "service": service,
            "ship_by": ship_by,
            "earliest": earliest,
            "latest": latest,
            "now": now,
        })

    db.session.commit()
    return True


def install_governed_amazon_fbm_profile_event_alignment(app) -> None:
    if getattr(app, "_bt38_amazon_fbm_profile_event_alignment", False):
        return

    @app.after_request
    def _amazon_fbm_profile_event(response):
        if (
            request.method != "POST"
            or request.path.rstrip("/") != "/governed/webhooks/amazon"
            or response.status_code >= 500
        ):
            return response
        try:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict):
                _persist(payload)
        except Exception:
            db.session.rollback()
            app.logger.exception("Amazon exact-event FBM profile alignment failed")
        return response

    app._bt38_amazon_fbm_profile_event_alignment = True
    app.logger.info(
        "BT38 Amazon FBM profile aligned: current webhook facts only; no startup repair, no historical replay, no polling"
    )
