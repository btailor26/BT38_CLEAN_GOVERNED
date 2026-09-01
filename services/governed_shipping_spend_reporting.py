"""Read-only reporting over BT38's persisted shipping spend authority."""
from __future__ import annotations

from decimal import Decimal

from flask import jsonify, request
from flask_login import login_required
from sqlalchemy import func

from extensions import db
from shipping_spend_models import ShippingSpendLedger


def _money(value) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def _row_payload(row: ShippingSpendLedger) -> dict:
    return {
        "id": row.id,
        "dispatch_key": row.dispatch_key,
        "shipment_id": row.shipment_id,
        "store_id": row.store_id,
        "marketplace_order_id": row.marketplace_order_id,
        "fulfillment_family": row.fulfillment_family,
        "provider": row.provider,
        "amount": _money(row.amount),
        "currency": row.currency,
        "source": row.source,
        "source_reference": row.source_reference,
        "confirmed": bool(row.confirmed),
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
    }


def install_governed_shipping_spend_reporting(app) -> None:
    if getattr(app, "_bt38_shipping_spend_reporting_installed", False):
        return

    @login_required
    def shipping_spend_report():
        family = str(request.args.get("family") or "").strip().upper()
        provider = str(request.args.get("provider") or "").strip().lower()
        query = ShippingSpendLedger.query.filter_by(confirmed=True)
        if family:
            query = query.filter(func.upper(ShippingSpendLedger.fulfillment_family) == family)
        if provider:
            query = query.filter(func.lower(ShippingSpendLedger.provider) == provider)
        rows = query.order_by(ShippingSpendLedger.recorded_at.desc(), ShippingSpendLedger.id.desc()).limit(500).all()

        totals: dict[str, Decimal] = {}
        provider_totals: dict[str, dict[str, Decimal]] = {}
        family_totals: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            currency = str(row.currency or "GBP").upper()
            amount = Decimal(str(row.amount or 0))
            totals[currency] = totals.get(currency, Decimal("0")) + amount
            provider_key = str(row.provider or "unknown")
            provider_totals.setdefault(provider_key, {})[currency] = provider_totals.setdefault(provider_key, {}).get(currency, Decimal("0")) + amount
            family_key = str(row.fulfillment_family or "unknown").upper()
            family_totals.setdefault(family_key, {})[currency] = family_totals.setdefault(family_key, {}).get(currency, Decimal("0")) + amount

        def money_map(values):
            return {currency: _money(amount) for currency, amount in sorted(values.items())}

        return jsonify({
            "success": True,
            "authority": "shipping_spend_ledger",
            "actual_recorded_spend_only": True,
            "dispatch_count": len(rows),
            "totals": money_map(totals),
            "provider_totals": {key: money_map(value) for key, value in sorted(provider_totals.items())},
            "family_totals": {key: money_map(value) for key, value in sorted(family_totals.items())},
            "entries": [_row_payload(row) for row in rows],
            "unavailable_costs_are_zero": False,
        })

    app.add_url_rule(
        "/fbm/shipping-spend",
        endpoint="bt38_shipping_spend_report",
        view_func=shipping_spend_report,
        methods=["GET"],
    )
    app._bt38_shipping_spend_reporting_installed = True
