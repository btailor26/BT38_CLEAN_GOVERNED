"""BT38 eBay legacy marketplace service disabled during shutdown proof."""

from typing import Any, Dict

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    DisabledMarketplaceService,
    disabled_response,
)

EBAY_SERVICE_DISABLED = True
LEGACY_EBAY_MARKETPLACE_DISABLED = True


class eBayAPIService(DisabledMarketplaceService):
    """Compatibility shell for retired eBay API service methods."""

    EBAY_SERVICE_DISABLED = EBAY_SERVICE_DISABLED
    LEGACY_EBAY_MARKETPLACE_DISABLED = LEGACY_EBAY_MARKETPLACE_DISABLED


EbayAPIService = eBayAPIService


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["ebay_service_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
