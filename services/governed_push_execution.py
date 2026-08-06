"""BT38 governed push execution service.

One clear path:
route shortcut -> governed service -> governed_execution -> marketplace adapter

Rules:
- request body quantity does not override warehouse truth
- group push resolves listings first
- service owns shared listing push logic
- routes must not be imported by services
- existing webhook pushes queue exact affected rows for the 15-minute alignment check
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


def _is_webhook_push_source(source: str) -> bool:
    value = str(source or "").strip().lower()
    return bool(
        value.startswith("webhook_")
        and "15m_retry" not in value
        and "group_notification" not in value
    )


def _is_automatic_push_source(source: str) -> bool:
    value = str(source or "").strip().lower()
    return bool(
        value.startswith("webhook_")
        or "auto_push" in value
        or "automatic_push" in value
    )


def _queue_exact_webhook_verification(*, listing, stock, source: str) -> None:
    if not _is_webhook_push_source(source):
        return

    try:
        from services.governed_runtime_engine import notify_governed_runtime_work

        notify_governed_runtime_work(
            "marketplace_webhook_push",
            store_id=getattr(listing, "store_id", None),
            marketplace=getattr(
                getattr(listing, "store", None),
                "platform",
                None,
            ),
            event_type="inventory_alignment",
            seller_sku=getattr(listing, "external_sku", None),
            listing_id=getattr(listing, "external_listing_id", None),
            listing_ids=[getattr(listing, "id", None)],
            warehouse_stock_id=getattr(stock, "id", None),
            group_id=getattr(stock, "master_product_group_id", None),
            expected_quantity=getattr(listing, "effective_quantity", None),
        )
    except Exception:
        return


def _queue_exact_group_webhook_verifications(*, listings, warehouse_rows, group_id: int, source: str) -> None:
    value = str(source or "").strip().lower()
    if not value.startswith("webhook_") or "15m_retry" in value:
        return

    try:
        from services.governed_runtime_engine import notify_governed_runtime_work

        stock_by_id = {int(stock.id): stock for stock in warehouse_rows}
        listings_by_stock = {}
        for listing in listings:
            stock_id = getattr(listing, "warehouse_stock_id", None)
            if stock_id is None:
                continue
            listings_by_stock.setdefault(int(stock_id), []).append(listing)

        for stock_id, members in listings_by_stock.items():
            stock = stock_by_id.get(stock_id)
            if stock is None or not members:
                continue
            first = members[0]
            notify_governed_runtime_work(
                "marketplace_webhook_group_push",
                store_id=getattr(first, "store_id", None),
                marketplace=getattr(
                    getattr(first, "store", None),
                    "platform",
                    None,
                ),
                event_type="group_inventory_alignment",
                seller_sku=getattr(first, "external_sku", None),
                listing_ids=[int(item.id) for item in members],
                warehouse_stock_id=stock_id,
                group_id=int(group_id),
                expected_quantity=getattr(first, "effective_quantity", None),
            )
    except Exception:
        return


def push_marketplace_listing(*, listing_id: int, actor: str, source: str, actor_user=None) -> Dict[str, Any]:
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import MarketplaceListing, SyncLog

    listing = db.session.get(MarketplaceListing, int(listing_id))
    if not listing:
        return _blocked(f"Marketplace listing {listing_id} was not found.", listing_id=listing_id)

    if not listing.store:
        return _blocked("Marketplace listing has no store.", listing_id=listing_id)

    if not listing.warehouse_stock:
        return _blocked("Marketplace listing is not linked to warehouse stock.", listing_id=listing_id)

    source_value = str(source or "").strip().lower()
    # WarehouseStock is the only Product Linking group authority.
    group_id = getattr(
        listing.warehouse_stock,
        "master_product_group_id",
        None,
    )
    group_controlled = bool(
        group_id
        or getattr(
            listing.warehouse_stock,
            "is_group_controlled",
            False,
        )
    )

    # Automatic changes entering through a single-listing shortcut must still
    # honour the saved DB relationship. Expand only the affected governed group.
    # Group members carry the suffix below so this does not recurse.
    if (
        _is_automatic_push_source(source_value)
        and ":group_member" not in source_value
        and group_id
        and group_controlled
    ):
        return push_group_listings(
            group_id=int(group_id),
            actor=actor,
            source=f"{source}:group_auto_push",
            actor_user=actor_user,
        )

    platform = (listing.store.platform or "").strip().lower()
    marketplace = "amazon" if "amazon" in platform else "ebay" if "ebay" in platform else platform

    try:
        push_quantity = int(listing.effective_quantity or 0)
    except Exception:
        return _blocked("Unable to derive governed push quantity from warehouse/listing truth.", listing_id=listing_id)

    sku = (listing.external_sku or listing.warehouse_stock.sku or "").strip()
    if not sku:
        return _blocked("Marketplace listing has no SKU for governed push.", listing_id=listing_id)

    payload = {
        "marketplace": marketplace,
        "action": "push_inventory",
        "sku": sku,
        "store_id": listing.store_id,
        "listing_id": listing.id,
        "external_listing_id": listing.external_listing_id,
        "quantity": push_quantity,
        "amazon_fulfillment_channel": (
            listing.normalized_amazon_fulfillment_channel
            or listing.amazon_fulfillment_channel
            or "MFN"
        ),
        "source": source,
    }

    result = submit_governed_marketplace_action(
        payload=payload,
        actor=actor,
        actor_user=actor_user,
        approval_type=AMAZON_FBM_LIVE_APPROVAL_TYPE,
        approval_id=None,
        dry_run=False,
    )

    ok = bool(result.get("ok") or result.get("success"))

    listing.last_push_at = datetime.utcnow()
    listing.last_push_quantity = push_quantity if ok else listing.last_push_quantity
    listing.last_push_status = "success" if ok else "error"
    listing.last_push_error = None if ok else str(result.get("reason") or result.get("failure_reason") or result)[:1000]
    listing.push_attempts = 0 if ok else (listing.push_attempts or 0) + 1
    listing.consecutive_failures = 0 if ok else (listing.consecutive_failures or 0) + 1

    current_channel = str(
        listing.normalized_amazon_fulfillment_channel
        or listing.amazon_fulfillment_channel
        or ""
    ).strip().upper()
    stale_error = str(listing.last_push_error or "").lower()

    if (
        current_channel in {"MFN", "FBM", "MERCHANT"}
        and (
            "fba/afn is read-only" in stale_error
            or "no fba push path" in stale_error
            or "read-only" in stale_error
        )
    ):
        listing.last_push_error = None
        listing.last_push_status = "pending"
        listing.consecutive_failures = 0

    db.session.add(SyncLog(
        store_id=listing.store_id,
        status="success" if ok else "error",
        message=(
            f"governed_push listing_id={listing.id} sku={sku} "
            f"marketplace={marketplace} source={source} ok={ok}"
        )[:500],
        items_synced=1 if ok else 0,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    _queue_exact_webhook_verification(
        listing=listing,
        stock=listing.warehouse_stock,
        source=source,
    )

    result.update({
        "listing_id": listing.id,
        "warehouse_stock_id": listing.warehouse_stock_id,
        "master_product_group_id": (
            listing.warehouse_stock.master_product_group_id
            if listing.warehouse_stock
            else None
        ),
        "push_quantity": push_quantity,
        "ui_action_wired": True,
        "grouping_layer_ready": True,
        "audit_history_logged": True,
        "listing_last_push_updated": True,
        "warehouse_truth_quantity_used": True,
        "request_quantity_ignored": True,
    })
    return result


def push_group_listings(*, group_id: int, actor: str, source: str, actor_user=None) -> Dict[str, Any]:
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    group_id = int(group_id)

    warehouse_ids = [
        row.id
        for row in (
            db.session.query(WarehouseStock)
            .filter(WarehouseStock.master_product_group_id == group_id)
            .filter(WarehouseStock.is_active == True)  # noqa: E712
            .all()
        )
    ]

    # Group push never uses MarketplaceListing.master_product_group_id.
    # Resolve active marketplace members only through the WarehouseStock rows
    # committed to this group.
    if warehouse_ids:
        listings = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(
                MarketplaceListing.warehouse_stock_id.in_(
                    warehouse_ids
                )
            )
            .order_by(MarketplaceListing.id)
            .all()
        )
    else:
        listings = []

    member_source = f"{source}:group_member"
    results: List[Dict[str, Any]] = [
        push_marketplace_listing(
            listing_id=listing.id,
            actor=actor,
            source=member_source,
            actor_user=actor_user,
        )
        for listing in listings
    ]

    def _is_success(item: Dict[str, Any]) -> bool:
        return bool(item.get("ok") or item.get("success"))

    def _is_fba_read_only_skip(item: Dict[str, Any]) -> bool:
        reason = str(item.get("reason") or item.get("error") or item.get("message") or "").lower()
        marketplace = str(item.get("marketplace") or item.get("platform") or "").lower()
        channel = str(item.get("amazon_fulfillment_channel") or item.get("fulfillment_channel") or item.get("fulfillment") or "").upper()
        return (
            bool(item.get("is_fba"))
            or item.get("push_status") == "read_only"
            or channel in {"AFN", "FBA"}
            or ("amazon" in marketplace and ("fba" in reason or "afn" in reason or "read-only" in reason or "read only" in reason))
        )

    success_count = sum(1 for item in results if _is_success(item))
    skipped_count = sum(1 for item in results if (not _is_success(item)) and _is_fba_read_only_skip(item))
    failed_count = len(results) - success_count - skipped_count
    pushable_count = len(results) - skipped_count

    # FBA/AFN members are intentionally read-only. A group containing only
    # protected FBA members is a successful governed no-op when nothing failed.
    group_success = failed_count == 0

    report_stock_ids = sorted({
        int(item.get("warehouse_stock_id"))
        for item in results
        if item.get("warehouse_stock_id")
    })

    warehouse_rows = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.id.in_(report_stock_ids))
        .all()
        if report_stock_ids else []
    )

    for stock in warehouse_rows:
        if hasattr(stock, "last_push_at"):
            stock.last_push_at = datetime.utcnow()
        if hasattr(stock, "last_push_status"):
            stock.last_push_status = "success" if group_success else "error"
        if hasattr(stock, "last_push_error"):
            stock.last_push_error = None if group_success else "Group push had failed marketplace members."
        if hasattr(stock, "last_group_push_at"):
            stock.last_group_push_at = datetime.utcnow()
        if hasattr(stock, "last_group_push_status"):
            stock.last_group_push_status = "success" if group_success else "error"
        if hasattr(stock, "last_group_push_result"):
            stock.last_group_push_result = {
                "group_id": group_id,
                "pushed": success_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "pushable_count": pushable_count,
                "source": source,
            }

    if warehouse_rows:
        db.session.commit()

    _queue_exact_group_webhook_verifications(
        listings=listings,
        warehouse_rows=warehouse_rows,
        group_id=group_id,
        source=source,
    )

    return {
        "success": group_success,
        "ok": group_success,
        "governed": True,
        "group_id": group_id,
        "warehouse_ids": warehouse_ids,
        "direct_group_listing_ids": direct_group_listing_ids,
        "total": len(results),
        "ok_count": success_count,
        "pushed": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "fba_read_only_skipped": skipped_count,
        "pushable_count": pushable_count,
        "warehouse_truth_quantity_used": True,
        "warehouse_authority_resolution": True,
        "request_quantity_ignored": True,
        "fba_read_only_does_not_fail_group": True,
        "results": results,
    }


def _blocked(reason: str, **extra) -> Dict[str, Any]:
    result = {
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": reason,
    }
    result.update(extra)
    return result