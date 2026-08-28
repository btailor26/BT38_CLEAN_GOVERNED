"""Read-only presentation alignment for FBM journey and notification order identity.

Scope is deliberately narrow:
- no marketplace API calls;
- no DB writes;
- no shipping execution;
- no inventory/runtime work;
- no MCF handling or classification changes.

Historical and new records use the same persisted DB facts.  The FBM page keeps
its existing Prime/SFP badge authority (`FBMOrderProfile.is_prime`) and shipment
state authority; this module only removes numeric Journey prefixes from rendered
HTML and makes persisted sale type clearer in the global notification bell.
"""
from __future__ import annotations

from typing import Any

from flask import request
from sqlalchemy import tuple_


_JOURNEY_LABEL_REPLACEMENTS = (
    ("1 · Picked up", "Picked up"),
    ("2 · In transit", "In transit"),
    ("3 · Delivered", "Delivered"),
)


def _clean_fbm_journey_html(html: str) -> str:
    """Remove presentation-only numbering without changing milestone state."""
    value = str(html or "")
    for old, new in _JOURNEY_LABEL_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _sale_identity(record: dict[str, Any]) -> tuple[int, str] | None:
    """Read the persisted order identity encoded by the canonical bell event key."""
    if str(record.get("log_type") or "") != "marketplace_sale":
        return None
    event_key = str(record.get("event_key") or "")
    parts = event_key.split(":", 3)
    if len(parts) < 4 or parts[0] != "order":
        return None
    try:
        store_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    order_id = str(parts[2] or "").strip()
    if not order_id:
        return None
    return store_id, order_id


def _order_display_platform(
    platform: str | None,
    fulfillment_type: str | None,
    is_prime: bool | None,
) -> str:
    """Return a clear display label from persisted order/profile facts only.

    Prime is shown only when the persisted FBM profile positively says True.
    FBA/AFN remains FBA.  MCF is intentionally left untouched and outside this
    alignment's scope.
    """
    raw_platform = str(platform or "Marketplace").strip() or "Marketplace"
    normalized_platform = raw_platform.lower()
    fulfillment = str(fulfillment_type or "").strip().upper()

    if fulfillment == "MCF" or fulfillment.startswith("MCF_"):
        return raw_platform

    if "amazon" in normalized_platform:
        if fulfillment in {"FBA", "AFN", "AMAZON"}:
            return "Amazon · FBA"
        if is_prime is True:
            return "Amazon · Prime"
        if fulfillment in {"FBM", "MFN", "SELLERFULFILLED", "SELLER_FULFILLED", ""}:
            return "Amazon · FBM"
        return f"Amazon · {fulfillment}"

    if "ebay" in normalized_platform:
        if fulfillment in {"FBM", "MFN", "SELLERFULFILLED", "SELLER_FULFILLED", ""}:
            return "eBay · FBM"
        return f"eBay · {fulfillment}"

    if fulfillment:
        return f"{raw_platform} · {fulfillment}"
    return raw_platform


def _enrich_notification_records(records: list[dict[str, Any]]) -> None:
    """Add display clarity to already-persisted sale records in one DB read set."""
    from extensions import db
    from fbm_models import FBMOrderProfile
    from models import MarketplaceOrder

    identities = {
        identity
        for record in records
        if (identity := _sale_identity(record)) is not None
    }
    if not identities:
        return

    identity_list = sorted(identities)

    order_rows = (
        db.session.query(
            MarketplaceOrder.store_id,
            MarketplaceOrder.marketplace_order_id,
            MarketplaceOrder.fulfillment_type,
        )
        .filter(
            tuple_(
                MarketplaceOrder.store_id,
                MarketplaceOrder.marketplace_order_id,
            ).in_(identity_list)
        )
        .all()
    )
    fulfillment_by_identity = {
        (int(row.store_id), str(row.marketplace_order_id)): row.fulfillment_type
        for row in order_rows
        if row.store_id is not None and row.marketplace_order_id
    }

    profile_rows = (
        db.session.query(
            FBMOrderProfile.store_id,
            FBMOrderProfile.marketplace_order_id,
            FBMOrderProfile.is_prime,
        )
        .filter(
            tuple_(
                FBMOrderProfile.store_id,
                FBMOrderProfile.marketplace_order_id,
            ).in_(identity_list)
        )
        .all()
    )
    prime_by_identity = {
        (int(row.store_id), str(row.marketplace_order_id)): row.is_prime
        for row in profile_rows
        if row.store_id is not None and row.marketplace_order_id
    }

    for record in records:
        identity = _sale_identity(record)
        if identity is None:
            continue
        fulfillment = fulfillment_by_identity.get(identity)
        is_prime = prime_by_identity.get(identity)

        # MCF stays exactly outside this presentation alignment.
        normalized_fulfillment = str(fulfillment or "").strip().upper()
        if normalized_fulfillment == "MCF" or normalized_fulfillment.startswith("MCF_"):
            continue

        record["fulfillment_type"] = fulfillment
        record["is_prime"] = is_prime is True
        record["platform"] = _order_display_platform(
            record.get("platform"),
            fulfillment,
            is_prime,
        )


def install_governed_order_clarity_alignment(app) -> None:
    """Install one read-only response alignment on the existing UI paths."""
    if getattr(app, "_bt38_order_clarity_alignment_installed", False):
        return
    app._bt38_order_clarity_alignment_installed = True

    @app.after_request
    def bt38_order_clarity_response(response):
        path = request.path.rstrip("/") or "/"

        if (
            path == "/fbm"
            and response.status_code == 200
            and response.content_type
            and "text/html" in response.content_type
        ):
            response.set_data(
                _clean_fbm_journey_html(response.get_data(as_text=True))
            )
            return response

        if (
            path == "/governed/ui/notifications"
            and response.status_code == 200
            and response.is_json
        ):
            payload = response.get_json(silent=True)
            if isinstance(payload, dict):
                records = payload.get("records")
                if isinstance(records, list):
                    _enrich_notification_records(records)
                    response.set_data(app.json.dumps(payload))
                    response.headers["Content-Type"] = "application/json"
            return response

        return response

    app.logger.info(
        "BT38 order clarity alignment installed: historical Journey labels and persisted bell order identity"
    )
