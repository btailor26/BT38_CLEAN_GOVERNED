"""BT38 governed Warehouse Sync entry point.

Warehouse Sync performs two governed recovery actions for each switched-on
store:
1. refresh marketplace listing structure/metadata, including variation identity;
2. recover recent/pending marketplace orders.

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
            "listing_structure": listing_result,
            "order_recovery": order_result,
        })

    success = bool(stores) and all(
        bool(item.get("success")) for item in sync_results
    )
    blocked = sum(
        1 for item in sync_results if item.get("execution_blocked")
    )

    return {
        "success": success,
        "ok": success,
        "governed": True,
        "manual": True,
        "execution_blocked": bool(stores) and blocked == len(stores),
        "fuse_box_checked": True,
        "mode": "governed_listing_and_order_recovery",
        "store_id": store_id,
        "stores_checked": len(stores),
        "stores_blocked": blocked,
        "guards": guard_results,
        "results": sync_results,
        "listing_structure_reconcile_started": True,
        "pending_order_recovery_started": True,
        "marketplace_push_started": False,
        "warehouse_quantity_changed_from_marketplace": False,
    }
