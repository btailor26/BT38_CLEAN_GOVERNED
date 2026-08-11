"""Exact idempotent recovery for one failed governed webhook.

Contract:
- Recover only the durable notification that failed.
- Never launch a recent-order/platform scan.
- Check canonical MarketplaceOrder first.
- If the exact order already exists, stop without reprocessing stock.
- If it is missing, replay only the captured payload through the existing
  governed webhook executor, then verify that the canonical order now exists.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from extensions import db


def _notification_table(platform: str) -> str:
    platform = str(platform or "").strip().lower()
    if platform == "amazon":
        return "webhooks.amazon_notifications"
    if platform == "ebay":
        return "webhooks.ebay_notifications"
    raise ValueError(f"unsupported_webhook_platform:{platform}")


def _load_notification(platform: str, notification_record_id: int) -> dict[str, Any] | None:
    table = _notification_table(platform)
    row = db.session.execute(
        text(
            f"""
            SELECT id, payload_json
            FROM {table}
            WHERE id = :notification_record_id
            LIMIT 1
            """
        ),
        {"notification_record_id": int(notification_record_id)},
    ).mappings().first()
    return dict(row) if row else None


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value and value[key] not in (None, ""):
            return value[key]
        for nested in value.values():
            found = _deep_get(nested, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _deep_get(nested, key)
            if found not in (None, ""):
                return found
    return None


def _exact_identity(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    order_id = (
        _deep_get(payload, "AmazonOrderId")
        or _deep_get(payload, "amazonOrderId")
        or _deep_get(payload, "orderId")
        or _deep_get(payload, "marketplace_order_id")
        or _deep_get(payload, "order_id")
    )
    seller_sku = (
        _deep_get(payload, "SellerSKU")
        or _deep_get(payload, "sellerSku")
        or _deep_get(payload, "seller_sku")
        or _deep_get(payload, "sku")
    )
    return {
        "platform": str(platform or "").strip().lower(),
        "order_id": str(order_id or "").strip() or None,
        "seller_sku": str(seller_sku or "").strip() or None,
    }


def _resolve_store_id(platform: str, seller_sku: str | None) -> int | None:
    from models import MarketplaceListing, Store

    if seller_sku:
        row = (
            db.session.query(MarketplaceListing.store_id)
            .join(Store, Store.id == MarketplaceListing.store_id)
            .filter(
                MarketplaceListing.external_sku == seller_sku,
                MarketplaceListing.is_active == True,  # noqa: E712
                Store.is_active == True,  # noqa: E712
                Store.store_mode == "live",
                Store.platform.ilike(f"%{platform}%"),
            )
            .order_by(MarketplaceListing.id.desc())
            .first()
        )
        if row:
            return int(row[0])

    stores = (
        db.session.query(Store.id)
        .filter(
            Store.is_active == True,  # noqa: E712
            Store.store_mode == "live",
            Store.platform.ilike(f"%{platform}%"),
        )
        .order_by(Store.id)
        .all()
    )
    if len(stores) == 1:
        return int(stores[0][0])
    return None


def _canonical_order_exists(store_id: int | None, order_id: str | None) -> bool:
    from models import MarketplaceOrder

    if not order_id:
        return False
    query = MarketplaceOrder.query.filter(
        MarketplaceOrder.marketplace_order_id == order_id
    )
    if store_id is not None:
        query = query.filter(MarketplaceOrder.store_id == int(store_id))
    return query.first() is not None


def recover_exact_failed_webhook(platform: str, notification_record_id: int) -> dict[str, Any]:
    """Recover one failed captured webhook, never a marketplace window."""
    platform = str(platform or "").strip().lower()
    notification = _load_notification(platform, int(notification_record_id))
    if not notification:
        return {
            "success": False,
            "reason": "notification_not_found",
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "broad_scan_started": False,
        }

    payload = notification.get("payload_json") or {}
    if not isinstance(payload, dict):
        return {
            "success": False,
            "reason": "captured_payload_not_object",
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "broad_scan_started": False,
        }

    identity = _exact_identity(platform, payload)
    store_id = _resolve_store_id(platform, identity.get("seller_sku"))

    # Critical duplicate guard: once the canonical order exists, recovery ends.
    # Do not replay the event and do not invoke stock mutation again.
    if _canonical_order_exists(store_id, identity.get("order_id")):
        return {
            "success": True,
            "recovered": False,
            "already_present": True,
            "duplicate_skipped": True,
            "order_id": identity.get("order_id"),
            "store_id": store_id,
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "broad_scan_started": False,
        }

    replay_payload = dict(payload)
    if store_id is not None:
        replay_payload["_bt38_store_id"] = int(store_id)

    from services.governed_webhook_execution import process_marketplace_notification

    replay_result = process_marketplace_notification(
        marketplace=platform,
        payload=replay_payload,
        actor=f"{platform}_webhook_exact_recovery",
        notification_record_id=int(notification_record_id),
    )

    db.session.expire_all()
    recovered = _canonical_order_exists(store_id, identity.get("order_id"))
    if not recovered:
        return {
            "success": False,
            "recovered": False,
            "reason": "canonical_order_still_missing_after_exact_replay",
            "order_id": identity.get("order_id"),
            "store_id": store_id,
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "replay_result": replay_result,
            "broad_scan_started": False,
        }

    return {
        "success": True,
        "recovered": True,
        "already_present": False,
        "duplicate_skipped": False,
        "order_id": identity.get("order_id"),
        "store_id": store_id,
        "notification_record_id": int(notification_record_id),
        "platform": platform,
        "replay_result": replay_result,
        "broad_scan_started": False,
    }
