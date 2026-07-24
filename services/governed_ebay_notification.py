"""BT38 governed eBay notification interpreter.

Responsibilities:
- Interpret the eBay notification envelope.
- Extract the exact eBay order ID.
- Resolve the already-matched BT38 eBay store.
- Trigger the existing exact governed eBay order importer.

This module does not:
- create MarketplaceOrder rows directly;
- mutate warehouse stock directly;
- push marketplace quantities directly;
- create MCF orders directly.

All order execution remains inside the existing governed exact-order path.
"""

from __future__ import annotations

from typing import Any, Dict


def _deep_find(value: Any, key: str) -> Any:
    """Find the first matching key inside nested notification data."""

    if isinstance(value, dict):
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)

        for child in value.values():
            found = _deep_find(child, key)
            if found not in (None, ""):
                return found

    elif isinstance(value, list):
        for child in value:
            found = _deep_find(child, key)
            if found not in (None, ""):
                return found

    return None


def _extract_ebay_order_id(payload: dict) -> str:
    for key in (
        "orderId",
        "order_id",
        "marketplace_order_id",
        "ebayOrderId",
    ):
        value = _deep_find(payload, key)
        if value not in (None, ""):
            return str(value).strip()

    return ""


def handle_governed_ebay_notification(
    *,
    payload: dict,
    actor: str,
    store_id: int | None,
) -> Dict[str, Any]:
    """Route one eBay notification through the exact governed importer."""

    payload = dict(payload or {})
    order_id = _extract_ebay_order_id(payload)

    if not order_id:
        return {
            "status": "unresolved",
            "reason": (
                "eBay notification reached BT38 but no exact order ID "
                "could be extracted from the notification envelope."
            ),
            "stock_changed": False,
            "correction_started": False,
        }

    if not store_id:
        return {
            "status": "store_unresolved",
            "reason": (
                "eBay notification contained an order ID but no live "
                "BT38 eBay store could be resolved."
            ),
            "order_id": order_id,
            "stock_changed": False,
            "correction_started": False,
        }

    from services.governed_marketplace_order_import import (
        run_governed_ebay_exact_order_import,
    )

    exact_result = run_governed_ebay_exact_order_import(
        store_id=int(store_id),
        order_id=order_id,
        source=f"{actor}:exact_order",
    )

    success = bool(exact_result.get("success"))

    return {
        "status": (
            "exact_order_processed"
            if success
            else "exact_order_failed"
        ),
        "reason": (
            "eBay notification routed through the marketplace notification "
            "handler and the single governed exact-order importer."
        ),
        "order_id": order_id,
        "store_id": int(store_id),
        "exact_order_import": exact_result,
        "stock_changed": False,
        "correction_started": False,
    }
