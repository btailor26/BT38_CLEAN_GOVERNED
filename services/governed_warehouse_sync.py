"""BT38 governed marketplace refresh entry point.

All manual store-sync shortcuts delegate to the same governed marketplace
import-refresh orchestrator used by the runtime engine.
"""

from services.runtime_action_guard import is_runtime_action_allowed


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

    store = db.session.get(Store, int(store_id)) if store_id else None

    guard = is_runtime_action_allowed(
        store=store,
        action_type="sync",
        manual=True,
        context={
            "source": actor,
            "store_id": store_id,
            "single_governed_path": True,
        },
    )

    if not guard.get("allowed"):
        return {
            "success": False,
            "ok": False,
            "governed": True,
            "execution_blocked": True,
            "fuse_box_checked": True,
            "reason": guard.get("reason"),
            "mode": "governed_marketplace_import_refresh",
            "store_id": store_id,
            "guard": guard,
        }

    result = run_governed_marketplace_import_refresh(
        store_id=store_id,
        source=actor,
    )

    success = bool(result.get("success", False)) if isinstance(result, dict) else True

    return {
        "success": success,
        "ok": success,
        "governed": True,
        "manual": True,
        "mode": "governed_marketplace_import_refresh",
        "store_id": store_id,
        "guard": guard,
        "result": result,
    }
