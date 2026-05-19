"""BT38 Amazon legacy marketplace service disabled during shutdown proof."""

from typing import Any, Dict

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    DisabledMarketplaceService,
    disabled_response,
)

AMAZON_SERVICE_DISABLED = True
LEGACY_AMAZON_MARKETPLACE_DISABLED = True
AMAZON_FBM_GOVERNED_SERVICE_ENABLED = True


class AmazonAPIService(DisabledMarketplaceService):
    """Compatibility shell for retired Amazon API service methods."""

    AMAZON_SERVICE_DISABLED = AMAZON_SERVICE_DISABLED
    LEGACY_AMAZON_MARKETPLACE_DISABLED = LEGACY_AMAZON_MARKETPLACE_DISABLED
    AMAZON_FBM_GOVERNED_SERVICE_ENABLED = AMAZON_FBM_GOVERNED_SERVICE_ENABLED

    def update_fbm_inventory_quantity_governed(
        self,
        *,
        store,
        listing,
        sku: str,
        quantity: int,
        marketplace_id: str = None,
        fulfillment_channel: str = "MFN",
        command_id: str = None,
        approval_id: str = None,
    ) -> Dict[str, Any]:
        """Governed-only Amazon FBM execution entry point.

        This method intentionally avoids all retired sync/push/import paths and
        delegates only to the dedicated governed Amazon live PATCH module.
        """

        channel = (fulfillment_channel or "").strip().upper()
        clean_sku = str(sku or "").strip()

        if not clean_sku:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                reason="Missing Amazon SKU",
            )

        if clean_sku.upper().startswith("FBA-") or channel in {"AFN", "FBA"}:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=clean_sku,
                quantity=quantity,
                reason="FBA/AFN is read-only",
            )

        if channel not in {"MFN", "FBM"}:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=clean_sku,
                quantity=quantity,
                reason="Unknown Amazon fulfillment",
            )

        from amazon_service_live_patch import governed_amazon_quantity_patch

        result = governed_amazon_quantity_patch(
            store=store,
            listing=listing,
            sku=clean_sku,
            quantity=quantity,
            marketplace_id=marketplace_id,
            command_id=command_id,
            approval_id=approval_id,
        )

        result.update(
            {
                "method": "update_fbm_inventory_quantity_governed",
                "delegated_method": None,
                "old_sync_disabled": OLD_SYNC_DISABLED,
                "marketplace_execution_disabled": MARKETPLACE_EXECUTION_DISABLED,
                "governed_path_required": GOVERNED_PATH_REQUIRED,
            }
        )
        return result


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["amazon_service_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
