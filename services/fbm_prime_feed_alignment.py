"""Amazon Prime/SFP profile alignment for live and existing FBM orders.

Amazon remains the classification authority. BT38 never promotes an order to
Prime from NextDay/SecondDay/Expedited or IsPremiumOrder alone.
"""
from __future__ import annotations

from typing import Any

from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder, Store


BACKFILL_BATCH_SIZE = 20
BACKFILL_SOURCE = "amazon_exact_prime_backfill_v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def refresh_amazon_prime_profiles(*, limit: int = BACKFILL_BATCH_SIZE) -> dict[str, Any]:
    """Refresh a progressive bounded batch of existing Amazon FBM orders.

    Each successfully audited historical profile is marked with BACKFILL_SOURCE
    so later governed feed cycles move on to older unaudited orders rather than
    repeatedly reading the newest batch. Amazon's exact IsPrime fact remains the
    authority; service speed and IsPremiumOrder are never treated as Prime.
    """
    bounded_limit = max(1, min(int(limit or BACKFILL_BATCH_SIZE), 50))
    candidates = (
        db.session.query(MarketplaceOrder)
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .outerjoin(
            FBMOrderProfile,
            (FBMOrderProfile.store_id == MarketplaceOrder.store_id)
            & (FBMOrderProfile.marketplace_order_id == MarketplaceOrder.marketplace_order_id),
        )
        .filter(
            Store.is_active == True,  # noqa: E712
            Store.platform.ilike("%amazon%"),
            ~db.func.upper(db.func.coalesce(MarketplaceOrder.fulfillment_type, "")).in_(["FBA", "AFN", "MCF"]),
            db.or_(FBMOrderProfile.id.is_(None), FBMOrderProfile.source != BACKFILL_SOURCE),
        )
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        # Pull extra line rows only to allow multi-line Amazon orders to collapse
        # to one exact order read while keeping the whole SQL candidate set Amazon-only.
        .limit(min(bounded_limit * 3, 150))
        .all()
    )

    unique: dict[tuple[int, str], MarketplaceOrder] = {}
    for row in candidates:
        order_id = _text(getattr(row, "marketplace_order_id", None))
        if order_id:
            unique.setdefault((row.store_id, order_id), row)
            if len(unique) >= bounded_limit:
                break

    refreshed = 0
    prime = 0
    failures: list[dict[str, str]] = []
    from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

    for (_, order_id), row in unique.items():
        try:
            profile = get_or_refresh_amazon_profile(row, force=True)
            profile.source = BACKFILL_SOURCE
            db.session.add(profile)
            db.session.commit()
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
        "remaining_batch_candidates": max(0, len(unique) - refreshed),
        "failures": failures,
        "source": BACKFILL_SOURCE,
    }
