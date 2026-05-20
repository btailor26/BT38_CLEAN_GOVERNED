"""
BT38 GOVERNED WAREHOUSE SYNC

Single manual warehouse sync path.

Rules:
- No old queue workers
- No old auto sync
- No legacy marketplace orchestration
- Manual execution only
- Warehouse is the truth
- FBA/AFN remains read-only
- FBM/MFN can be pushed only through governed listing push path
"""

from datetime import datetime

from app import db
from models import Store, MarketplaceListing, SyncLog
from governed_routes import _push_one_listing


def run_governed_warehouse_sync(store_id=None, actor="manual-warehouse-sync"):
    query = db.session.query(MarketplaceListing)

    if store_id:
        query = query.filter(MarketplaceListing.store_id == store_id)

    listings = query.filter(
        MarketplaceListing.is_active == True,  # noqa: E712
        MarketplaceListing.warehouse_stock_id.isnot(None),
    ).order_by(MarketplaceListing.id).all()

    results = []
    pushed = 0
    blocked = 0

    for listing in listings:
        platform = (listing.store.platform or "").strip().lower() if listing.store else ""
        channel = (listing.normalized_amazon_fulfillment_channel or "").upper()

        if "amazon" in platform and channel not in ("MFN", "FBM", "MERCHANT"):
            blocked += 1
            results.append({
                "listing_id": listing.id,
                "sku": listing.external_sku,
                "platform": platform,
                "channel": channel,
                "ok": False,
                "blocked": True,
                "reason": "FBA/AFN is read-only and cannot be pushed",
            })
            continue

        result = _push_one_listing(
            listing_id=listing.id,
            quantity=None,
            actor=actor,
            source="warehouse_manual_sync_button",
        )

        if result.get("ok") or result.get("success"):
            pushed += 1
        else:
            blocked += 1

        results.append({
            "listing_id": listing.id,
            "sku": listing.external_sku,
            "platform": platform,
            "channel": channel,
            "ok": bool(result.get("ok") or result.get("success")),
            "reason": result.get("reason") or result.get("failure_reason"),
        })

    db.session.add(SyncLog(
        store_id=store_id,
        status="success" if pushed else "blocked",
        message=f"governed_warehouse_sync pushed={pushed} blocked={blocked}",
        items_synced=pushed,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "manual": True,
        "store_id": store_id,
        "total": len(results),
        "pushed": pushed,
        "blocked": blocked,
        "results": results[:100],
    }
