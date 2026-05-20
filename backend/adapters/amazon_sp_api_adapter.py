"""
BT38 CLEAN AMAZON SP-API ADAPTER

Single governed adapter layer.

Rules:
- No old routes
- No queue workers
- No orchestration services
- No marketplace startup execution
- Inventory read only
"""

from datetime import datetime

from sp_api.api import Inventories
from sp_api.base import Marketplaces


class AmazonSPAPIAdapter:

    def __init__(self, store):

        self.store = store

        creds = store.api_key or {}

        self.client = Inventories(
            marketplace=Marketplaces.UK,
            refresh_token=creds.get("refresh_token"),
            lwa_app_id=creds.get("lwa_app_id")
                or creds.get("client_id"),
            lwa_client_secret=creds.get("lwa_client_secret")
                or creds.get("client_secret"),
            aws_access_key=creds.get("aws_access_key"),
            aws_secret_key=creds.get("aws_secret_key"),
            role_arn=creds.get("role_arn"),
        )

    def get_inventory(self):

        response = self.client.get_inventory_summary_marketplace()

        payload = response.payload or {}

        rows = payload.get("inventorySummaries") or []

        normalized = []

        for row in rows:

            inventory_details = row.get("inventoryDetails") or {}

            fulfillable = (
                inventory_details.get("fulfillableQuantity")
                or 0
            )

            normalized.append({
                "seller_sku": row.get("sellerSku"),
                "asin": row.get("asin"),
                "fnsku": row.get("fnSku"),
                "available_quantity": int(fulfillable),
                "fulfillment_channel": (
                    "AFN"
                    if row.get("fnSku")
                    else "MFN"
                ),
                "raw": row,
                "synced_at": datetime.utcnow().isoformat(),
            })

        return normalized
