"""Pure shipping-spend authority for existing BT38 shipment paths.

This does not create another shipping workflow.  It defines how a confirmed
purchase amount is represented after Amazon Buy Shipping, eBay Shipping,
Packlink or a manual/seller-delivery path has persisted its shipment.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ShippingSpend:
    amount: Decimal | None
    currency: str | None
    source: str | None
    confirmed: bool


def money_amount(value: Any) -> Decimal | None:
    """Read a provider money value without inventing a missing amount."""
    candidate = value
    if isinstance(value, dict):
        candidate = next(
            (value.get(key) for key in ("value", "amount", "total", "price") if value.get(key) is not None),
            None,
        )
    if candidate in (None, ""):
        return None
    try:
        return Decimal(str(candidate))
    except (InvalidOperation, TypeError, ValueError):
        return None


def money_currency(value: Any, default: str | None = None) -> str | None:
    if isinstance(value, dict):
        currency = value.get("currency") or value.get("currencyCode") or value.get("currency_code")
        if currency:
            return str(currency).strip().upper() or None
    return str(default).strip().upper() if default else None


def confirmed_purchase_spend(value: Any, *, source: str, default_currency: str | None = None) -> ShippingSpend:
    amount = money_amount(value)
    return ShippingSpend(
        amount=amount,
        currency=money_currency(value, default_currency) if amount is not None else None,
        source=source if amount is not None else None,
        confirmed=amount is not None,
    )


def unavailable_spend() -> ShippingSpend:
    """Missing marketplace permission/data is unavailable, never zero or estimated."""
    return ShippingSpend(amount=None, currency=None, source=None, confirmed=False)
