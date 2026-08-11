"""
Services package - Business logic and external integrations
"""

# Global Amazon FBA settlement rule. Importing the services package installs a
# narrow wrapper around the existing exact-event runtime so any delayed Amazon
# FBA Seller-SKU change committed to Neon wakes the same targeted UI channel.
# It does not create a second inventory authority, worker, scan, or push path.
import services.governed_fba_settlement_ui_alignment  # noqa: F401,E402

# One shared read-side table interceptor. It handles bounded GET reads only and
# adds no routes, workers, marketplace writes, Warehouse mutations or push path.
try:
    from app import app as _bt38_app
    from services.governed_operational_table_read_alignment import (
        install_operational_table_read_alignment,
    )

    install_operational_table_read_alignment(_bt38_app)
except Exception:
    # Read-side UI alignment must never make the application fail to boot.
    pass
