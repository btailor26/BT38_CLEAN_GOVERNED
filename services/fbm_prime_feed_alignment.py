"""Amazon Prime/SFP profile alignment for live and existing FBM orders.

Amazon remains the classification authority. BT38 never promotes an order to
Prime from NextDay/SecondDay/Expedited or IsPremiumOrder alone.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder


BACKFILL_BATCH_SIZE = 20
BACKFILL_RECHECK_AFTER = timedelta(hours=24)


def _text(value: Any) -> str:
    return str(value or "").strip()


def refresh_amazon_prime_profiles(*, limit: int = BACKFILL_BATCH_SIZE) -> dict[str, Any]:
    """Refresh a bounded batch of existing Amazon FBM orders from Amazon.

    This is safe to call from the governed order-update cycle: it is bounded,
    skips FBA/AFN/MCF, and only persists marketplace-owned shipping facts. New
    orders are still refreshed immediately by the exact-order update alignment;
    this batch closes the historical gap without guessing Prime from service
    names.
    """
    cutoff = datetime.utcnow() - BACKFILL_RECHECK_AFTER
    candidates = (
        db.session.query(MarketplaceOrder)
        .outerjoin(
            FBMOrderProfile,
            (FBMOrderProfile.store_id == MarketplaceOrder.store_id)
            & (FBMOrderProfile.marketplace_order_id == MarketplaceOrder.marketplace_order_id),
        )
        .filter(
            ~db.func.upper(db.func.coalesce(MarketplaceOrder.fulfillment_type, "")).in_(["FBA", "AFN", "MCF"]),
            db.func.lower(db.func.coalesce(FBMOrderProfile.platform, "amazon")) == "amazon",
            db.or_(FBMOrderProfile.id.is_(None), FBMOrderProfile.checked_at < cutoff),
        )
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(max(1, min(int(limit or BACKFILL_BATCH_SIZE), 50)))
        .all()
    )

    # De-duplicate line rows: Amazon classification belongs to the order.
    unique: dict[tuple[int, str], MarketplaceOrder] = {}
    for row in candidates:
        store = getattr(row, "store", None)
        platform = _text(getattr(store, "platform", None)).lower() if store else ""
        order_id = _text(getattr(row, "marketplace_order_id", None))
        if store is not None and "amazon" in platform and order_id:
            unique.setdefault((row.store_id, order_id), row)

    refreshed = 0
    prime = 0
    failures: list[dict[str, str]] = []
    from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

    for (_, order_id), row in unique.items():
        try:
            profile = get_or_refresh_amazon_profile(row, force=True)
            refreshed += 1
            if getattr(profile, "is_prime", None) is True:
                prime += 1
        except Exception as exc:
            db.session.rollback()
            failures.append({"order_id": order_id, "error": str(exc)[:300]})

    return {
        "success": not failures,
        "refreshed": refreshed,
        "prime": prime,
        "failed": len(failures),
        "failures": failures,
        "source": "amazon_exact_order_bounded_backfill",
    }
