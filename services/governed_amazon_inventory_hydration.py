"""
BT38 governed Amazon inventory hydration compatibility shell.

Hydration writes are disabled during governed-path consolidation.
Warehouse quantity mutation from hydration is intentionally blocked.
"""

HYDRATION_DISABLED = True


def hydrate_amazon_inventory():
    return {
        "success": False,
        "governed": True,
        "hydration_disabled": True,
        "execution_blocked": True,
        "reason": "Governed Amazon inventory hydration is disabled during route consolidation.",
    }
