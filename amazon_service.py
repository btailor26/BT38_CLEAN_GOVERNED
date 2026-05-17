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


class AmazonAPIService(DisabledMarketplaceService):
    """Compatibility shell for retired Amazon API service methods."""

    AMAZON_SERVICE_DISABLED = AMAZON_SERVICE_DISABLED
    LEGACY_AMAZON_MARKETPLACE_DISABLED = LEGACY_AMAZON_MARKETPLACE_DISABLED

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
        """Single governed Amazon FBM quantity update method.

        This method is callable only from the governed Amazon FBM adapter. It
        validates FBM/MFN again, then delegates to a concrete Listings patch
        implementation if one is present. In this shutdown branch the inherited
        compatibility fallback remains disabled, so tests monkeypatch this method
        rather than making live marketplace calls.
        """
        channel = (fulfillment_channel or "").strip().upper()
        if str(sku or "").upper().startswith("FBA-") or channel in {"AFN", "FBA"}:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=sku,
                quantity=quantity,
                reason="FBA/AFN is read-only",
            )
        if channel not in {"MFN", "FBM"}:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=sku,
                quantity=quantity,
                reason="Unknown Amazon fulfillment",
            )

        patch_method = getattr(self, "update_listing_quantity_patch")
        result = patch_method(
            store=store,
            sku=sku,
            quantity=quantity,
            marketplace_id=marketplace_id,
            amazon_fulfillment_channel=channel,
        )
        return {
            "success": bool(_result_success(result)),
            "ok": bool(_result_success(result)),
            "method": "update_fbm_inventory_quantity_governed",
            "delegated_method": "update_listing_quantity_patch",
            "sku": sku,
            "quantity": quantity,
            "marketplace_id": marketplace_id,
            "command_id": command_id,
            "approval_id": approval_id,
            "raw_result": result,
        }


def _result_success(result: Any) -> bool:
    if isinstance(result, tuple) and result:
        return bool(result[0])
    if isinstance(result, dict):
        return bool(result.get("success") or result.get("ok"))
    return bool(result)


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["amazon_service_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
