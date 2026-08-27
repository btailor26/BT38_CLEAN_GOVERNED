"""Persist FBM operational facts without changing marketplace order ownership.

This table is deliberately separate from MarketplaceOrder. It stores only the
shipping-desk facts that BT38 needs to render an operational view consistently:
marketplace delivery promise, selected/advertised shipping service, ship-by
deadline, and the actual packed-parcel values entered by the user.

The table is created lazily with checkfirst=True so the governed branch can add
this additive state without altering existing marketplace/order tables.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from models import MarketplaceOrder


class FBMOrderOperationalState(db.Model):
    __tablename__ = "fbm_order_operational_state"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(
        db.Integer,
        db.ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "store_id",
            "marketplace_order_id",
            name="uq_fbm_operational_state_store_order",
        ),
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
            return (
                f"{self.earliest_delivery_at.strftime('%d %b')} – "
                f"{self.latest_delivery_at.strftime('%d %b')}"
            )
        value = self.latest_delivery_at or self.earliest_delivery_at
        return value.strftime("%d %b") if value else None


def ensure_operational_table() -> None:
    """Create only this additive table when it is first needed."""
    FBMOrderOperationalState.__table__.create(bind=db.engine, checkfirst=True)


def operational_state(order: Any, *, create: bool = False) -> FBMOrderOperationalState | None:
    if order is None or getattr(order, "store_id", None) is None:
        return None
    order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
    if not order_id:
        return None
    ensure_operational_table()
    row = FBMOrderOperationalState.query.filter_by(
        store_id=order.store_id,
        marketplace_order_id=order_id,
    ).first()
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
    """Persist actual packed-parcel values for this marketplace order.

    Order-level parcel persistence is always allowed. Reusable SKU/carton
    defaults remain a separate, stricter concern in fbm_order_mapper.
    """
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


def promise_state(order: Any, shipment: Any = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return display state against the marketplace-owned delivery promise."""
    current = now or datetime.utcnow()
    state = operational_state(order, create=False)
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


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# Expose read-only operational state to Jinja through existing MarketplaceOrder
# objects without changing the marketplace order table itself.
def _fbm_operational_state_property(order: MarketplaceOrder):
    return operational_state(order, create=False)


def _fbm_promise_state_property(order: MarketplaceOrder):
    return promise_state(order)


if not hasattr(MarketplaceOrder, "fbm_operational_state"):
    MarketplaceOrder.fbm_operational_state = property(_fbm_operational_state_property)
if not hasattr(MarketplaceOrder, "fbm_promise_state"):
    MarketplaceOrder.fbm_promise_state = property(_fbm_promise_state_property)
