"""Amazon REST API client disabled during shutdown proof."""

from old_path_shutdown import DisabledMarketplaceService, disabled_response

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True
AMAZON_REST_API_DISABLED = True


class AmazonRestAPIClient(DisabledMarketplaceService):
    """Compatibility shell for retired Amazon REST API methods."""

    AMAZON_REST_API_DISABLED = AMAZON_REST_API_DISABLED

    def update_inventory_quantity(self, sku: str, quantity: int, seller_id: str):
        result = disabled_response(
            "amazon_rest_api.update_inventory_quantity",
            sku=sku,
            quantity=quantity,
            seller_id=seller_id,
        )
        return False, result["error"]
