"""Exact idempotent recovery for one governed webhook.

Contract:
- Recover only the durable notification selected by the caller.
- Never launch a recent-order/platform scan.
- Check canonical MarketplaceOrder first for order events that require it.
- If the exact order already exists, never replay order/stock mutation.
- Amazon MCF lifecycle events terminate in the existing MCF lifecycle path and
  do not require a canonical MarketplaceOrder row.
- Amazon FBA may still perform one exact Seller-SKU settlement verification;
  this is read-only marketplace truth and is not an order replay.
- Existing eBay orders perform one exact order + shipping_fulfillment hydration
  so marketplace shipment truth can advance persisted dispatch/tracking state.
- Existing Amazon FBM orders perform one exact Orders/PACKAGES readback so
  marketplace shipment truth can advance persisted dispatch/tracking state.
- A shipment/lifecycle notification never replays a missing sale/order. If the
  canonical order is missing, recovery stops without stock mutation.
- Other failed order events may replay only the captured exact notification,
  then verify the canonical order exists.
- Any committed recovery/FBA/MCF change uses the existing DB -> UI publisher.
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


def _status_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _is_dispatch_lifecycle_payload(platform: str, payload: dict[str, Any]) -> bool:
    """Recognize only explicit marketplace shipment/lifecycle truth."""
    platform = str(platform or "").strip().lower()
    topic = _status_key(
        _deep_get(payload, "topic")
        or _deep_get(payload, "notificationType")
        or _deep_get(payload, "eventType")
        or _deep_get(payload, "event_type")
    )
    if platform == "ebay" and topic == "ITEMMARKEDSHIPPED":
        return True

    for key in (
        "OrderStatus",
        "orderStatus",
        "orderFulfillmentStatus",
        "fulfillmentStatus",
        "packageStatus",
    ):
        raw = _deep_get(payload, key)
        if isinstance(raw, dict):
            raw = raw.get("status")
        if _status_key(raw) in {
            "PARTIALLYSHIPPED",
            "SHIPPED",
            "FULFILLED",
            "PICKEDUPBYCARRIER",
            "CHECKEDINTOCARRIERHUB",
            "INTRANSIT",
            "OUTFORDELIVERY",
            "DELIVERED",
        }:
            return True
    return False


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
    fulfillment_type = (
        _deep_get(payload, "FulfillmentType")
        or _deep_get(payload, "fulfillmentType")
        or _deep_get(payload, "fulfillment_type")
    )
    return {
        "platform": str(platform or "").strip().lower(),
        "order_id": str(order_id or "").strip() or None,
        "seller_sku": str(seller_sku or "").strip() or None,
        "fulfillment_type": str(fulfillment_type or "").strip().upper() or None,
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


def _publish_committed_change(platform: str, notification_record_id: int, result: dict | None) -> bool:
    """Reuse the normal sleeping-browser handoff after a committed change."""
    if not isinstance(result, dict):
        return False

    from services.governed_ui_event_signal import (
        _result_has_committed_change,
        publish_webhook_ui_event,
    )

    if not _result_has_committed_change(result):
        return False

    publish_webhook_ui_event(
        platform=platform,
        notification_record_id=int(notification_record_id),
        scope=result,
    )
    return True


def _verify_existing_amazon_fba(
    *,
    identity: dict[str, Any],
    store_id: int | None,
    payload: dict[str, Any],
    notification_record_id: int,
) -> dict[str, Any] | None:
    """Refresh one exact FBA Seller SKU without replaying the order."""
    if (
        identity.get("platform") != "amazon"
        or identity.get("fulfillment_type") not in {"AFN", "FBA", "AMAZON"}
        or store_id is None
        or not identity.get("seller_sku")
    ):
        return None

    from services.governed_runtime_engine import _verify_exact_fba

    result = _verify_exact_fba({
        "event_type": "ORDER_CHANGE",
        "marketplace": "amazon",
        "store_id": int(store_id),
        "seller_sku": identity.get("seller_sku"),
        "order_id": identity.get("order_id"),
        "payload": payload,
    })
    result["ui_event_published"] = _publish_committed_change(
        "amazon",
        int(notification_record_id),
        result,
    )
    return result


def _hydrate_existing_amazon_order(
    *,
    identity: dict[str, Any],
    store_id: int | None,
    notification_record_id: int,
) -> dict[str, Any] | None:
    """Apply exact Amazon-owned FBM lifecycle truth to an existing order only."""
    if (
        identity.get("platform") != "amazon"
        or store_id is None
        or not identity.get("order_id")
    ):
        return None

    from models import Store
    from services.governed_amazon_tracking_readback import hydrate_amazon_tracking_for_order

    store = db.session.get(Store, int(store_id))
    if store is None:
        return None

    result = hydrate_amazon_tracking_for_order(
        store=store,
        marketplace_order_id=str(identity["order_id"]),
        source="amazon_webhook_exact_recovery:existing_order_hydration",
    )
    result["ui_event_published"] = _publish_committed_change(
        "amazon",
        int(notification_record_id),
        result,
    )
    return result


def _hydrate_existing_ebay_order(
    *,
    identity: dict[str, Any],
    store_id: int | None,
    notification_record_id: int,
) -> dict[str, Any] | None:
    """Apply exact eBay marketplace lifecycle truth to an existing order only."""
    if (
        identity.get("platform") != "ebay"
        or store_id is None
        or not identity.get("order_id")
    ):
        return None

    from models import Store
    from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

    store = db.session.get(Store, int(store_id))
    if store is None:
        return None

    result = hydrate_exact_ebay_order(
        store=store,
        marketplace_order_id=str(identity["order_id"]),
        source="ebay_webhook_exact_recovery:existing_order_hydration",
    )
    result["ui_event_published"] = _publish_committed_change(
        "ebay",
        int(notification_record_id),
        result,
    )
    return result


def _mcf_lifecycle_completed(replay_result: dict | None) -> bool:
    """Return True when one Amazon MCF lifecycle signal was fully handled.

    MCF fulfilment status is not a marketplace sale/order-intake event. Its
    canonical object is the existing MCF lifecycle row, so requiring a
    MarketplaceOrder after successful handling creates false recovery failures
    and unnecessary follow-on DB work.
    """
    if not isinstance(replay_result, dict):
        return False
    status = str(replay_result.get("status") or "").strip().lower()
    business_event = str(
        replay_result.get("business_event") or ""
    ).strip().lower()
    mcf_result = replay_result.get("mcf_result")
    mcf_handled = isinstance(mcf_result, dict) and bool(
        mcf_result.get("success") or mcf_result.get("skipped")
    )
    return bool(
        status == "mcf_fulfillment_status_processed"
        and business_event == "mcf_fulfillment_status"
        and mcf_handled
        and replay_result.get("inventory_push_started") is False
    )


def recover_exact_failed_webhook(platform: str, notification_record_id: int) -> dict[str, Any]:
    """Recover one captured webhook, never a marketplace window."""
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
    dispatch_lifecycle = _is_dispatch_lifecycle_payload(platform, payload)

    # Critical duplicate guard: once the canonical order exists, never replay
    # the order and never invoke its stock mutation again. Marketplace-owned
    # lifecycle truth may still refresh that same existing identity. Amazon FBA
    # performs one exact settlement verification; Amazon FBM reads the exact
    # order/PACKAGES truth; eBay reads the exact order + shipping fulfillments.
    if _canonical_order_exists(store_id, identity.get("order_id")):
        fba_verification = _verify_existing_amazon_fba(
            identity=identity,
            store_id=store_id,
            payload=payload,
            notification_record_id=int(notification_record_id),
        )
        amazon_hydration = _hydrate_existing_amazon_order(
            identity=identity,
            store_id=store_id,
            notification_record_id=int(notification_record_id),
        )
        ebay_hydration = _hydrate_existing_ebay_order(
            identity=identity,
            store_id=store_id,
            notification_record_id=int(notification_record_id),
        )
        return {
            "success": True,
            "recovered": bool(
                (amazon_hydration and amazon_hydration.get("success") and not amazon_hydration.get("skipped"))
                or (ebay_hydration and ebay_hydration.get("success"))
            ),
            "already_present": True,
            "duplicate_skipped": True,
            "order_replayed": False,
            "dispatch_lifecycle": dispatch_lifecycle,
            "fba_verification": fba_verification,
            "amazon_hydration": amazon_hydration,
            "ebay_hydration": ebay_hydration,
            "order_id": identity.get("order_id"),
            "store_id": store_id,
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "broad_scan_started": False,
        }

    # Shipment/lifecycle truth must never recreate/replay a missing sale. The
    # marketplace notification is authoritative for dispatch state, but stock
    # mutation remains owned by the original canonical sale/order intake.
    if dispatch_lifecycle:
        return {
            "success": False,
            "recovered": False,
            "reason": "canonical_order_missing_for_dispatch_lifecycle",
            "order_replayed": False,
            "stock_mutation_started": False,
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

    # MCF status signals terminate in the existing MCF lifecycle handler. They
    # intentionally do not create MarketplaceOrder rows and must return to
    # sleep immediately after that one exact handler completes.
    if _mcf_lifecycle_completed(replay_result):
        ui_event_published = _publish_committed_change(
            platform,
            int(notification_record_id),
            replay_result,
        )
        return {
            "success": True,
            "recovered": True,
            "handled_without_canonical_order": True,
            "canonical_order_required": False,
            "order_replayed": False,
            "inventory_push_started": False,
            "order_id": identity.get("order_id"),
            "store_id": store_id,
            "notification_record_id": int(notification_record_id),
            "platform": platform,
            "replay_result": replay_result,
            "ui_event_published": ui_event_published,
            "broad_scan_started": False,
        }

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

    ui_event_published = _publish_committed_change(
        platform,
        int(notification_record_id),
        replay_result,
    )

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
        "ui_event_published": ui_event_published,
        "broad_scan_started": False,
    }
