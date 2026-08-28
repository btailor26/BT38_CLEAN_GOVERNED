"""Read-only presentation alignment for FBM journey and notification order identity.

Scope is deliberately narrow:
- no marketplace API calls;
- no DB writes;
- no shipping execution;
- no inventory/runtime work;
- no MCF handling or classification changes.

Historical and new records use the same persisted DB facts. The FBM page keeps
its existing Prime/SFP badge authority (`FBMOrderProfile.is_prime`) and shipment
state authority. This module removes numeric Journey prefixes, guarantees that
any persisted courier tracking for a marketplace FBM order is visible on the FBM
page regardless of marketplace, and makes persisted sale type clearer in the
global notification bell.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any

from flask import request
from sqlalchemy import tuple_


_JOURNEY_LABEL_REPLACEMENTS = (
    ("1 · Picked up", "Picked up"),
    ("2 · In transit", "In transit"),
    ("3 · Delivered", "Delivered"),
)
_FBM_ROW_RE = re.compile(
    r'(<tr class="fbm-order-row" data-order-id="(?P<row_id>\d+)">)(?P<body>.*?)(</tr>)',
    re.DOTALL,
)


def _clean_fbm_journey_html(html: str) -> str:
    """Remove presentation-only numbering without changing milestone state."""
    value = str(html or "")
    for old, new in _JOURNEY_LABEL_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _persisted_tracking_by_order_row(order_row_ids: set[int]) -> dict[int, list[dict[str, str]]]:
    """Return every DB-recorded courier tracking value for the displayed FBM orders.

    The rule is marketplace-neutral and age-neutral. Tracking can have been
    persisted either on any MarketplaceOrder line for the order or on any
    FBMShipment for that same store/order identity. No provider or marketplace
    API is called here and no record is modified.
    """
    if not order_row_ids:
        return {}

    from extensions import db
    from fbm_models import FBMShipment
    from models import MarketplaceOrder

    displayed_rows = (
        db.session.query(
            MarketplaceOrder.id,
            MarketplaceOrder.store_id,
            MarketplaceOrder.marketplace_order_id,
        )
        .filter(MarketplaceOrder.id.in_(sorted(order_row_ids)))
        .all()
    )
    identity_by_row_id = {
        int(row.id): (int(row.store_id), str(row.marketplace_order_id))
        for row in displayed_rows
        if row.id is not None and row.store_id is not None and row.marketplace_order_id
    }
    identities = sorted(set(identity_by_row_id.values()))
    if not identities:
        return {}

    tracking_by_identity: dict[tuple[int, str], list[dict[str, str]]] = {
        identity: [] for identity in identities
    }
    seen_by_identity: dict[tuple[int, str], set[str]] = {
        identity: set() for identity in identities
    }

    marketplace_rows = (
        db.session.query(
            MarketplaceOrder.store_id,
            MarketplaceOrder.marketplace_order_id,
            MarketplaceOrder.tracking_number,
            MarketplaceOrder.carrier,
        )
        .filter(
            tuple_(
                MarketplaceOrder.store_id,
                MarketplaceOrder.marketplace_order_id,
            ).in_(identities)
        )
        .all()
    )
    for row in marketplace_rows:
        identity = (int(row.store_id), str(row.marketplace_order_id))
        tracking = str(row.tracking_number or "").strip()
        if not tracking or tracking in seen_by_identity[identity]:
            continue
        seen_by_identity[identity].add(tracking)
        tracking_by_identity[identity].append({
            "tracking_number": tracking,
            "carrier": str(row.carrier or "").strip(),
            "source": "marketplace_order",
        })

    shipment_rows = (
        db.session.query(
            FBMShipment.store_id,
            FBMShipment.marketplace_order_id,
            FBMShipment.tracking_number,
            FBMShipment.carrier,
            FBMShipment.provider,
        )
        .filter(
            tuple_(
                FBMShipment.store_id,
                FBMShipment.marketplace_order_id,
            ).in_(identities),
            FBMShipment.tracking_number.isnot(None),
            FBMShipment.tracking_number != "",
        )
        .order_by(FBMShipment.id.desc())
        .all()
    )
    for row in shipment_rows:
        identity = (int(row.store_id), str(row.marketplace_order_id))
        tracking = str(row.tracking_number or "").strip()
        if not tracking or tracking in seen_by_identity[identity]:
            continue
        seen_by_identity[identity].add(tracking)
        tracking_by_identity[identity].append({
            "tracking_number": tracking,
            "carrier": str(row.carrier or row.provider or "").strip(),
            "source": "fbm_shipment",
        })

    return {
        row_id: tracking_by_identity.get(identity, [])
        for row_id, identity in identity_by_row_id.items()
    }


def _enrich_fbm_tracking_html(html: str) -> str:
    """Make every persisted courier tracking value visible on the FBM page."""
    value = str(html or "")
    row_ids = {
        int(match.group("row_id"))
        for match in _FBM_ROW_RE.finditer(value)
    }
    tracking_by_row = _persisted_tracking_by_order_row(row_ids)
    if not tracking_by_row:
        return value

    def replace_row(match: re.Match[str]) -> str:
        row_id = int(match.group("row_id"))
        body = match.group("body")
        records = tracking_by_row.get(row_id) or []
        missing = [
            record for record in records
            if escape(record["tracking_number"]) not in body
        ]
        if not missing:
            return match.group(0)

        blocks = []
        for record in missing:
            carrier = escape(record.get("carrier") or "Courier")
            tracking = escape(record["tracking_number"])
            blocks.append(
                '<div class="small mt-1 bt38-db-tracking">'
                f'<span class="text-muted">{carrier} tracking:</span> '
                f'<code>{tracking}</code>'
                '</div>'
            )
        tracking_html = "".join(blocks)

        order_cell_match = re.search(
            r'(<td>[^<]+)(<div class="small text-muted">)',
            body,
            re.DOTALL,
        )
        if order_cell_match:
            insert_at = order_cell_match.end(1)
            body = body[:insert_at] + tracking_html + body[insert_at:]
        else:
            body = tracking_html + body
        return match.group(1) + body + match.group(4)

    return _FBM_ROW_RE.sub(replace_row, value)


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


def _canonical_fulfillment(
    marketplace_fulfillment: str | None,
    profile_fulfillment: str | None,
) -> str:
    """Use only persisted fulfilment evidence; never guess a missing order type."""
    marketplace_value = str(marketplace_fulfillment or "").strip().upper()
    profile_value = str(profile_fulfillment or "").strip().upper()
    return marketplace_value or profile_value


def _order_display_platform(
    platform: str | None,
    fulfillment_type: str | None,
    is_prime: bool | None,
) -> str:
    """Return a clear order label from persisted facts only.

    Prime is shown only when the persisted FBM profile positively says True.
    FBA/AFN remains FBA. Unknown historical types remain neutral rather than
    being guessed. MCF is deliberately left untouched and outside this scope.
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
        if fulfillment in {"FBM", "MFN", "SELLERFULFILLED", "SELLER_FULFILLED"}:
            return "Amazon · FBM"
        if fulfillment:
            return f"Amazon · {fulfillment}"
        return "Amazon · Order"

    if "ebay" in normalized_platform:
        if fulfillment in {"FBM", "MFN", "SELLERFULFILLED", "SELLER_FULFILLED"}:
            return "eBay · FBM"
        if fulfillment:
            return f"eBay · {fulfillment}"
        return "eBay · Order"

    if fulfillment:
        return f"{raw_platform} · {fulfillment}"
    return raw_platform


