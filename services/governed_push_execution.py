"""BT38 governed push execution service.

One clear path:
route shortcut -> governed service -> governed_execution -> marketplace adapter

Rules:
- request body quantity does not override warehouse truth
- group push resolves current Product Linking members first
- one Warehouse row supplies one shared target quantity for the whole group
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


def _is_fba_listing(listing) -> bool:
    platform = str(
        getattr(getattr(listing, "store", None), "platform", "") or ""
    ).strip().lower()
    channel = str(
        getattr(listing, "normalized_amazon_fulfillment_channel", None)
        or getattr(listing, "amazon_fulfillment_channel", None)
        or ""
    ).strip().upper()
    explicit_fba = bool(getattr(listing, "is_fba", False))
    is_amazon = "amazon" in platform
    is_fbm = (
        is_amazon
        and not explicit_fba
        and channel in {"MFN", "FBM", "MERCHANT"}
    )
    return bool(is_amazon and (explicit_fba or not is_fbm))


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
            group_id=getattr(listing, "master_product_group_id", None),
            expected_quantity=getattr(stock, "sellable_quantity", None),
        )
    except Exception:
        return


def _queue_exact_group_webhook_verifications(*, listings, warehouse_rows, group_id: int, source: str, target_quantity: int) -> None:
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
                expected_quantity=int(target_quantity),
            )
    except Exception:
        return


def push_marketplace_listing(
    *,
    listing_id: int,
    actor: str,
    source: str,
    actor_user=None,
    dry_run: bool = False,
) -> Dict[str, Any]:
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
    group_id = getattr(listing, "master_product_group_id", None)
    group_controlled = bool(
        group_id
        or getattr(listing.warehouse_stock, "is_group_controlled", False)
    )

    # Automatic single-listing entry points always expand through the same
    # group engine and use the exact changed Warehouse row as group authority.
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
            authority_warehouse_stock_id=listing.warehouse_stock_id,
            dry_run=dry_run,
        )

    platform = (listing.store.platform or "").strip().lower()
    marketplace = "amazon" if "amazon" in platform else "ebay" if "ebay" in platform else platform

    # FBA/AFN is read-only. Never call the marketplace writer for it.
    if _is_fba_listing(listing):
        listing.last_push_at = datetime.utcnow()
        listing.last_push_status = "read_only"
        listing.last_push_error = None
        listing.push_attempts = 0
        listing.consecutive_failures = 0
        db.session.commit()
        return {
            "success": False,
            "ok": False,
            "governed": True,
            "listing_id": listing.id,
            "warehouse_stock_id": listing.warehouse_stock_id,
            "marketplace": marketplace,
            "amazon_fulfillment_channel": (
                listing.normalized_amazon_fulfillment_channel
                or listing.amazon_fulfillment_channel
                or "FBA"
            ),
            "is_fba": True,
            "push_status": "read_only",
            "reason": "Amazon FBA/AFN is read-only and was not written.",
        }

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
        dry_run=bool(dry_run),
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
            f"event_type={'marketplace_push_succeeded' if ok else 'marketplace_push_failed'} "
            f"automatic={_is_automatic_push_source(source_value)} "
            f"actor={actor} source={source} "
            f"store_id={listing.store_id} marketplace={marketplace} "
            f"listing_id={listing.id} sku={sku} "
            f"warehouse_stock_id={listing.warehouse_stock_id} "
            f"group_id={group_id} quantity={push_quantity} ok={ok}"
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
        "master_product_group_id": group_id,
        "push_quantity": push_quantity,
        "ui_action_wired": True,
        "grouping_layer_ready": True,
        "audit_history_logged": True,
        "listing_last_push_updated": True,
        "warehouse_truth_quantity_used": True,
        "request_quantity_ignored": True,
    })
    return result


def push_group_listings(
    *,
    group_id: int,
    actor: str,
    source: str,
    actor_user=None,
    authority_warehouse_stock_id: int | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    group_id = int(group_id)

    # Current Product Linking membership is owned only by MarketplaceListing.
    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .filter(MarketplaceListing.master_product_group_id == group_id)
        .filter(MarketplaceListing.warehouse_stock_id.isnot(None))
        .order_by(MarketplaceListing.id)
        .all()
    )

    if not listings:
        return _blocked(
            "No active marketplace listings belong to the requested Product Linking group.",
            group_id=group_id,
        )

    warehouse_ids = sorted({
        int(listing.warehouse_stock_id)
        for listing in listings
        if listing.warehouse_stock_id is not None
    })

    warehouse_rows = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.id.in_(warehouse_ids))
        .all()
    )
    stock_by_id = {int(stock.id): stock for stock in warehouse_rows}

    # FBA-led groups take their one shared quantity from the already-refreshed
    # Amazon read-only cache. The decision and Warehouse handoff live here so
    # webhook, Warehouse and Product Linking entry points use this same group
    # service. No caller-supplied quantity is accepted.
    fba_listing = next(
        (listing for listing in listings if _is_fba_listing(listing)),
        None,
    )
    fba_quantity = None
    if fba_listing is not None:
        confirmed_quantity = getattr(
            fba_listing,
            "last_marketplace_qty",
            None,
        )
        if confirmed_quantity is None:
            return _blocked(
                "Confirmed FBA quantity is missing; group propagation stopped.",
                group_id=group_id,
                listing_id=int(fba_listing.id),
                warehouse_stock_id=int(fba_listing.warehouse_stock_id),
            )
        try:
            fba_quantity = max(0, int(confirmed_quantity))
        except (TypeError, ValueError):
            return _blocked(
                "Confirmed FBA quantity is invalid; group propagation stopped.",
                group_id=group_id,
                listing_id=int(fba_listing.id),
                warehouse_stock_id=int(fba_listing.warehouse_stock_id),
            )

    authority_stock = None
    if fba_listing is not None:
        authority_stock = stock_by_id.get(
            int(fba_listing.warehouse_stock_id)
        )
    if (
        fba_listing is None
        and authority_warehouse_stock_id not in (None, "")
    ):
        try:
            authority_stock_id = int(authority_warehouse_stock_id)
        except (TypeError, ValueError):
            return _blocked(
                "authority_warehouse_stock_id must be an integer.",
                group_id=group_id,
            )

        if authority_stock_id not in warehouse_ids:
            return _blocked(
                "Warehouse authority row does not belong to the current Product Linking group.",
                group_id=group_id,
                warehouse_stock_id=authority_stock_id,
            )
        authority_stock = stock_by_id.get(authority_stock_id)

    # When no exact changed row is supplied, use the group's permanent master
    # Warehouse identity. Fall back deterministically only if legacy data lacks it.
    if authority_stock is None:
        for listing in listings:
            stock = stock_by_id.get(int(listing.warehouse_stock_id))
            if (
                stock is not None
                and getattr(stock, "master_product_group_id", None) == group_id
            ):
                authority_stock = stock
                break

    if authority_stock is None and warehouse_rows:
        authority_stock = sorted(warehouse_rows, key=lambda row: int(row.id))[0]

    if authority_stock is None:
        return _blocked(
            "Warehouse group quantity authority could not be resolved.",
            group_id=group_id,
        )

    if not bool(getattr(authority_stock, "is_active", False)):
        return _blocked(
            "Warehouse group quantity authority is inactive.",
            group_id=group_id,
            warehouse_stock_id=authority_stock.id,
        )

    # One Warehouse row supplies one shared target quantity for normal groups;
    # an FBA-led group uses the committed Amazon cache from that row's listing.
    # Request body quantity does not override Warehouse truth.
    target_quantity = (
        int(fba_quantity)
        if fba_quantity is not None
        else int(getattr(authority_stock, "sellable_quantity", 0) or 0)
    )

    # Synchronize every member Warehouse row to the same SELLABLE quantity.
    # Relationship identity is never changed here.
    now = datetime.utcnow()
    for stock in warehouse_rows:
        reserved = int(getattr(stock, "reserved_quantity", 0) or 0)
        allocated = int(getattr(stock, "allocated_quantity", 0) or 0)
        stock.available_quantity = int(target_quantity + reserved + allocated)
        stock.updated_at = now
    db.session.flush()

    member_source = f"{source}:group_member"
    results: List[Dict[str, Any]] = []
    for listing in listings:
        if _is_fba_listing(listing):
            listing.last_push_at = datetime.utcnow()
            listing.last_push_status = "read_only"
            listing.last_push_error = None
            listing.push_attempts = 0
            listing.consecutive_failures = 0
            results.append({
                "success": False,
                "ok": False,
                "governed": True,
                "listing_id": listing.id,
                "warehouse_stock_id": listing.warehouse_stock_id,
                "marketplace": "amazon",
                "amazon_fulfillment_channel": (
                    listing.normalized_amazon_fulfillment_channel
                    or listing.amazon_fulfillment_channel
                    or "FBA"
                ),
                "is_fba": True,
                "push_status": "read_only",
                "quantity": target_quantity,
                "reason": "Amazon FBA/AFN is read-only and was not written.",
            })
            continue

        result = push_marketplace_listing(
            listing_id=listing.id,
            actor=actor,
            source=member_source,
            actor_user=actor_user,
            dry_run=dry_run,
        )
        result.setdefault("quantity", target_quantity)
        results.append(result)

    def _is_success(item: Dict[str, Any]) -> bool:
        return bool(item.get("ok") or item.get("success"))

    def _is_fba_read_only_skip(item: Dict[str, Any]) -> bool:
        return bool(
            item.get("is_fba")
            or item.get("push_status") == "read_only"
        )

    success_count = sum(1 for item in results if _is_success(item))
    skipped_count = sum(
        1
        for item in results
        if (not _is_success(item)) and _is_fba_read_only_skip(item)
    )
    failed_count = len(results) - success_count - skipped_count
    pushable_count = len(results) - skipped_count
    group_success = failed_count == 0
    failed_reasons = [
        str(item.get("error") or item.get("reason") or item.get("message"))
        for item in results
        if (not _is_success(item))
        and (not _is_fba_read_only_skip(item))
        and (item.get("error") or item.get("reason") or item.get("message"))
    ]

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
                "authority_warehouse_stock_id": int(authority_stock.id),
                "target_quantity": target_quantity,
                "pushed": success_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "pushable_count": pushable_count,
                "source": source,
            }

    db.session.commit()

    _queue_exact_group_webhook_verifications(
        listings=listings,
        warehouse_rows=warehouse_rows,
        group_id=group_id,
        source=source,
        target_quantity=target_quantity,
    )

    response = {
        "success": group_success,
        "ok": group_success,
        "governed": True,
        "changed": True,
        "group_id": group_id,
        "warehouse_stock_id": int(authority_stock.id),
        "authority_warehouse_stock_id": int(authority_stock.id),
        "target_quantity": target_quantity,
        "fba_read_only_authority_used": fba_quantity is not None,
        "dry_run": bool(dry_run),
        "warehouse_ids": warehouse_ids,
        "affected_group_ids": [group_id],
        "affected_listing_ids": [int(listing.id) for listing in listings],
        "affected_warehouse_stock_ids": warehouse_ids,
        "direct_group_listing_ids": [int(listing.id) for listing in listings],
        "total": len(results),
        "total_listings": len(results),
        "ok_count": success_count,
        "pushed": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "fba_read_only_skipped": skipped_count,
        "pushable_count": pushable_count,
        "warehouse_truth_quantity_used": True,
        "warehouse_authority_resolution": True,
        "one_shared_group_quantity": True,
        "request_quantity_ignored": True,
        "fba_read_only_does_not_fail_group": True,
        "results": results,
    }
    if failed_reasons:
        # Give every shortcut the real marketplace/settings failure instead of
        # forcing the UI to replace it with "Warehouse group push failed".
        response["error"] = failed_reasons[0]
        response["message"] = failed_reasons[0]
    return response


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
