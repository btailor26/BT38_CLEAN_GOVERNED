"""BT38 governed Warehouse Sync entry point.

Warehouse Sync reuses the existing governed recovery paths. The Warehouse UI
can request one narrow operation at a time:

- orders: recover recent/pending marketplace orders only;
- listings: recover only genuinely missing eBay listing Item IDs through the
  existing bounded missed-listing recovery;
- default: preserve the existing combined listing-structure + order recovery
  contract for callers that have not opted into the Warehouse dropdown yet.

It never treats marketplace quantity as Warehouse truth and never starts a
marketplace quantity push.
"""

from services.runtime_action_guard import is_runtime_action_allowed


def _guard_store_sync(store, actor):
    return is_runtime_action_allowed(
        store=store,
        action_type="sync",
        manual=True,
        context={
            "source": actor,
            "store_id": getattr(store, "id", None),
            "single_governed_path": True,
        },
    )


def _warehouse_sync_mode(actor: str) -> str:
    source = str(actor or "").strip().lower()
    if source in {
        "warehouse-sync-orders",
        "warehouse-sync-button:orders",
        "warehouse_sync_orders",
    }:
        return "orders"
    if source in {
        "warehouse-sync-listings",
        "warehouse-sync-button:listings",
        "warehouse_sync_listings",
    }:
        return "listings"
    return "combined"


def run_governed_warehouse_sync(
    store_id=None,
    actor="manual-warehouse-sync",
    limit=5,
):
    from extensions import db
    from models import Store
    from services.governed_listing_structure_reconcile import (
        run_governed_listing_structure_reconcile,
    )
    from services.governed_marketplace_order_import import (
        run_governed_marketplace_order_import,
    )

    mode = _warehouse_sync_mode(actor)

    if store_id:
        stores = [db.session.get(Store, int(store_id))]
    else:
        stores = (
            Store.query
            .filter(Store.is_active == True)  # noqa: E712
            .filter(Store.store_mode == "live")
            .order_by(Store.id)
            .all()
        )

    stores = [store for store in stores if store is not None]
    guard_results = []
    sync_results = []

    for store in stores:
        guard = _guard_store_sync(store, actor)
        guard_results.append({
            "store_id": getattr(store, "id", None),
            "store": getattr(store, "name", None),
            "platform": getattr(store, "platform", None),
            "guard": guard,
        })

        if not guard.get("allowed"):
            sync_results.append({
                "store_id": getattr(store, "id", None),
                "store": getattr(store, "name", None),
                "platform": getattr(store, "platform", None),
                "success": False,
                "execution_blocked": True,
                "reason": guard.get("reason"),
            })
            continue

        platform = str(getattr(store, "platform", "") or "").strip().lower()

        if mode == "orders":
            order_result = run_governed_marketplace_order_import(
                store_id=store.id,
                source=f"{actor}:pending_order_recovery",
            )
            order_success = (
                bool(order_result.get("success", False))
                if isinstance(order_result, dict)
                else True
            )
            sync_results.append({
                "store_id": store.id,
                "store": getattr(store, "name", None),
                "platform": getattr(store, "platform", None),
                "success": order_success,
                "sync_mode": "orders",
                "listing_structure": None,
                "listing_recovery": None,
                "order_recovery": order_result,
            })
            continue

        if mode == "listings":
            if "ebay" in platform:
                from services.governed_ebay_missed_listing_recovery import (
                    recover_missed_ebay_listings,
                )

                listing_result = recover_missed_ebay_listings(
                    store_id=store.id,
                )
                listing_success = bool(
                    listing_result.get("success", False)
                    if isinstance(listing_result, dict)
                    else True
                )
                sync_results.append({
                    "store_id": store.id,
                    "store": getattr(store, "name", None),
                    "platform": getattr(store, "platform", None),
                    "success": listing_success,
                    "sync_mode": "listings",
                    "listing_structure": None,
                    "listing_recovery": listing_result,
                    "order_recovery": None,
                })
            else:
                # Amazon listing webhooks are the normal automatic path. The
                # Warehouse listing recovery option deliberately avoids starting
                # an unnecessary Amazon catalogue refresh.
                sync_results.append({
                    "store_id": store.id,
                    "store": getattr(store, "name", None),
                    "platform": getattr(store, "platform", None),
                    "success": True,
                    "sync_mode": "listings",
                    "listing_structure": None,
                    "listing_recovery": None,
                    "order_recovery": None,
                    "skipped": True,
                    "reason": "listing_webhook_primary",
                })
            continue

        # Compatibility/default contract for non-dropdown callers.
        listing_result = run_governed_listing_structure_reconcile(
            store_id=store.id,
            source=f"{actor}:listing_structure_reconcile",
        )

        order_result = run_governed_marketplace_order_import(
            store_id=store.id,
            source=f"{actor}:pending_order_recovery",
        )

        listing_success = (
            bool(listing_result.get("success", False))
            if isinstance(listing_result, dict)
            else True
        )
        order_success = (
            bool(order_result.get("success", False))
            if isinstance(order_result, dict)
            else True
        )

        sync_results.append({
            "store_id": store.id,
            "store": getattr(store, "name", None),
            "platform": getattr(store, "platform", None),
            "success": listing_success and order_success,
            "sync_mode": "combined",
            "listing_structure": listing_result,
            "listing_recovery": None,
            "order_recovery": order_result,
        })

    success = bool(stores) and all(
        bool(item.get("success")) for item in sync_results
    )
    blocked = sum(
        1 for item in sync_results if item.get("execution_blocked")
    )

    if mode == "orders":
        message = "Order recovery complete."
    elif mode == "listings":
        missing = sum(
            int((item.get("listing_recovery") or {}).get("missing") or 0)
            for item in sync_results
        )
        imported = sum(
            int((item.get("listing_recovery") or {}).get("imported") or 0)
            for item in sync_results
        )
        message = f"Listing recovery complete. Missing: {missing}. Imported: {imported}."
    else:
        message = "Warehouse sync complete."

    return {
        "success": success,
        "ok": success,
        "governed": True,
        "manual": True,
        "execution_blocked": bool(stores) and blocked == len(stores),
        "fuse_box_checked": True,
        "mode": mode,
        "store_id": store_id,
        "stores_checked": len(stores),
        "stores_blocked": blocked,
        "guards": guard_results,
        "results": sync_results,
        "listing_structure_reconcile_started": mode == "combined",
        "bounded_missing_listing_recovery_started": mode == "listings",
        "pending_order_recovery_started": mode in {"orders", "combined"},
        "marketplace_push_started": False,
        "warehouse_quantity_changed_from_marketplace": False,
        "message": message,
    }
