"""Align existing marketplace shipping purchase paths to one persisted spend ledger.

No label workflow is replaced.  The existing Amazon/eBay purchase functions run
unchanged; this module observes their confirmed provider responses and persists
only an authoritative monetary value when the marketplace actually returned one.
Missing permission/data stays unavailable rather than becoming zero or a quote.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from shipping_spend_models import ShippingSpendLedger
from services.governed_shipping_spend_policy import confirmed_purchase_spend


_purchase_spend: ContextVar[tuple[Decimal, str, str, str | None] | None] = ContextVar(
    "bt38_purchase_spend", default=None
)


def _capture(value: Any, *, source: str, reference: str | None = None) -> None:
    spend = confirmed_purchase_spend(value, source=source, default_currency="GBP")
    if spend.confirmed and spend.amount is not None:
        _purchase_spend.set((spend.amount, spend.currency or "GBP", source, reference))


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
    db.session.commit()


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


def install_governed_shipping_spend_alignment(app) -> None:
    if getattr(app, "_bt38_shipping_spend_alignment_installed", False):
        return

    # The application already uses safe create_all-on-startup semantics.  This
    # creates only the new ledger table; existing tables/columns are untouched.
    with app.app_context():
        ShippingSpendLedger.__table__.create(bind=db.engine, checkfirst=True)

    # Amazon Merchant Fulfillment createShipment returns ShippingService rate
    # data in the existing adapter as total_charge. Capture it before the route
    # can fall back to its stored pre-purchase quote.
    from services.fbm_amazon_shipping_adapter import AmazonShippingAdapter

    original_amazon_purchase = AmazonShippingAdapter.purchase_shipment
    if not getattr(original_amazon_purchase, "_bt38_shipping_spend_wrapped", False):
        @wraps(original_amazon_purchase)
        def amazon_purchase(self, *args, **kwargs):
            result = original_amazon_purchase(self, *args, **kwargs)
            if isinstance(result, dict):
                _capture(
                    result.get("total_charge"),
                    source="amazon_buy_shipping_purchase",
                    reference=str(result.get("shipment_id") or "") or None,
                )
            return result

        amazon_purchase._bt38_shipping_spend_wrapped = True
        AmazonShippingAdapter.purchase_shipment = amazon_purchase

    # eBay Logistics createFromShippingQuote can return the final purchased
    # totalShippingCost either on the shipment or its rate object.
    import services.governed_ebay_native_shipping_alignment as ebay

    original_ebay_create = ebay._create_shipment
    if not getattr(original_ebay_create, "_bt38_shipping_spend_wrapped", False):
        @wraps(original_ebay_create)
        def ebay_create(*args, **kwargs):
            result = original_ebay_create(*args, **kwargs)
            if isinstance(result, dict):
                rate = result.get("rate") if isinstance(result.get("rate"), dict) else {}
                final_cost = result.get("totalShippingCost") or rate.get("totalShippingCost")
                _capture(
                    final_cost,
                    source="ebay_shipping_purchase",
                    reference=str(result.get("shipmentId") or "") or None,
                )
            return result

        ebay_create._bt38_shipping_spend_wrapped = True
        ebay._create_shipment = ebay_create

    _wrap_purchase_view(app, "governed_fbm.amazon_purchase")
    _wrap_purchase_view(app, "bt38_ebay_native_shipping_purchase")
    app._bt38_shipping_spend_alignment_installed = True
