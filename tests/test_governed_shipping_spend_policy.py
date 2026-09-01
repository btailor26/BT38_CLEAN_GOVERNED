from decimal import Decimal

from services.governed_shipping_spend_policy import (
    confirmed_purchase_spend,
    unavailable_spend,
)


def test_confirmed_marketplace_purchase_keeps_amount_and_currency():
    spend = confirmed_purchase_spend(
        {"value": "2.51", "currency": "GBP"},
        source="amazon_buy_shipping",
    )
    assert spend.amount == Decimal("2.51")
    assert spend.currency == "GBP"
    assert spend.source == "amazon_buy_shipping"
    assert spend.confirmed is True


def test_permission_gated_or_missing_cost_is_not_zero():
    spend = unavailable_spend()
    assert spend.amount is None
    assert spend.currency is None
    assert spend.source is None
    assert spend.confirmed is False


def test_missing_provider_amount_is_not_estimated():
    spend = confirmed_purchase_spend({}, source="ebay_shipping", default_currency="GBP")
    assert spend.amount is None
    assert spend.currency is None
    assert spend.confirmed is False
