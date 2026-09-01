"""Align existing marketplace/provider shipping paths to one persisted spend ledger.

No label workflow is replaced. Existing purchase/status functions run unchanged;
this module persists a monetary fact only after the existing shipment authority
has confirmed a purchased/paid label. Missing permission/data stays unavailable
rather than becoming zero.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Any

from extensions import db
from fbm_models import FBMRateQuote, FBMShipment
from shipping_spend_models import ShippingSpendLedger
from services.governed_shipping_spend_policy import confirmed_purchase_spend


_purchase_spend: ContextVar[tuple[Decimal, str, str, str | None] | None] = ContextVar(
    "bt38_purchase_spend", default=None
)


def _capture(value: Any, *, source: str, reference: str | None = None) -> None:
    spend = confirmed_purchase_spend(value, source=source, default_currency="GBP")
    if spend.confirmed and spend.amount is not None:
        _purchase_spend.set((spend.amount, spend.currency or "GBP", source, reference))


def _upsert_spend(
    shipment: FBMShipment,
    *,
    amount: Decimal,
    currency: str,
    source: str,
    source_reference: str | None = None,
) -> ShippingSpendLedger:
    dispatch_key = shipment.purchase_key or f"shipment:{shipment.id}"
    row = ShippingSpendLedger.query.filter_by(dispatch_key=dispatch_key).first()
    if row is None:
        row = ShippingSpendLedger(dispatch_key=dispatch_key)
        db.session.add(row)
    row.shipment_id = shipment.id
    row.store_id = shipment.store_id
    row.marketplace_order_id = shipment.marketplace_order_id
    row.fulfillment_family = "FBM"
    row.provider = shipment.provider
    row.amount = amount
    row.currency = currency
    row.source = source
    row.source_reference = source_reference or shipment.provider_shipment_id
    row.confirmed = True
    row.recorded_at = shipment.label_purchased_at or datetime.utcnow()
    return row


def _persist_from_response(response: Any) -> None:
    captured = _purchase_spend.get()
    if captured is None:
        return
    amount, currency, source, source_reference = captured
    flask_response = response[0] if isinstance(response, tuple) else response
    payload = flask_response.get_json(silent=True) if hasattr(flask_response, "get_json") else None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return
    shipment_id = payload.get("shipment_id")
    if not shipment_id:
        return
    shipment = db.session.get(FBMShipment, int(shipment_id))
    if shipment is None or str(shipment.purchase_status or "").lower() != "purchased":
        return
    _upsert_spend(
        shipment,
        amount=amount,
        currency=currency,
        source=source,
        source_reference=source_reference,
    )
    db.session.commit()


def _stored_selected_rate(shipment: FBMShipment) -> dict | None:
    """Resolve the exact persisted Packlink rate used by this shipment."""
    if not shipment.selected_rate_id:
        return None
    quotes = (
        FBMRateQuote.query
        .filter_by(
            store_id=shipment.store_id,
            marketplace_order_id=shipment.marketplace_order_id,
            provider="packlink",
        )
        .order_by(FBMRateQuote.created_at.desc(), FBMRateQuote.id.desc())
        .all()
    )
    selected_id = str(shipment.selected_rate_id)
    for quote in quotes:
        for rate in quote.rates or []:
            if not isinstance(rate, dict):
                continue
            candidate = str(rate.get("rate_id") or rate.get("id") or rate.get("service_id") or "")
            if candidate == selected_id:
                return rate
    return None


def recover_confirmed_packlink_spend(shipment: FBMShipment) -> ShippingSpendLedger | None:
    """Recover historical/live Packlink spend only from a confirmed paid label.

    The stored selected rate is accepted as recoverable purchase spend only after
    the existing shipment path has independently proved provider payment by a
    purchased status plus label purchase timestamp. Pending/draft rows never
    enter the ledger.
    """
    if str(shipment.provider or "").lower() != "packlink":
        return None
    if str(shipment.purchase_status or "").lower() != "purchased" or shipment.label_purchased_at is None:
        return None
    rate = _stored_selected_rate(shipment)
    if rate is None:
        return None
    spend = confirmed_purchase_spend(
        rate.get("price") or rate.get("total_price") or rate.get("amount"),
        source="packlink_purchased_selected_rate",
        default_currency=str(rate.get("currency") or "GBP"),
    )
    if not spend.confirmed or spend.amount is None:
        return None
    return _upsert_spend(
        shipment,
        amount=spend.amount,
        currency=spend.currency or "GBP",
        source=spend.source or "packlink_purchased_selected_rate",
        source_reference=shipment.provider_shipment_id,
    )


def recover_historical_packlink_spend() -> int:
    """Idempotently backfill only already-confirmed Packlink purchases."""
    shipments = (
        FBMShipment.query
        .filter_by(provider="packlink", purchase_status="purchased")
        .filter(FBMShipment.label_purchased_at.isnot(None))
        .all()
    )
    recovered = 0
    for shipment in shipments:
        if recover_confirmed_packlink_spend(shipment) is not None:
            recovered += 1
    if recovered:
        db.session.commit()
    return recovered


def _wrap_purchase_view(app, endpoint: str) -> None:
    original = app.view_functions.get(endpoint)
    if original is None or getattr(original, "_bt38_shipping_spend_wrapped", False):
        return
    @wraps(original)
    def wrapped(*args, **kwargs):
        token = _purchase_spend.set(None)
        try:
            response = original(*args, **kwargs)
            _persist_from_response(response)
            return response
        finally:
            _purchase_spend.reset(token)
    wrapped._bt38_shipping_spend_wrapped = True
    app.view_functions[endpoint] = wrapped


def _wrap_packlink_status(app) -> None:
    endpoint = "governed_fbm.packlink_shipment_status"
    original = app.view_functions.get(endpoint)
    if original is None or getattr(original, "_bt38_packlink_spend_wrapped", False):
        return
    @wraps(original)
    def wrapped(*args, **kwargs):
        response = original(*args, **kwargs)
        flask_response = response[0] if isinstance(response, tuple) else response
        payload = flask_response.get_json(silent=True) if hasattr(flask_response, "get_json") else None
        if isinstance(payload, dict) and payload.get("success") is True and payload.get("payment_complete") is True:
            shipment_id = payload.get("shipment_id")
            if shipment_id:
                shipment = db.session.get(FBMShipment, int(shipment_id))
                if shipment is not None and recover_confirmed_packlink_spend(shipment) is not None:
                    db.session.commit()
        return response
    wrapped._bt38_packlink_spend_wrapped = True
    app.view_functions[endpoint] = wrapped


def install_governed_shipping_spend_alignment(app) -> None:
    if getattr(app, "_bt38_shipping_spend_alignment_installed", False):
        return
    with app.app_context():
        ShippingSpendLedger.__table__.create(bind=db.engine, checkfirst=True)
        recover_historical_packlink_spend()

    from services.fbm_amazon_shipping_adapter import AmazonShippingAdapter
    original_amazon_purchase = AmazonShippingAdapter.purchase_shipment
    if not getattr(original_amazon_purchase, "_bt38_shipping_spend_wrapped", False):
        @wraps(original_amazon_purchase)
        def amazon_purchase(self, *args, **kwargs):
            result = original_amazon_purchase(self, *args, **kwargs)
            if isinstance(result, dict):
                _capture(result.get("total_charge"), source="amazon_buy_shipping_purchase", reference=str(result.get("shipment_id") or "") or None)
            return result
        amazon_purchase._bt38_shipping_spend_wrapped = True
        AmazonShippingAdapter.purchase_shipment = amazon_purchase

    import services.governed_ebay_native_shipping_alignment as ebay
    original_ebay_create = ebay._create_shipment
    if not getattr(original_ebay_create, "_bt38_shipping_spend_wrapped", False):
        @wraps(original_ebay_create)
        def ebay_create(*args, **kwargs):
            result = original_ebay_create(*args, **kwargs)
            if isinstance(result, dict):
                rate = result.get("rate") if isinstance(result.get("rate"), dict) else {}
                _capture(result.get("totalShippingCost") or rate.get("totalShippingCost"), source="ebay_shipping_purchase", reference=str(result.get("shipmentId") or "") or None)
            return result
        ebay_create._bt38_shipping_spend_wrapped = True
        ebay._create_shipment = ebay_create

    _wrap_purchase_view(app, "governed_fbm.amazon_purchase")
    _wrap_purchase_view(app, "bt38_ebay_native_shipping_purchase")
    _wrap_packlink_status(app)
    app._bt38_shipping_spend_alignment_installed = True
