"""BT38 governed marketplace refresh entry point.

All manual store-sync shortcuts delegate to the same governed marketplace
import-refresh orchestrator used by the runtime engine.
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
    from services.governed_runtime_engine import (
        run_governed_marketplace_import_refresh,
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

    guard_results = []
    import_results = []

    for store in stores:
        guard = _guard_store_sync(store, actor)
        guard_results.append({
            "store_id": getattr(store, "id", None),
            "store": getattr(store, "name", None),
            "platform": getattr(store, "platform", None),
            "guard": guard,
        })

        if not guard.get("allowed"):
            import_results.append({
                "store_id": getattr(store, "id", None),
                "store": getattr(store, "name", None),
                "platform": getattr(store, "platform", None),
                "success": False,
                "execution_blocked": True,
                "reason": guard.get("reason"),
            })
            continue

        result = run_governed_marketplace_import_refresh(
            store_id=store.id,
            source=actor,
        )
        result_success = (
            bool(result.get("success", False))
            if isinstance(result, dict)
            else True
        )
        import_results.append({
            "store_id": store.id,
            "store": getattr(store, "name", None),
            "platform": getattr(store, "platform", None),
            "success": result_success,
            "result": result,
        })

    success = bool(stores) and all(
        bool(item.get("success")) for item in import_results
    )
    blocked = sum(
        1 for item in import_results if item.get("execution_blocked")
    )

    return {
        "success": success,
        "ok": success,
        "governed": True,
        "manual": True,
        "execution_blocked": bool(stores) and blocked == len(stores),
        "fuse_box_checked": True,
        "mode": "governed_marketplace_import_refresh",
        "store_id": store_id,
        "stores_checked": len(stores),
        "stores_blocked": blocked,
        "guards": guard_results,
        "results": import_results,
    }
