"""BT38 governed marketplace notification router.

Responsibilities:
- Select the marketplace-specific notification interpreter.
- Return a normalised result to the generic webhook execution bridge.

Rules:
- No MarketplaceOrder creation.
- No warehouse mutation.
- No marketplace API calls directly.
- No push execution.
- No MCF execution.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def route_marketplace_notification(
    *,
    marketplace: str,
    payload: dict,
    actor: str,
    store_id: int | None,
) -> Optional[Dict[str, Any]]:
    """Route a notification to its marketplace-specific interpreter.

    Returning None means the marketplace continues through the existing
    generic webhook notification behaviour.
    """

    platform = str(marketplace or "").strip().lower()

    if platform == "ebay":
        from services.governed_ebay_notification import (
            handle_governed_ebay_notification,
        )

        return handle_governed_ebay_notification(
            payload=payload,
            actor=actor,
            store_id=store_id,
        )

    return None
