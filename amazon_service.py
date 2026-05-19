"""BT38 governed Amazon FBM marketplace service."""

from __future__ import annotations

import json
from typing import Any, Dict

import requests

from old_path_shutdown import (
    DisabledMarketplaceService,
    disabled_response,
)
from store_credentials import AmazonCredentials

AMAZON_SERVICE_DISABLED = True
LEGACY_AMAZON_MARKETPLACE_DISABLED = True


class AmazonAPIService(DisabledMarketplaceService):
    """Governed-only Amazon marketplace service."""

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

        if not store:
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=sku,
                quantity=quantity,
                reason="Missing governed store",
            )

        creds = store.amazon_credentials

        if not creds or not creds.is_valid():
            return _blocked(
                "update_fbm_inventory_quantity_governed",
                sku=sku,
                quantity=quantity,
                reason="Amazon credentials invalid",
            )

        endpoint = "https://sellingpartnerapi-eu.amazon.com"

        payload = {
            "productType": "PRODUCT",
            "patches": [
                {
                    "op": "replace",
                    "path": "/attributes/fulfillment_availability",
                    "value": [
                        {
                            "fulfillment_channel_code": "DEFAULT",
                            "quantity": int(quantity),
                        }
                    ],
                }
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "x-amz-access-token": creds.refresh_token,
        }

        url = (
            f"{endpoint}/listings/2021-08-01/items/"
            f"{creds.seller_id}/{sku}"
            f"?marketplaceIds={marketplace_id or creds.marketplace_id}"
        )

        try:
            response = requests.patch(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=30,
            )

            success = response.status_code in {200, 202}

            return {
                "success": success,
                "ok": success,
                "method": "update_fbm_inventory_quantity_governed",
                "delegated_method": None,
                "sku": sku,
                "quantity": quantity,
                "marketplace_id": marketplace_id or creds.marketplace_id,
                "command_id": command_id,
                "approval_id": approval_id,
                "status_code": response.status_code,
                "response_text": response.text[:4000],
            }

        except Exception as exc:
            return {
                "success": False,
                "ok": False,
                "method": "update_fbm_inventory_quantity_governed",
                "sku": sku,
                "quantity": quantity,
                "command_id": command_id,
                "approval_id": approval_id,
                "error": str(exc),
            }


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["amazon_service_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
