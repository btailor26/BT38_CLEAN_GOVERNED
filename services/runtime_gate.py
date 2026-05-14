"""BT38 centralized runtime gate.

All push/sync/import execution paths must pass through this module.
Do not import runtime gate logic from routes.py.
"""

def is_runtime_action_allowed(store=None, action_type="unknown", manual=False):
    """Central BT38 runtime gate for push/sync/import actions."""
    try:
        from models import PushSettings

        settings = PushSettings.query.first()
        global_push_enabled = True

        if settings and hasattr(settings, "global_push_enabled"):
            global_push_enabled = bool(settings.global_push_enabled)

        if not global_push_enabled and action_type in {"push", "sync", "import"}:
            return False, "Global push/sync/import is disabled in Settings."

        if store is not None:
            if hasattr(store, "is_active") and not store.is_active:
                return False, "Store is inactive in Settings."

            if action_type == "push":
                if hasattr(store, "auto_push_enabled") and not store.auto_push_enabled and not manual:
                    return False, "Store auto push is disabled in Settings."

                if hasattr(store, "fbm_sync_enabled") and not store.fbm_sync_enabled:
                    return False, "FBM sync is disabled for this store in Settings."

            if action_type == "import":
                if hasattr(store, "fba_import_enabled") and not store.fba_import_enabled:
                    return False, "FBA/import is disabled for this store in Settings."

        return True, "Runtime action allowed by Settings."

    except Exception as e:
        return False, f"Runtime gate error: {str(e)}"
