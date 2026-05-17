"""eBay governed adapter skeleton.

No eBay SDK, Trading API, Inventory API, OAuth, network, or HTTP clients are
imported here. The adapter returns a dry-run/blocked contract only.
"""

from __future__ import annotations

from typing import Any, Mapping

from marketplace_adapters.base import GovernedMarketplaceAdapter


EBAY_ADAPTER_DRY_RUN_ONLY = True


class EbayAdapter(GovernedMarketplaceAdapter):
    """Dry-run/blocked skeleton for future governed eBay execution."""

    marketplace = "ebay"
    adapter_name = "ebay"

    def execute(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.blocked_result(
            action=action,
            payload=payload,
            reason="eBay adapter skeleton is dry-run only; no live eBay API call was made.",
        )
