"""Persist and expose one governed FBM operational state path.

MarketplaceOrder remains the order source of truth. This module stores only
shipping-desk facts (marketplace promise/service and packed parcel values) and
builds the server-side state used by the existing /fbm page. It does not create
orders, buy postage, dispatch, mutate inventory, or create a second UI route.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from models import MarketplaceOrder


class FBMOrderOperationalState(db.Model):
    __tablename__ = "fbm_order_operational_state"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(200), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False, index=True)
    shipping_service = db.Column(db.String(250), nullable=True)
    ship_by_at = db.Column(db.DateTime, nullable=True)
    earliest_delivery_at = db.Column(db.DateTime, nullable=True)
    latest_delivery_at = db.Column(db.DateTime, nullable=True, index=True)
    parcel = db.Column(db.JSON, nullable=False, default=dict)
    marketplace_checked_at = db.Column(db.DateTime, nullable=True)
    parcel_saved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("store_id", "marketplace_order_id", name="uq_fbm_operational_state_store_order"),
    )

    @property
    def promise_available(self) -> bool:
        return bool(self.earliest_delivery_at or self.latest_delivery_at)

    @property
    def promise_label(self) -> str | None:
        if not self.promise_available:
            return None
        if self.earliest_delivery_at and self.latest_delivery_at:
            if self.earliest_delivery_at.date() == self.latest_delivery_at.date():
                return self.latest_delivery_at.strftime("%d %b")
            return f"{self.earliest_delivery_at.strftime('%d %b')} – {self.latest_delivery_at.strftime('%d %b')}"
        value = self.latest_delivery_at or self.earliest_delivery_at
        return value.strftime("%d %b") if value else None


def ensure_operational_table() -> None:
    FBMOrderOperationalState.__table__.create(bind=db.engine, checkfirst=True)


def operational_state(order: Any, *, create: bool = False) -> FBMOrderOperationalState | None:
    if order is None or getattr(order, "store_id", None) is None:
        return None
    order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
    if not order_id:
        return None
    ensure_operational_table()
    row = FBMOrderOperationalState.query.filter_by(store_id=order.store_id, marketplace_order_id=order_id).first()
    if row is None and create:
        store = getattr(order, "store", None)
        row = FBMOrderOperationalState(
            store_id=order.store_id,
            marketplace_order_id=order_id,
            platform=str(getattr(store, "platform", "") or "Unknown").strip(),
        )
        db.session.add(row)
    return row


def update_marketplace_facts(
    order: Any,
    *,
    platform: str,
    shipping_service: str | None = None,
    ship_by_at: datetime | None = None,
    earliest_delivery_at: datetime | None = None,
    latest_delivery_at: datetime | None = None,
) -> FBMOrderOperationalState | None:
    row = operational_state(order, create=True)
    if row is None:
        return None
    row.platform = str(platform or row.platform or "Unknown")
    if shipping_service:
        row.shipping_service = str(shipping_service).strip() or row.shipping_service
    if ship_by_at is not None:
        row.ship_by_at = ship_by_at
    if earliest_delivery_at is not None:
        row.earliest_delivery_at = earliest_delivery_at
    if latest_delivery_at is not None:
        row.latest_delivery_at = latest_delivery_at
    row.marketplace_checked_at = datetime.utcnow()
    db.session.add(row)
    return row


def save_order_parcel(order: Any, values: dict[str, Any]) -> FBMOrderOperationalState | None:
    row = operational_state(order, create=True)
    if row is None:
        return None
    parcel = dict(row.parcel or {})
    changed = False
    for key in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        value = _positive_float(values.get(key))
        if value is not None and parcel.get(key) != value:
            parcel[key] = value
            changed = True
    if changed:
        row.parcel = parcel
        row.parcel_saved_at = datetime.utcnow()
        db.session.add(row)
    return row


def saved_order_parcel(order: Any) -> dict[str, float]:
    row = operational_state(order, create=False)
    if row is None or not isinstance(row.parcel, dict):
        return {}
    result: dict[str, float] = {}
    for key in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        value = _positive_float(row.parcel.get(key))
        if value is not None:
            result[key] = value
    return result


def _latest_shipment(order: Any):
    if order is None or getattr(order, "store_id", None) is None:
        return None
    order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
    if not order_id:
        return None
    try:
        from fbm_models import FBMShipment
        return (
            FBMShipment.query
            .filter_by(store_id=order.store_id, marketplace_order_id=order_id)
            .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
            .first()
        )
    except Exception:
        return None


def promise_state(order: Any, shipment: Any = None, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.utcnow()
    state = operational_state(order, create=False)
    if shipment is None:
        shipment = _latest_shipment(order)
    latest = getattr(state, "latest_delivery_at", None) if state is not None else None
    delivered_at = getattr(shipment, "delivered_at", None) if shipment is not None else None
    late = bool(latest and current > latest and not delivered_at)
    delivered_late = bool(latest and delivered_at and delivered_at > latest)
    delivered_on_time = bool(latest and delivered_at and delivered_at <= latest)
    return {
        "available": bool(state and state.promise_available),
        "label": state.promise_label if state else None,
        "latest_delivery_at": latest,
        "late": late,
        "delivered_late": delivered_late,
        "delivered_on_time": delivered_on_time,
        "shipping_service": getattr(state, "shipping_service", None) if state else None,
    }


def fbm_view_state(order: Any) -> dict[str, Any]:
    """Return the single server-side state consumed by the existing /fbm page.

    Missing marketplace-owned facts are hydrated through the existing exact-order
    readers only. No alternate endpoint, browser overlay or second import path is
    created.
    """
    cached = getattr(order, "_bt38_fbm_view_state", None)
    if isinstance(cached, dict):
        return cached

    store = getattr(order, "store", None)
    platform = str(getattr(store, "platform", "") or "").strip().lower()
    refresh_error = None

    try:
        state = operational_state(order, create=False)
        promise_known = bool(state and (state.earliest_delivery_at or state.latest_delivery_at))
        service_known = bool(state and state.shipping_service)
        address_missing = any(
            not str(getattr(order, field, "") or "").strip()
            for field in ("ship_to_name", "ship_to_address", "ship_to_city", "ship_to_postcode", "ship_to_country")
        )

        if platform == "amazon":
            from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile
            get_or_refresh_amazon_profile(order, force=not promise_known)
        elif platform == "ebay" and (address_missing or not promise_known or not service_known):
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order
            hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=str(order.marketplace_order_id),
                source="fbm_page",
            )
    except Exception as exc:
        db.session.rollback()
        refresh_error = str(exc)

    state = operational_state(order, create=False)
    shipment = _latest_shipment(order)
    promise = promise_state(order, shipment=shipment)

    profile = None
    try:
        from fbm_models import FBMOrderProfile
        profile = FBMOrderProfile.query.filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        ).first()
    except Exception:
        profile = None

    journey = "not_started"
    if shipment is not None:
        if getattr(shipment, "delivered_at", None):
            journey = "delivered"
        elif getattr(shipment, "first_movement_at", None):
            journey = "in_transit"
        elif getattr(shipment, "carrier_accepted_at", None):
            journey = "accepted"
        elif getattr(shipment, "label_purchased_at", None):
            journey = "awaiting_carrier_acceptance"

    destination = {
        "name": str(getattr(order, "ship_to_name", "") or "").strip() or None,
        "address": str(getattr(order, "ship_to_address", "") or "").strip() or None,
        "city": str(getattr(order, "ship_to_city", "") or "").strip() or None,
        "postcode": str(getattr(order, "ship_to_postcode", "") or "").strip() or None,
        "country": str(getattr(order, "ship_to_country", "") or "").strip() or None,
    }
    missing_address = [key for key, value in destination.items() if not value]
    is_prime = bool(platform == "amazon" and profile and profile.is_prime is True)

    result = {
        "platform": platform,
        "is_prime": is_prime,
        "prime_locked": is_prime,
        "shipping_service": (
            getattr(state, "shipping_service", None)
            or (getattr(profile, "shipment_service_level", None) if profile else None)
        ),
        "promise": promise,
        "journey_state": journey,
        "picked_up": journey in {"accepted", "in_transit", "out_for_delivery", "delivered"},
        "in_transit": journey in {"in_transit", "out_for_delivery", "delivered"},
        "delivered": journey == "delivered",
        "destination": destination,
        "address_complete": not missing_address,
        "missing_address": missing_address,
        "parcel": saved_order_parcel(order),
        "refresh_error": refresh_error,
    }
    setattr(order, "_bt38_fbm_view_state", result)
    return result


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


if not hasattr(MarketplaceOrder, "fbm_operational_state"):
    MarketplaceOrder.fbm_operational_state = property(lambda order: operational_state(order, create=False))
if not hasattr(MarketplaceOrder, "fbm_promise_state"):
    MarketplaceOrder.fbm_promise_state = property(lambda order: promise_state(order))
if not hasattr(MarketplaceOrder, "fbm_view_state"):
    MarketplaceOrder.fbm_view_state = property(fbm_view_state)
