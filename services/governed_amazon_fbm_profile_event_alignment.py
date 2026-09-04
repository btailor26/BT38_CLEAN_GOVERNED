"""Align Amazon FBM shipping classification with the existing persisted profile.

This is an event-bound adapter, not a page poller or order importer. Amazon's exact
ORDER_CHANGE payload is allowed to seed the existing FBMOrderProfile immediately;
when promise dates are present they are written to the existing operational-state
row. The FBM page remains DB-only and continues to use its existing batched reads.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import request
from sqlalchemy import text

from extensions import db
from fbm_models import FBMOrderProfile


def _deep_values(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if str(name).lower() == key.lower():
                found.append(item)
            found.extend(_deep_values(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_deep_values(item, key))
    return found


def _first(payload: dict, *keys: str):
    for key in keys:
        for value in _deep_values(payload, key):
            if value not in (None, "", []):
                return value
    return None


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _parse_iso(value: Any):
    value = _text(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _prime(payload: dict) -> bool | None:
    programs = _first(payload, "OrderPrograms", "orderPrograms")
    if isinstance(programs, list):
        names = {str(item or "").strip().lower() for item in programs}
        if "prime" in names:
            return True
    raw = _first(payload, "IsPrime", "isPrime")
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        value = str(raw).strip().lower()
        if value in {"true", "1", "yes"}:
            return True
        if value in {"false", "0", "no"}:
            return False
    return None


def _amazon_order_id(payload: dict) -> str | None:
    return _text(_first(payload, "AmazonOrderId", "amazonOrderId", "marketplace_order_id"))


def _store_id(payload: dict) -> int | None:
    raw = _first(payload, "_bt38_store_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _persist_event_truth(payload: dict) -> bool:
    order_id = _amazon_order_id(payload)
    store_id = _store_id(payload)
    if not order_id or store_id is None:
        return False

    is_prime = _prime(payload)
    programs = _first(payload, "OrderPrograms", "orderPrograms")
    is_premium = None
    if isinstance(programs, list):
        is_premium = "premium" in {str(item or "").strip().lower() for item in programs}

    fulfillment = _text(_first(payload, "FulfillmentType", "FulfillmentChannel"))
    service = _text(_first(payload, "ShipmentServiceLevelCategory", "ShipServiceLevel"))
    ship_by = _parse_iso(_first(payload, "LatestShipDate", "latestShipDate"))
    earliest_delivery = _parse_iso(_first(payload, "EarliestDeliveryDate", "earliestDeliveryDate"))
    latest_delivery = _parse_iso(_first(payload, "LatestDeliveryDate", "latestDeliveryDate"))

    if not any((is_prime is not None, is_premium is not None, fulfillment, service, ship_by, earliest_delivery, latest_delivery)):
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

    # Reuse the existing operational-state authority consumed by the FBM page.
    # One indexed UPSERT per exact event; no schema discovery and no page write.
    if any((service, ship_by, earliest_delivery, latest_delivery)):
        db.session.execute(
            text(
                """
                INSERT INTO fbm_order_operational_state
                    (store_id, marketplace_order_id, platform, shipping_service,
                     ship_by_at, earliest_delivery_at, latest_delivery_at,
                     marketplace_checked_at, created_at, updated_at)
                VALUES
                    (:store_id, :order_id, 'amazon', :service,
                     :ship_by, :earliest_delivery, :latest_delivery,
                     :checked_at, :checked_at, :checked_at)
                ON CONFLICT (store_id, marketplace_order_id) DO UPDATE SET
                    platform = 'amazon',
                    shipping_service = COALESCE(EXCLUDED.shipping_service, fbm_order_operational_state.shipping_service),
                    ship_by_at = COALESCE(EXCLUDED.ship_by_at, fbm_order_operational_state.ship_by_at),
                    earliest_delivery_at = COALESCE(EXCLUDED.earliest_delivery_at, fbm_order_operational_state.earliest_delivery_at),
                    latest_delivery_at = COALESCE(EXCLUDED.latest_delivery_at, fbm_order_operational_state.latest_delivery_at),
                    marketplace_checked_at = EXCLUDED.marketplace_checked_at,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "store_id": store_id,
                "order_id": order_id,
                "service": service,
                "ship_by": ship_by,
                "earliest_delivery": earliest_delivery,
                "latest_delivery": latest_delivery,
                "checked_at": now,
            },
        )

    db.session.commit()
    return True


def install_governed_amazon_fbm_profile_event_alignment(app) -> None:
    if getattr(app, "_bt38_amazon_fbm_profile_event_alignment", False):
        return

    @app.after_request
    def _persist_amazon_fbm_profile_from_event(response):
        if request.method != "POST" or request.path.rstrip("/") != "/governed/webhooks/amazon":
            return response
        if response.status_code >= 500:
            return response
        try:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict):
                _persist_event_truth(payload)
        except Exception:
            db.session.rollback()
            app.logger.exception("Amazon exact-event FBM profile alignment failed")
        return response

    app._bt38_amazon_fbm_profile_event_alignment = True
