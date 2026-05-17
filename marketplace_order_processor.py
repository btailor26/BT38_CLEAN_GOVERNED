"""
BT38 marketplace order processor disabled.

Temporary fail-closed compatibility shell for shutdown phase.
All marketplace order imports, order processing, stock decrements,
marketplace polling, and follow-up push coordination are blocked.

This prevents old marketplace ingestion paths from mutating warehouse state
while the new governed execution architecture is being rebuilt.
"""

import logging
from typing import Dict, Tuple, Optional

MARKETPLACE_ORDER_PROCESSOR_DISABLED = True
LEGACY_ORDER_IMPORT_DISABLED = True


def _disabled(action: str) -> Dict:
    logging.warning("[ORDER_PROCESSOR_DISABLED] %s blocked.", action)
    return {
        "success": False,
        "ok": False,
        "execution_blocked": True,
        "order_processor_disabled": True,
        "action": action,
        "error": "Legacy marketplace order processor disabled during governed-path rebuild.",
    }


class MarketplaceOrderProcessor:
    execution_disabled = True

    @staticmethod
    def process_order(*args, **kwargs) -> Tuple[bool, str, Optional[object]]:
        return False, "Marketplace order processing disabled", None

    @staticmethod
    def get_order_status(*args, **kwargs):
        return None

    @staticmethod
    def cancel_order(*args, **kwargs) -> Tuple[bool, str]:
        return False, "Marketplace order cancellation disabled"


class OrderImportService:
    execution_disabled = True

    @staticmethod
    def import_orders_for_store(*args, **kwargs) -> Dict:
        return _disabled("import_orders_for_store")

    @staticmethod
    def run_scheduled_import(*args, **kwargs) -> Dict:
        return _disabled("run_scheduled_import")
