"""
Services package - Business logic and external integrations
"""

# Global Amazon FBA settlement rule. Importing the services package installs a
# narrow wrapper around the existing exact-event runtime so any delayed Amazon
# FBA Seller-SKU change committed to Neon wakes the same targeted UI channel.
# It does not create a second inventory authority, worker, scan, or push path.
import services.governed_fba_settlement_ui_alignment  # noqa: F401,E402


# Operational-table read alignment is installed lazily on the first request.
# The services package can be imported while app.py is still registering its
# blueprints, so installing the FBA replacement at import time would race the
# existing governed endpoint. A before-request hook waits until Flask startup is
# complete, then replaces that existing reader in-place exactly once.
try:
    from app import app as _bt38_app
    from services.governed_operational_table_read_alignment import (
        install_operational_table_read_alignment,
    )

    @_bt38_app.before_request
    def _bt38_install_operational_table_read_alignment_once():
        if getattr(_bt38_app, "_bt38_operational_table_read_alignment_ready", False):
            return None

        result = install_operational_table_read_alignment(_bt38_app)
        if result.get("installed"):
            _bt38_app._bt38_operational_table_read_alignment_ready = True
        return None
except Exception:
    # Startup remains governed by app/main. Read-side UI alignment must never
    # make the application fail to boot or create a fallback execution path.
    pass
