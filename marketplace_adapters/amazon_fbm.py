"""Amazon FBM governed adapter.

No Amazon work is performed for dry-run. Live execution is only reachable from
`execute_governed_action()` after approval, runtime gate, and FBM eligibility
checks pass. FBA/AFN and unknown fulfillment fail closed again here.
"""

from __future__ import annotations

from typing import Any, Mapping

from marketplace_adapters.base import GovernedMarketplaceAdapter


AMAZON_FBM_ADAPTER_DRY_RUN_ONLY = False
FBA_AFN_READ_ONLY = True
UNKNOWN_FULFILLMENT_FAILS_CLOSED = True


class AmazonFbmAdapter(GovernedMarketplaceAdapter):
    """Governed adapter for one Amazon FBM/MFN SKU quantity update."""

    marketplace = "amazon"
    adapter_name = "amazon_fbm"

    def execute(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        fulfillment = _normalized_fulfillment(payload)
        sku = str(payload.get("sku") or "unknown")

        if fulfillment in {"AFN", "FBA"} or sku.upper().startswith("FBA-"):
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Amazon FBA/AFN is read-only; no FBA push path is permitted.",
            )

        if fulfillment not in {"MFN", "FBM"}:
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Amazon fulfillment is unknown; governed execution fails closed.",
            )

        if bool(payload.get("_governed_dry_run", True)):
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Amazon FBM/MFN adapter dry-run only; no live Listings API call was made.",
            )

        from amazon_service import AmazonAPIService

        service_result = AmazonAPIService().update_fbm_inventory_quantity_governed(
            store=payload.get("_governed_store"),
            listing=payload.get("_governed_listing"),
            sku=sku,
            quantity=payload.get("quantity"),
            marketplace_id=payload.get("marketplace_id"),
            fulfillment_channel=fulfillment,
            command_id=payload.get("_governed_command_id"),
            approval_id=payload.get("_governed_approval_id"),
        )
        success = bool(service_result.get("success") or service_result.get("ok"))
        return {
            "success": success,
            "ok": success,
            "governed": True,
            "dry_run": False,
            "execution_blocked": not success,
            "marketplace": self.marketplace,
            "adapter": self.adapter_name,
            "action": action,
            "reason": (
                "Amazon FBM inventory quantity push completed via governed path."
                if success
                else "Amazon FBM inventory quantity push failed or was blocked by service."
            ),
            "sku": sku,
            "quantity": payload.get("quantity"),
            "store_id": payload.get("store_id"),
            "listing_id": payload.get("listing_id"),
            "approval_id": payload.get("_governed_approval_id"),
            "amazon_result": service_result,
        }


def _normalized_fulfillment(payload: Mapping[str, Any]) -> str:
    value = (
        payload.get("amazon_fulfillment_channel")
        or payload.get("fulfillment_channel")
        or payload.get("fulfillment")
        or ""
    )
    return str(value).strip().upper()
