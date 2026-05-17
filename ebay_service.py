"""
BT38 eBay marketplace service disabled.

Temporary fail-closed compatibility shell for shutdown phase.
eBay inventory push, listing updates, imports, policy lookups,
authentication, and direct marketplace calls are blocked.

New governed execution may only be added after shutdown tests pass.
"""

import logging
from typing import Any, Dict, Tuple

MARKETPLACE_EXECUTION_DISABLED = True
EBAY_MARKETPLACE_DISABLED = True


def _disabled_response(action: str) -> Dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "execution_blocked": True,
        "marketplace_disabled": True,
        "platform": "ebay",
        "action": action,
        "error": "eBay marketplace execution is disabled during governed-path rebuild.",
    }


class eBayAPIService:
    execution_disabled = True
    marketplace_disabled = True
    platform = "ebay"

    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger(__name__)
        self.logger.warning("[MARKETPLACE_DISABLED] eBayAPIService disabled compatibility shell initialized.")

    def _blocked_tuple(self, action: str) -> Tuple[bool, str]:
        logging.warning("[MARKETPLACE_DISABLED] eBay action blocked: %s", action)
        return False, _disabled_response(action)["error"]

    def _blocked_dict(self, action: str) -> Dict[str, Any]:
        logging.warning("[MARKETPLACE_DISABLED] eBay action blocked: %s", action)
        return _disabled_response(action)

    def authenticate_store(self, *args, **kwargs) -> bool:
        logging.warning("[MARKETPLACE_DISABLED] eBay authenticate_store blocked.")
        return False

    def validate_credentials_format(self, *args, **kwargs):
        return False, "eBay marketplace service disabled"

    def push_quantity_only(self, *args, **kwargs) -> Tuple[bool, str]:
        return self._blocked_tuple("push_quantity_only")

    def get_item(self, *args, **kwargs) -> Dict[str, Any]:
        return self._blocked_dict("get_item")

    def get_ebay_official_time(self, *args, **kwargs):
        return False, "eBay marketplace service disabled"

    def get_seller_profiles(self, *args, **kwargs):
        return False, {"error": "eBay marketplace service disabled"}

    def import_inventory_from_ebay(self, *args, **kwargs):
        return False, [], "eBay marketplace service disabled"

    def resolve_item_id_by_sku(self, *args, **kwargs):
        return None

    def __getattr__(self, name: str):
        def blocked(*args, **kwargs):
            return self._blocked_dict(name)
        return blocked
