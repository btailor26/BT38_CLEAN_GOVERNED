"""BT38 governed webhook execution bridge.

Rules:
- Webhook payloads are immediate marketplace truth.
- Exact sale/order events use the canonical MarketplaceOrder + stock path.
- Exact cancellation events update the existing MarketplaceOrder first, then
  cancel the linked Amazon MCF order when one exists.
- Amazon FBA inventory remains Amazon-controlled and is updated only from
  Amazon inventory events.
- Missing listings use the existing marketplace listing recovery/import path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def process_marketplace_notification(
    *,
    marketplace: str,
    payload: dict,
    actor: str = "marketplace_webhook",
    notification_record_id: int | None = None,
) -> Dict[str, Any]:
    from extensions import db
    from models import MarketplaceListing
    from services.governed_push_execution import (
        push_group_listings,
        push_marketplace_listing,
    )

    payload = dict(payload or {})

    if notification_record_id is not None:
        payload["_bt38_notification_record_id"] = int(
            notification_record_id
        )

    marketplace = str(
        marketplace or payload.get("marketplace") or ""
    ).strip().lower()
    event_type = _event_type(payload)
    business_event = _classify_business_event(
        event_type,
        payload,
    )

    if business_event == "cancellation":
        return _handle_marketplace_cancellation(
            marketplace=marketplace,
            event_type=event_type,
            payload=payload,
        )

    listing = _find_listing(
        MarketplaceListing,
        marketplace,
        payload,
    )

    listing_recovery = None
    listing_discovery = None
    listing_was_missing = listing is None
    listing_notification = _is_listing_notification(
        marketplace=marketplace,
        event_type=event_type,
        payload=payload,
    )

    if listing_was_missing or listing_notification:
        seller_sku = (
            _deep_get(payload, "seller_sku")
            or _deep_get(payload, "sellerSku")
            or _deep_get(payload, "sku")
            or _deep_get(payload, "SKU")
        )

        from services.governed_marketplace_listing_recovery import (
            recover_governed_marketplace_listing,
        )

        listing_discovery = recover_governed_marketplace_listing(
            marketplace=marketplace,
            store_id=payload.get("_bt38_store_id"),
            event_type=event_type,
            seller_sku=(
                str(seller_sku)
                if seller_sku not in (None, "")
                else None
            ),
            payload=payload,
        )

        listing_recovery = (
            (listing_discovery or {}).get("recovery")
            or (listing_discovery or {}).get("result")
        )

        if (
            bool((listing_discovery or {}).get("applicable"))
            and bool((listing_discovery or {}).get("success"))
        ):
            listing = _find_listing(
                MarketplaceListing,
                marketplace,
                payload,
            )

    if not listing:
        return _log_result(
            status="unresolved",
            marketplace=marketplace,
            listing_recovery=listing_recovery,
            listing_discovery=listing_discovery,
            event_type=event_type,
            business_event=business_event,
            reason=(
                "Notification received but no marketplace listing "
                "could be matched."
            ),
            payload=payload,
        )

    listing_discovered = bool(listing_was_missing and listing is not None)

    platform_name = str(marketplace or "").strip().lower()
    listing_channel = str(
        getattr(
            listing,
            "normalized_amazon_fulfillment_channel",
            None,
        )
        or getattr(
            listing,
            "amazon_fulfillment_channel",
            None,
        )
        or ""
    ).strip().upper()

    is_amazon_fba = (
        "amazon" in platform_name
        and listing_channel not in {"MFN", "FBM", "MERCHANT"}
    )

    stock = listing.warehouse_stock
    if not stock:
        return _log_result(
            status="unlinked",
            marketplace=marketplace,
            event_type=event_type,
            business_event=business_event,
            reason=(
                "Notification matched listing but listing is not linked "
                "to warehouse stock."
            ),
            payload=payload,
            listing_id=listing.id,
        )

    explicit_inventory_quantity = any(
        _deep_get(payload, key) is not None
        for key in (
            "available_quantity",
            "fulfillableQuantity",
            "totalQuantity",
            "inventoryDetails",
            "inventory_details",
        )
    )

    if is_amazon_fba and explicit_inventory_quantity:
        from services.governed_amazon_inventory_import import (
            apply_governed_amazon_fba_event,
        )

        fba_result = apply_governed_amazon_fba_event(
            store_id=getattr(listing, "store_id", None),
            payload=payload,
            source="amazon_webhook_targeted_fba_handoff",
        )

        if fba_result.get("success"):
            return _log_result(
                status=(
                    "fba_inventory_updated"
                    if fba_result.get("stock_changed")
                    else "fba_inventory_unchanged"
                ),
                marketplace=marketplace,
                event_type=event_type,
                business_event=business_event,
                reason=(
                    "Targeted FBA inventory event used the existing "
                    "AmazonFBAInventory writer and refresh contract."
                ),
                payload=payload,
                **fba_result,
            )

    quantity = _extract_quantity(payload)
    is_stock_event = _is_stock_decrement_event(
        event_type,
        payload,
    )

    if not is_stock_event or quantity <= 0:
        group_id = getattr(listing, "master_product_group_id", None)
        return _log_result(
            status=f"{business_event}_stored",
            marketplace=marketplace,
            event_type=event_type,
            business_event=business_event,
            reason=_business_reason(business_event),
            payload=payload,
            listing_id=listing.id,
            warehouse_stock_id=stock.id,
            group_id=int(group_id) if group_id is not None else None,
            store_id=getattr(listing, "store_id", None),
            seller_sku=getattr(listing, "external_sku", None),
            listing_discovery=listing_discovery,
            changed=bool(
                listing_discovered
                or (listing_notification and (listing_discovery or {}).get("success"))
            ),
            created=listing_discovered,
            affected_listing_ids=[int(listing.id)],
            affected_warehouse_stock_ids=[int(stock.id)],
            affected_group_ids=(
                [int(group_id)] if group_id is not None else []
            ),
            stock_changed=False,
            correction_started=False,
        )

    group_context = _resolve_group_context(
        listing=listing,
        stock=stock,
    )
    grouped = bool(group_context.get("grouped"))
    group_id = group_context.get("group_id")

    before_qty = int(
        getattr(stock, "available_quantity", 0) or 0
    )

    order_intake = _import_marketplace_order_from_notification(
        marketplace=marketplace,
        event_type=event_type,
        payload=payload,
        listing=listing,
        stock=stock,
        quantity=quantity,
    )

    order = order_intake.get("order")

    if not order_intake.get("success") or order is None:
        return _log_result(
            status="order_import_failed",
            marketplace=marketplace,
            event_type=event_type,
            business_event=business_event,
            reason=(
                order_intake.get("reason")
                or "Canonical MarketplaceOrder import did not return a row."
            ),
            payload=payload,
            listing_id=listing.id,
            warehouse_stock_id=stock.id,
            stock_changed=False,
            correction_started=False,
            order_id=order_intake.get("marketplace_order_id"),
            order_intake=order_intake.get("result"),
        )

    from services.governed_order_stock_mutation import (
        process_exact_marketplace_order_line,
    )

    mutation_result = process_exact_marketplace_order_line(
        order,
        source=f"webhook_{marketplace}_order_intake",
    )

    if not mutation_result.get("success"):
        order.status = "failed"
        order.error_message = str(
            mutation_result.get("reason")
            or mutation_result
        )
        order.updated_at = datetime.utcnow()
        db.session.commit()

    # Amazon FBA/AFN sale notifications must still enter the canonical order
    # database, but order quantity is never FBA inventory authority. The exact
    # order processor marks the row processed without mutating Warehouse stock.
    # Return here before any Warehouse/group marketplace push; the runtime's
    # exact FBA verification refreshes AmazonFBAInventory from Amazon truth.
    if is_amazon_fba:
        return _log_result(
            status="fba_order_processed",
            marketplace=marketplace,
            event_type=event_type,
            business_event=business_event,
            reason=(
                "Amazon FBA/AFN sale was stored through canonical order intake. "
                "Order quantity did not mutate Warehouse or FBA inventory and "
                "no marketplace push was started; Amazon remains FBA inventory authority."
            ),
            payload=payload,
            store_id=getattr(listing, "store_id", None),
            listing_id=listing.id,
            warehouse_stock_id=stock.id,
            seller_sku=(
                getattr(listing, "external_sku", None)
                or getattr(stock, "sku", None)
            ),
            order_id=order_intake.get("marketplace_order_id"),
            order_intake=order_intake.get("result"),
            stock_mutation=mutation_result,
            stock_changed=False,
            correction_started=False,
            push_started=False,
            fba_inventory_verification_required=True,
        )

    if grouped:
        if not group_id:
            return _log_result(
                status="group_unresolved",
                marketplace=marketplace,
                event_type=event_type,
                business_event=business_event,
                reason=(
                    "DB says listing/stock is grouped but no group_id "
                    "could be resolved."
                ),
                payload=payload,
                listing_id=listing.id,
                warehouse_stock_id=stock.id,
                order_id=order_intake.get("marketplace_order_id"),
                order_intake=order_intake.get("result"),
                stock_mutation=mutation_result,
                group_context=group_context,
            )

        push_result = push_group_listings(
            group_id=int(group_id),
            actor=actor,
            source=f"webhook_{marketplace}_group_notification",
            actor_user=None,
            authority_warehouse_stock_id=stock.id,
        )

        return _log_result(
            status="group_processed",
            marketplace=marketplace,
            event_type=event_type,
            business_event=business_event,
            reason=(
                "Grouped sale notification created MarketplaceOrder, "
                "updated Warehouse truth through governed order mutation, and "
                "handed the exact current group to the shared Warehouse-controlled correction path."
            ),
            payload=payload,
            store_id=getattr(listing, "store_id", None),
            listing_id=listing.id,
            warehouse_stock_id=stock.id,
            group_id=int(group_id),
            seller_sku=(
                getattr(listing, "external_sku", None)
                or getattr(stock, "sku", None)
            ),
            group_context=group_context,
            before_qty=before_qty,
            after_qty=int(
                getattr(stock, "available_quantity", 0) or 0
            ),
            expected_quantity=int(
                getattr(stock, "sellable_quantity", 0) or 0
            ),
            stock_changed=bool(
                mutation_result.get("success")
                and not mutation_result.get("skipped")
            ),
            correction_started=True,
            order_id=order_intake.get("marketplace_order_id"),
            order_intake=order_intake.get("result"),
            stock_mutation=mutation_result,
            push_result=push_result,
        )

    push_result = push_marketplace_listing(
        listing_id=listing.id,
        actor=actor,
        source=f"webhook_{marketplace}_warehouse_notification",
        actor_user=None,
    )

    return _log_result(
        status="warehouse_processed",
        marketplace=marketplace,
        event_type=event_type,
        business_event=business_event,
        reason=(
            "Sale notification created MarketplaceOrder, updated Warehouse truth "
            "through governed order mutation, and handed the exact listing to "
            "the shared Warehouse-controlled correction path."
        ),
        payload=payload,
        store_id=getattr(listing, "store_id", None),
        listing_id=listing.id,
        warehouse_stock_id=stock.id,
        seller_sku=(
            getattr(listing, "external_sku", None)
            or getattr(stock, "sku", None)
        ),
        before_qty=before_qty,
        after_qty=int(
            getattr(stock, "available_quantity", 0) or 0
        ),
        expected_quantity=int(
            getattr(stock, "sellable_quantity", 0) or 0
        ),
        stock_changed=bool(
            mutation_result.get("success")
            and not mutation_result.get("skipped")
        ),
        correction_started=True,
        order_id=order_intake.get("marketplace_order_id"),
        order_intake=order_intake.get("result"),
        stock_mutation=mutation_result,
        push_result=push_result,
    )


def _extract_marketplace_order_id(payload: dict) -> str | None:
    value = (
        _deep_get(payload, "marketplace_order_id")
        or _deep_get(payload, "order_id")
        or _deep_get(payload, "orderId")
        or _deep_get(payload, "order_number")
        or _deep_get(payload, "orderNumber")
        or _deep_get(payload, "amazonOrderId")
        or _deep_get(payload, "ebayOrderId")
    )
    text = str(value or "").strip()
    return text or None


def _handle_marketplace_cancellation(
    *,
    marketplace: str,
    event_type: str,
    payload: dict,
) -> Dict[str, Any]:
    from extensions import db
    from models import MarketplaceOrder, Store

    order_id = _extract_marketplace_order_id(payload)
    if not order_id:
        return _log_result(
            status="cancellation_unresolved",
            marketplace=marketplace,
            event_type=event_type,
            business_event="cancellation",
            reason="Cancellation payload did not include a marketplace order ID.",
            payload=payload,
        )

    query = MarketplaceOrder.query.filter(
        MarketplaceOrder.marketplace_order_id == order_id
    )

    store_id = _deep_get(payload, "_bt38_store_id")
    try:
        store_id = int(store_id) if store_id is not None else None
    except (TypeError, ValueError):
        store_id = None

    if store_id is not None:
        query = query.filter(MarketplaceOrder.store_id == store_id)
    elif marketplace:
        query = query.join(
            Store,
            Store.id == MarketplaceOrder.store_id,
        ).filter(Store.platform.ilike(f"%{marketplace}%"))

    lines = query.order_by(MarketplaceOrder.id).all()
    if not lines:
        return _log_result(
            status="cancellation_unresolved",
            marketplace=marketplace,
            event_type=event_type,
            business_event="cancellation",
            reason="No existing MarketplaceOrder matched the cancellation.",
            payload=payload,
            order_id=order_id,
        )

    now = datetime.utcnow()
    for line in lines:
        line.status = "cancel_requested"
        line.updated_at = now
    db.session.commit()

    mcf = next(
        (line.mcf_order for line in lines if line.mcf_order_id),
        None,
    )

    if mcf is None:
        for line in lines:
            line.status = "cancelled"
            line.updated_at = datetime.utcnow()
        db.session.commit()
        return _log_result(
            status="cancellation_processed",
            marketplace=marketplace,
            event_type=event_type,
            business_event="cancellation",
            reason=(
                "Marketplace cancellation stored on the exact order. "
                "No Amazon MCF order was linked."
            ),
            payload=payload,
            order_id=order_id,
            marketplace_order_row_ids=[line.id for line in lines],
            mcf_order_id=None,
            amazon_cancelled=False,
        )

    from services.governed_mcf_execution import cancel_mcf_order

    cancelled, cancel_result = cancel_mcf_order(mcf)
    if not cancelled:
        for line in lines:
            line.status = "cancel_requested"
            line.error_message = cancel_result.get("error")
            line.updated_at = datetime.utcnow()
        db.session.commit()
        return _log_result(
            status="cancellation_cancel_failed",
            marketplace=marketplace,
            event_type=event_type,
            business_event="cancellation",
            reason="Marketplace cancellation stored but Amazon MCF cancellation failed.",
            payload=payload,
            order_id=order_id,
            marketplace_order_row_ids=[line.id for line in lines],
            mcf_order_id=mcf.id,
            amazon_cancelled=False,
            cancel_result=cancel_result,
        )

    return _log_result(
        status="cancellation_processed",
        marketplace=marketplace,
        event_type=event_type,
        business_event="cancellation",
        reason=(
            "Marketplace cancellation stored and the linked Amazon MCF "
            "order was cancelled through the existing MCF client."
        ),
        payload=payload,
        order_id=order_id,
        marketplace_order_row_ids=[line.id for line in lines],
        mcf_order_id=mcf.id,
        amazon_cancelled=True,
        cancel_result=cancel_result,
    )


def _parse_marketplace_order_timestamp(payload: dict) -> datetime | None:
    for key in (
        "creationDate",
        "orderCreationDate",
        "PurchaseDate",
        "purchaseDate",
        "orderCreatedAt",
        "order_created_at",
    ):
        value = _deep_get(payload, key)
        if value in (None, ""):
            continue
        try:
            return datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            continue
    return None


def _import_marketplace_order_from_notification(
    *,
    marketplace: str,
    event_type: str,
    payload: dict,
    listing,
    stock,
    quantity: int,
) -> Dict[str, Any]:
    import hashlib
    import json

    from extensions import db
    from models import Store
    from services.governed_marketplace_order_import import (
        upsert_governed_marketplace_order_line,
    )

    order_id = _extract_marketplace_order_id(payload)

    item_id = (
        _deep_get(payload, "marketplace_order_item_id")
        or _deep_get(payload, "order_item_id")
        or _deep_get(payload, "orderItemId")
        or _deep_get(payload, "line_item_id")
        or _deep_get(payload, "lineItemId")
        or _deep_get(payload, "transaction_id")
        or _deep_get(payload, "transactionId")
        or getattr(listing, "external_listing_id", None)
    )

    sku = (
        _deep_get(payload, "sku")
        or _deep_get(payload, "seller_sku")
        or _deep_get(payload, "sellerSku")
        or _deep_get(payload, "external_sku")
        or getattr(listing, "external_sku", None)
        or getattr(stock, "sku", None)
    )

    if not order_id:
        raw = json.dumps(
            payload or {},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha1(
            raw.encode("utf-8")
        ).hexdigest()[:16]
        order_id = f"webhook-{marketplace}-{digest}"

    order_id = str(order_id)
    item_id = str(item_id or order_id)
    sku = str(sku or "").strip()

    store = getattr(listing, "store", None)
    if store is None:
        listing_store_id = getattr(listing, "store_id", None)
        store = (
            db.session.get(Store, listing_store_id)
            if listing_store_id
            else None
        )

    if store is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "webhook_listing_store_missing",
            "marketplace_order_id": order_id,
            "sku": sku,
        }

    channel = str(
        getattr(
            listing,
            "normalized_amazon_fulfillment_channel",
            None,
        )
        or ""
    ).upper()

    platform = str(
        getattr(store, "platform", None)
        or marketplace
        or ""
    ).lower()

    fulfillment_type = (
        "FBA"
        if "amazon" in platform
        and channel not in {"MFN", "FBM", "MERCHANT"}
        else "FBM"
    )

    try:
        unit_price = float(
            _deep_get(payload, "unit_price")
            or _deep_get(payload, "price")
            or _deep_get(payload, "item_price")
            or _deep_get(payload, "itemPrice")
            or 0
        )
    except (TypeError, ValueError):
        unit_price = 0.0

    result = upsert_governed_marketplace_order_line(
        store=store,
        marketplace_order_id=order_id,
        marketplace_order_item_id=item_id,
        sku=sku,
        quantity=int(quantity or 1),
        unit_price=unit_price,
        fulfillment_type=fulfillment_type,
        status="pending",
        marketplace_created_at=(
            _parse_marketplace_order_timestamp(payload)
        ),
        import_source=f"webhook_{marketplace}",
        listing=listing,
    )

    order = result.get("_order_row")
    public_result = {
        key: value
        for key, value in result.items()
        if key != "_order_row"
    }

    return {
        "success": bool(result.get("success")),
        "created": bool(result.get("created")),
        "skipped": bool(result.get("skipped")),
        "reason": result.get("reason"),
        "order": order,
        "result": public_result,
        "marketplace_order_id": (
            result.get("order_id") or order_id
        ),
    }


def _resolve_group_context(*, listing, stock) -> Dict[str, Any]:
    from extensions import db
    from models import MarketplaceListing

    listing_group_id = getattr(
        listing,
        "master_product_group_id",
        None,
    )
    stock_group_id = getattr(
        stock,
        "master_product_group_id",
        None,
    )
    stock_group_controlled = bool(
        getattr(stock, "is_group_controlled", False)
    )

    # Current Product Linking relationship is authoritative for correction.
    # WarehouseStock.master_product_group_id remains permanent/original identity.
    group_id = listing_group_id or stock_group_id
    linked_group_members = []
    linked_stock_members = []

    if group_id:
        linked_group_members = (
            db.session.query(MarketplaceListing.id)
            .filter(
                MarketplaceListing.master_product_group_id
                == int(group_id)
            )
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .all()
        )

    if getattr(stock, "id", None):
        linked_stock_members = (
            db.session.query(MarketplaceListing.id)
            .filter(
                MarketplaceListing.warehouse_stock_id
                == int(stock.id)
            )
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .all()
        )

    grouped = bool(
        listing_group_id
        or len(linked_group_members) > 1
        or len(linked_stock_members) > 1
    )

    return {
        "grouped": grouped,
        "group_id": int(group_id) if group_id else None,
        "listing_id": getattr(listing, "id", None),
        "listing_group_id": (
            int(listing_group_id)
            if listing_group_id
            else None
        ),
        "warehouse_stock_id": getattr(stock, "id", None),
        "stock_group_id": (
            int(stock_group_id)
            if stock_group_id
            else None
        ),
        "stock_is_group_controlled": stock_group_controlled,
        "linked_group_member_count": len(linked_group_members),
        "linked_stock_member_count": len(linked_stock_members),
        "authority": "current_listing_relationship_then_warehouse_identity",
    }


def _apply_group_stock_change(
    warehouse_stock_id: int,
    quantity_delta: int,
) -> None:
    try:
        from group_resolution import apply_group_quantity_change

        apply_group_quantity_change(
            warehouse_stock_id=int(warehouse_stock_id),
            quantity_delta=int(quantity_delta),
            reason="marketplace_webhook_notification",
        )
        return
    except Exception:
        pass

    from extensions import db
    from models import WarehouseStock

    stock = db.session.get(
        WarehouseStock,
        int(warehouse_stock_id),
    )
    if stock:
        before = int(
            getattr(stock, "available_quantity", 0) or 0
        )
        _ = before


def _find_listing(
    MarketplaceListing,
    marketplace: str,
    payload: dict,
):
    identifiers = _flatten_values(payload)

    listing_id_keys = {
        "listing_id",
        "marketplace_listing_id",
    }
    external_keys = {
        "external_listing_id",
        "item_id",
        "itemid",
        "listingid",
        "orderlineitemid",
    }
    sku_keys = {
        "sku",
        "seller_sku",
        "sellersku",
        "external_sku",
    }

    def _active_query():
        return MarketplaceListing.query.filter(
            MarketplaceListing.is_active == True  # noqa: E712
        )

    def _best(query):
        return query.order_by(
            MarketplaceListing.warehouse_stock_id.is_(None),
            MarketplaceListing.updated_at.desc(),
            MarketplaceListing.id.desc(),
        ).first()

    for key in listing_id_keys:
        value = _deep_get(payload, key)
        if value:
            try:
                listing = _active_query().filter(
                    MarketplaceListing.id == int(value)
                ).first()
                if listing:
                    return listing
            except Exception:
                pass

    for key in external_keys:
        value = _deep_get(payload, key)
        if value:
            listing = _best(
                _active_query().filter(
                    MarketplaceListing.external_listing_id == str(value)
                )
            )
            if listing:
                return listing

    for key in sku_keys:
        value = _deep_get(payload, key)
        if value:
            query = _active_query().filter(
                MarketplaceListing.external_sku == str(value)
            )
            store_id = payload.get("_bt38_store_id")
            if store_id not in (None, ""):
                try:
                    query = query.filter(
                        MarketplaceListing.store_id == int(store_id)
                    )
                except (TypeError, ValueError):
                    pass
            elif marketplace:
                query = query.join(
                    MarketplaceListing.store
                ).filter_by(platform=marketplace)
            listing = _best(query)
            if listing:
                return listing

    for value in identifiers:
        text = str(value).strip()
        if not text:
            continue
        query = _active_query().filter(
            (MarketplaceListing.external_listing_id == text)
            | (MarketplaceListing.external_sku == text)
        )
        store_id = payload.get("_bt38_store_id")
        if store_id not in (None, ""):
            try:
                query = query.filter(
                    MarketplaceListing.store_id == int(store_id)
                )
            except (TypeError, ValueError):
                pass
        listing = _best(query)
        if listing:
            return listing

    return None


def _event_type(payload: dict) -> str:
    return str(
        payload.get("event_type")
        or payload.get("eventType")
        or payload.get("notificationType")
        or payload.get("NotificationType")
        or payload.get("type")
        or payload.get("topic")
        or "marketplace_notification"
    ).strip().lower()


def _is_listing_notification(*, marketplace: str, event_type: str, payload: dict) -> bool:
    normalized = str(event_type or "").strip().upper()
    if str(marketplace or "").strip().lower() == "amazon":
        return normalized in {
            "LISTINGS_ITEM_STATUS_CHANGE",
            "LISTINGS_ITEM_MFN_QUANTITY_CHANGE",
        }

    topic = str(
        _deep_get(payload, "topic")
        or _deep_get(payload, "notificationType")
        or ""
    ).strip().upper()
    return str(marketplace or "").strip().lower() == "ebay" and topic == "LISTING"


def _classify_business_event(
    event_type: str,
    payload: dict,
) -> str:
    text = " ".join(
        str(v).lower()
        for v in _flatten_values(payload)
    )
    combined = f"{event_type} {text}"

    checks = [
        ("cancellation", ["order cancelled", "order canceled", "order cancellation", "cancel request", "cancel_requested", "cancelled", "canceled"]),
        ("fba_pending", ["fba pending", "afn pending", "pending fba", "pending inventory", "inbound pending"]),
        ("fba_received", ["fba received", "afn received", "received by amazon", "inbound received"]),
        ("fba_adjustment", ["fba adjustment", "inventory adjustment", "afn adjustment"]),
        ("fba_lost", ["fba lost", "lost inventory", "inventory lost"]),
        ("fba_damaged", ["fba damaged", "damaged inventory", "warehouse damaged"]),
        ("fba_reimbursement", ["fba reimbursement", "reimbursement", "reimbursed"]),
        ("customer_message", ["message", "buyer message", "customer message", "inbox", "unread"]),
        ("return", ["return", "return request", "refund requested"]),
        ("case", ["case", "dispute", "claim", "a-to-z", "chargeback"]),
        ("listing_created", ["listing created", "item listed", "offer created", "new listing"]),
        ("listing_removed", ["listing removed", "listing ended", "item ended", "offer deleted", "listing blocked", "suppressed"]),
        ("payment_deferred", ["deferred", "reserve", "hold", "held", "pending payout"]),
        ("payout", ["payout", "disbursement", "settlement", "payment released", "paid out"]),
        ("tracking", ["tracking", "tracking uploaded", "shipment confirmed", "carrier"]),
        ("delivery", ["delivered", "delivery confirmed"]),
        ("policy", ["policy", "violation", "warning", "account health", "performance notification"]),
        ("stock_decrement", ["order", "sale", "sold", "transaction", "paid", "purchase"]),
    ]

    for label, words in checks:
        if any(word in combined for word in words):
            return label

    return "marketplace_notification"


def _business_reason(business_event: str) -> str:
    reasons = {
        "cancellation": "Cancellation notification stored against the exact existing MarketplaceOrder and propagated to Amazon MCF when linked.",
        "fba_pending": "FBA pending notification stored. No warehouse stock change is made until Amazon supplies confirmed inventory state.",
        "fba_received": "FBA received notification stored for exact verification.",
        "fba_adjustment": "FBA adjustment notification stored for exact verification.",
        "fba_lost": "FBA lost notification stored for exact verification.",
        "fba_damaged": "FBA damaged notification stored for exact verification.",
        "fba_reimbursement": "FBA reimbursement notification stored for exact verification.",
        "customer_message": "Customer message notification stored for awareness.",
        "return": "Return notification stored for awareness.",
        "case": "Case/dispute notification stored for awareness.",
        "listing_created": "Listing-created notification stored for awareness.",
        "listing_removed": "Listing removed/blocked/ended notification stored for awareness.",
        "payout": "Payout notification stored for awareness.",
        "payment_deferred": "Deferred/held payment notification stored for awareness.",
        "tracking": "Tracking notification stored for awareness.",
        "delivery": "Delivery notification stored for awareness.",
        "policy": "Policy/account-health notification stored for awareness.",
    }
    return reasons.get(
        business_event,
        "Notification stored but did not contain a confirmed stock-decrement event.",
    )


def _is_stock_decrement_event(event_type: str, payload: dict) -> bool:
    return _classify_business_event(event_type, payload) == "stock_decrement"


def _extract_quantity(payload: dict) -> int:
    for key in ["quantity", "qty", "quantity_sold", "quantitySold", "orderQuantity", "amount"]:
        value = _deep_get(payload, key)
        if value is not None:
            try:
                qty = int(value)
                if qty > 0:
                    return qty
            except Exception:
                pass
    return 1


def _deep_get(obj: Any, key: str):
    key_lower = str(key).lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == key_lower:
                return v
            found = _deep_get(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_get(item, key)
            if found is not None:
                return found
    return None


def _flatten_values(obj: Any):
    values = []
    if isinstance(obj, dict):
        for value in obj.values():
            values.extend(_flatten_values(value))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_flatten_values(item))
    else:
        values.append(obj)
    return values


def _log_result(**data) -> Dict[str, Any]:
    from extensions import db
    from models import SystemLog

    safe = dict(data)
    payload = dict(safe.pop("payload", {}) or {})

    notification_record_id = (
        safe.get("notification_record_id")
        or payload.pop("_bt38_notification_record_id", None)
    )
    if notification_record_id is not None:
        safe["notification_record_id"] = int(notification_record_id)

    try:
        db.session.add(
            SystemLog(
                log_type="governed_webhook_execution",
                message=(
                    f"{safe.get('marketplace')} webhook execution "
                    f"{safe.get('status')}: {safe.get('event_type')}"
                ),
                details=str({**safe, "payload_keys": list(payload.keys())})[:1000],
                created_at=datetime.utcnow(),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()

    success_statuses = {
        "group_processed", "warehouse_processed", "stock_decrement_stored",
        "marketplace_notification_stored", "customer_message_stored", "return_stored",
        "case_stored", "listing_created_stored", "listing_removed_stored", "payout_stored",
        "payment_deferred_stored", "tracking_stored", "delivery_stored", "policy_stored",
        "fba_pending_stored", "fba_received_stored", "fba_adjustment_stored", "fba_lost_stored",
        "fba_damaged_stored", "fba_reimbursement_stored", "fba_inventory_updated",
        "fba_inventory_unchanged", "fba_order_processed", "cancellation_processed",
    }

    return {
        "ok": safe.get("status") in success_statuses,
        "success": safe.get("status") in success_statuses,
        "governed": True,
        **safe,
    }
