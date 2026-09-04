"""Persist exact Amazon FBM shipping facts into BT38's existing FBM profile.

Event-bound for new Amazon notifications, plus one bounded post-restart repair for
recent existing Amazon FBM rows whose profile/promise truth is still missing.
There is no page polling, order import, provider path or marketplace-wide scan.
"""
from __future__ import annotations
import threading
from datetime import datetime
from typing import Any
from flask import request
from sqlalchemy import text
from extensions import db
from fbm_models import FBMOrderProfile


_backfill_lock = threading.Lock()
_backfill_started = False


def _values(value: Any, key: str) -> list[Any]:
    out = []
    if isinstance(value, dict):
        for name, item in value.items():
            if str(name).lower() == key.lower(): out.append(item)
            out.extend(_values(item, key))
    elif isinstance(value, list):
        for item in value: out.extend(_values(item, key))
    return out


def _first(payload: dict, *keys: str):
    for key in keys:
        for value in _values(payload, key):
            if value not in (None, "", []): return value
    return None


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _date(value: Any):
    value = _text(value)
    if not value: return None
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception: return None


def _program_names(payload: dict) -> set[str]:
    """Collect every exact Amazon OrderPrograms value from the notification."""
    names: set[str] = set()
    for programs in _values(payload, "OrderPrograms") + _values(payload, "orderPrograms"):
        if isinstance(programs, (list, tuple, set)):
            values = programs
        else:
            values = [programs]
        for item in values:
            if isinstance(item, dict):
                item = item.get("Name") or item.get("name") or item.get("Program") or item.get("program")
            name = str(item or "").strip().lower()
            if name:
                names.add(name)
    return names


def _prime(payload: dict) -> bool | None:
    # Amazon ORDER_CHANGE can contain more than one OrderPrograms occurrence.
    # Prime anywhere in the exact notification is positive marketplace truth.
    if "prime" in _program_names(payload):
        return True
    raw = _first(payload, "IsPrime", "isPrime")
    if isinstance(raw, bool): return raw
    value = str(raw).strip().lower() if raw is not None else ""
    if value in {"true", "1", "yes"}: return True
    if value in {"false", "0", "no"}: return False
    return None


def _identity(payload: dict) -> tuple[int | None, str | None]:
    from models import MarketplaceOrder, Store
    order_id = _text(_first(payload, "AmazonOrderId", "amazonOrderId", "marketplace_order_id"))
    if not order_id: return None, None
    raw_store = _first(payload, "_bt38_store_id")
    try:
        if raw_store is not None: return int(raw_store), order_id
    except (TypeError, ValueError):
        pass
    # The canonical order was committed by the governed webhook path before this
    # response hook runs. Resolve that exact row rather than scanning Amazon.
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
    if store_id is None or not order_id: return False
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
    profile = FBMOrderProfile.query.filter_by(store_id=store_id, marketplace_order_id=order_id).first()
    if profile is None:
        profile = FBMOrderProfile(store_id=store_id, marketplace_order_id=order_id, platform="amazon")
    if is_prime is not None: profile.is_prime = is_prime
    if is_premium is not None: profile.is_premium = is_premium
    if fulfillment: profile.fulfillment_channel = fulfillment
    if service: profile.shipment_service_level = service
    if ship_by: profile.latest_ship_at = ship_by
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
              marketplace_checked_at=EXCLUDED.marketplace_checked_at,updated_at=EXCLUDED.updated_at
        """), {"store_id":store_id,"order_id":order_id,"service":service,"ship_by":ship_by,"earliest":earliest,"latest":latest,"now":now})
    db.session.commit()
    return True


def _hydrate_missing_recent_profiles(app, limit: int = 25) -> None:
    """One bounded repair of existing FBM rows, then return to event-only mode.

    Selection is DB-only and limited to canonical Amazon FBM orders already in
    BT38 from the last 48 hours. Amazon is read only for exact selected order IDs
    whose Prime/profile or delivery-promise persistence is incomplete.
    """
    try:
        with app.app_context():
            from models import MarketplaceOrder, Store
            from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

            rows = (
                db.session.query(MarketplaceOrder)
                .join(Store, Store.id == MarketplaceOrder.store_id)
                .outerjoin(
                    FBMOrderProfile,
                    (FBMOrderProfile.store_id == MarketplaceOrder.store_id)
                    & (FBMOrderProfile.marketplace_order_id == MarketplaceOrder.marketplace_order_id),
                )
                .outerjoin(
                    text("fbm_order_operational_state AS ops"),
                    text("ops.store_id = marketplace_orders.store_id AND ops.marketplace_order_id = marketplace_orders.marketplace_order_id"),
                )
                .filter(Store.is_active == True)  # noqa: E712
                .filter(Store.platform.ilike("%amazon%"))
                .filter(MarketplaceOrder.created_at >= text("NOW() - INTERVAL '48 hours'"))
                .filter(
                    text("UPPER(COALESCE(marketplace_orders.fulfillment_type, '')) NOT IN ('FBA','AFN','AMAZON')")
                )
                .filter(
                    text("(fbm_order_profiles.id IS NULL OR fbm_order_profiles.is_prime IS NULL OR ops.ship_by_at IS NULL OR ops.latest_delivery_at IS NULL)")
                )
                .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
                .limit(max(1, min(int(limit), 25)))
                .all()
            )

            seen = set()
            for order in rows:
                identity = (int(order.store_id), str(order.marketplace_order_id))
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    get_or_refresh_amazon_profile(order, force=True)
                except Exception:
                    db.session.rollback()
                    app.logger.exception(
                        "Amazon exact FBM profile repair failed store_id=%s order_id=%s",
                        order.store_id,
                        order.marketplace_order_id,
                    )
    except Exception:
        db.session.rollback()
        app.logger.exception("Amazon bounded FBM profile repair selector failed")


def _start_missing_profile_repair_once(app) -> None:
    global _backfill_started
    with _backfill_lock:
        if _backfill_started:
            return
        _backfill_started = True
    threading.Thread(
        target=_hydrate_missing_recent_profiles,
        args=(app,),
        daemon=True,
        name="BT38AmazonFBMProfileRepair",
    ).start()


def install_governed_amazon_fbm_profile_event_alignment(app) -> None:
    if getattr(app, "_bt38_amazon_fbm_profile_event_alignment", False): return

    @app.before_request
    def _amazon_fbm_profile_repair_once():
        _start_missing_profile_repair_once(app)
        return None

    @app.after_request
    def _amazon_fbm_profile_event(response):
        if request.method != "POST" or request.path.rstrip("/") != "/governed/webhooks/amazon" or response.status_code >= 500:
            return response
        try:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict): _persist(payload)
        except Exception:
            db.session.rollback()
            app.logger.exception("Amazon exact-event FBM profile alignment failed")
        return response
    app._bt38_amazon_fbm_profile_event_alignment = True