def _enrich_notification_records(records: list[dict[str, Any]]) -> None:
    """Add clarity to displayed persisted sale records in one read-only DB pass."""
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
    marketplace_fulfillment_by_identity = {
        (int(row.store_id), str(row.marketplace_order_id)): row.fulfillment_type
        for row in order_rows
        if row.store_id is not None and row.marketplace_order_id
    }

    profile_rows = (
        db.session.query(
            FBMOrderProfile.store_id,
            FBMOrderProfile.marketplace_order_id,
            FBMOrderProfile.is_prime,
            FBMOrderProfile.fulfillment_channel,
        )
        .filter(
            tuple_(
                FBMOrderProfile.store_id,
                FBMOrderProfile.marketplace_order_id,
            ).in_(identity_list)
        )
        .all()
    )
    profile_by_identity = {
        (int(row.store_id), str(row.marketplace_order_id)): (
            row.is_prime,
            row.fulfillment_channel,
        )
        for row in profile_rows
        if row.store_id is not None and row.marketplace_order_id
    }

    for record in records:
        identity = _sale_identity(record)
        if identity is None:
            continue

        profile_prime, profile_fulfillment = profile_by_identity.get(
            identity,
            (None, None),
        )
        fulfillment = _canonical_fulfillment(
            marketplace_fulfillment_by_identity.get(identity),
            profile_fulfillment,
        )

        # MCF stays exactly outside this presentation alignment.
        if fulfillment == "MCF" or fulfillment.startswith("MCF_"):
            continue

        record["fulfillment_type"] = fulfillment or None
        record["is_prime"] = profile_prime is True
        record["platform"] = _order_display_platform(
            record.get("platform"),
            fulfillment,
            profile_prime,
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
                _enrich_fbm_tracking_html(
                    _clean_fbm_journey_html(response.get_data(as_text=True))
                )
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
        "BT38 order clarity alignment installed: historical Journey labels, all persisted FBM tracking, and persisted bell order identity"
    )
