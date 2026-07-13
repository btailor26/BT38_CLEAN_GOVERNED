"""Governed manual MCF workflow.

Page reads are database-only. Marketplace writes happen only through explicit
POST actions and the existing runtime push guard.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import tuple_
from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    AmazonFBAInventory,
    MCFOrder,
    MCFOrderItem,
    MarketplaceListing,
    MarketplaceOrder,
    Store,
    WarehouseStock,
)
from mcf_service import MCFService
from services.governed_ebay_dispatch import complete_sale
from services.runtime_action_guard import is_runtime_action_allowed


governed_mcf_bp = Blueprint("governed_mcf", __name__)


def _order_lines(anchor: MarketplaceOrder) -> list[MarketplaceOrder]:
    return (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == anchor.store_id,
            MarketplaceOrder.marketplace_order_id == anchor.marketplace_order_id,
        )
        .order_by(MarketplaceOrder.id)
        .all()
    )


def _amazon_store_ids() -> list[int]:
    return [
        int(store.id)
        for store in (
            Store.query
            .filter(Store.is_active == True)  # noqa: E712
            .all()
        )
        if "amazon" in str(store.platform or "").lower()
    ]


def _group_stock_ids(order: MarketplaceOrder) -> list[int]:
    """
    MCF only applies to an explicitly linked Product Linking group.

    Ungrouped and non-FBA warehouse stock must never enter the MCF path.
    """
    stock = order.warehouse_stock
    if stock is None or not stock.master_product_group_id:
        return []

    return [
        int(row.id)
        for row in (
            WarehouseStock.query
            .filter(
                WarehouseStock.master_product_group_id
                == int(stock.master_product_group_id)
            )
            .all()
        )
    ]


def _fba_candidate(order: MarketplaceOrder) -> AmazonFBAInventory | None:
    """
    Resolve the canonical Amazon FBA inventory row through the existing links:

    MarketplaceOrder
      -> WarehouseStock
      -> Product Linking group
      -> Amazon MarketplaceListing
      -> AmazonFBAInventory
    """
    stock_ids = _group_stock_ids(order)
    if not stock_ids:
        return None

    amazon_store_ids = _amazon_store_ids()
    if not amazon_store_ids:
        return None

    amazon_listings = (
        MarketplaceListing.query
        .filter(
            MarketplaceListing.store_id.in_(amazon_store_ids),
            MarketplaceListing.warehouse_stock_id.in_(stock_ids),
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .all()
    )

    seller_skus = {
        str(listing.external_sku or "").strip()
        for listing in amazon_listings
        if str(listing.external_sku or "").strip()
    }

    fnskus = {
        str(listing.fnsku or "").strip()
        for listing in amazon_listings
        if str(listing.fnsku or "").strip()
    }

    if not seller_skus and not fnskus:
        return None

    query = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.store_id.in_(amazon_store_ids),
        AmazonFBAInventory.is_active == True,  # noqa: E712
        AmazonFBAInventory.is_archived == False,  # noqa: E712
        AmazonFBAInventory.mcf_enabled == True,  # noqa: E712
    )

    if seller_skus and fnskus:
        query = query.filter(
            db.or_(
                AmazonFBAInventory.seller_sku.in_(seller_skus),
                AmazonFBAInventory.fnsku.in_(fnskus),
            )
        )
    elif seller_skus:
        query = query.filter(
            AmazonFBAInventory.seller_sku.in_(seller_skus)
        )
    else:
        query = query.filter(
            AmazonFBAInventory.fnsku.in_(fnskus)
        )

    return (
        query
        .order_by(
            AmazonFBAInventory.available_quantity.desc(),
            AmazonFBAInventory.updated_at.desc(),
            AmazonFBAInventory.id.desc(),
        )
        .first()
    )


def _line_view(line: MarketplaceOrder) -> dict:
    fba = _fba_candidate(line)
    group_id = getattr(line.warehouse_stock, "master_product_group_id", None) if line.warehouse_stock else None
    return {
        "line": line,
        "group_id": group_id,
        "fba": fba,
        "eligible": bool(
            fba
            and int(fba.available_quantity or 0)
            >= int(line.quantity or 0)
        ),
    }


def _bulk_orders(limit: int = 100) -> list[dict]:
    """
    Load only linked FBA-group orders.

    Non-FBA groups and ungrouped orders are excluded before rendering.
    """
    seed_rows = (
        MarketplaceOrder.query
        .order_by(
            MarketplaceOrder.created_at.desc(),
            MarketplaceOrder.id.desc(),
        )
        .limit(max(1, min(int(limit or 100), 250)))
        .all()
    )

    keys = []
    seen = set()

    for row in seed_rows:
        key = (row.store_id, row.marketplace_order_id)
        if key not in seen:
            seen.add(key)
            keys.append(key)

    if not keys:
        return []

    lines = (
        MarketplaceOrder.query
        .options(
            selectinload(MarketplaceOrder.store),
            selectinload(MarketplaceOrder.mcf_order),
            selectinload(MarketplaceOrder.warehouse_stock),
        )
        .filter(
            tuple_(
                MarketplaceOrder.store_id,
                MarketplaceOrder.marketplace_order_id,
            ).in_(keys)
        )
        .order_by(
            MarketplaceOrder.created_at.desc(),
            MarketplaceOrder.id,
        )
        .all()
    )

    lines_by_key = defaultdict(list)

    for line in lines:
        lines_by_key[
            (line.store_id, line.marketplace_order_id)
        ].append(line)

    orders = []

    for key in keys:
        order_lines = lines_by_key.get(key, [])
        if not order_lines:
            continue

        views = [_line_view(line) for line in order_lines]

        mcf = next(
            (
                line.mcf_order
                for line in order_lines
                if line.mcf_order_id
            ),
            None,
        )

        eligible = bool(views) and all(
            view["eligible"] for view in views
        )

        # Existing MCF orders remain visible for status/tracking.
        # New orders only appear when every line resolves to linked FBA truth.
        if not eligible and not mcf:
            continue

        state = (
            "ready"
            if eligible and not mcf
            else "submitted"
            if mcf and mcf.status in {
                "submitted",
                "processing",
                "completed",
            }
            else "failed"
            if mcf and mcf.status == "failed"
            else "submitted"
        )

        orders.append({
            "anchor": order_lines[0],
            "lines": order_lines,
            "views": views,
            "eligible": eligible,
            "mcf": mcf,
            "state": state,
            "group_ids": sorted({
                view["group_id"]
                for view in views
                if view["group_id"]
            }),
        })

    return orders


def _source_is_ebay(order: MarketplaceOrder) -> bool:
    return bool(order.store and "ebay" in str(order.store.platform or "").lower())


def _active_fba_store() -> Store | None:
    return (
        Store.query
        .filter(Store.is_active == True)  # noqa: E712
        .filter(Store.platform.in_(["AmazonFBA", "amazon_fba", "Amazon", "amazon"]))
        .order_by(Store.platform == "AmazonFBA", Store.id)
        .first()
    )


def _guard(store: Store, context: str) -> dict:
    return is_runtime_action_allowed(
        store,
        "push",
        manual=True,
        context={"actor_user": current_user, "context": context},
    )


def _safe_seller_fulfillment_id(order: MarketplaceOrder) -> str:
    suffix = re.sub(r"[^A-Za-z0-9-]", "", order.marketplace_order_id or "")[-20:]
    return f"MCF-EBAY-{order.id}-{suffix}"[:50]


@governed_mcf_bp.get("/orders-mcf")
@login_required
def orders_mcf_page():
    orders = _bulk_orders(limit=100)
    return render_template("mcf_orders.html", orders=orders)


@governed_mcf_bp.get("/orders-mcf/<int:order_id>")
@login_required
def order_mcf_detail_page(order_id: int):
    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None:
        flash("Order not found.", "danger")
        return redirect(url_for("governed_mcf.orders_mcf_page"))

    lines = _order_lines(anchor)
    views = [_line_view(line) for line in lines]
    mcf = next((line.mcf_order for line in lines if line.mcf_order_id), None)
    return render_template(
        "mcf_order_detail.html",
        anchor=anchor,
        lines=lines,
        views=views,
        mcf=mcf,
        eligible=bool(views) and all(view["eligible"] for view in views),
    )


@governed_mcf_bp.post("/governed/orders-mcf/<int:order_id>/send")
@login_required
def send_order_to_mcf(order_id: int):
    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None:
        flash("Order not found.", "danger")
        return redirect(url_for("governed_mcf.orders_mcf_page"))

    lines = _order_lines(anchor)
    existing = next((line.mcf_order for line in lines if line.mcf_order_id), None)
    if existing:
        flash(f"Order already has MCF reference {existing.seller_fulfillment_order_id}.", "warning")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    if not _source_is_ebay(anchor):
        flash("The first governed MCF path currently accepts eBay source orders only.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    fba_store = _active_fba_store()
    if fba_store is None:
        flash("No active Amazon FBA store is configured.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    amazon_guard = _guard(fba_store, "manual_mcf_submit")
    ebay_guard = _guard(anchor.store, "mcf_acceptance_ebay_dispatch")
    if not amazon_guard.get("allowed"):
        flash(f"Amazon MCF blocked: {amazon_guard.get('reason')}", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))
    if not ebay_guard.get("allowed"):
        flash(f"eBay dispatch blocked: {ebay_guard.get('reason')}", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    views = [_line_view(line) for line in lines]
    if not views or not all(view["eligible"] for view in views):
        flash("Every order line must resolve to an MCF-enabled FBA listing with enough available stock.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    address = {
        "name": str(request.form.get("name") or anchor.ship_to_name or "").strip(),
        "address_line1": str(request.form.get("address_line1") or anchor.ship_to_address or "").strip(),
        "address_line2": str(request.form.get("address_line2") or "").strip(),
        "city": str(request.form.get("city") or anchor.ship_to_city or "").strip(),
        "state": str(request.form.get("state") or "").strip(),
        "postcode": str(request.form.get("postcode") or anchor.ship_to_postcode or "").strip(),
        "country": str(request.form.get("country") or anchor.ship_to_country or "GB").strip().upper(),
        "phone": str(request.form.get("phone") or "").strip(),
    }
    missing = [key for key in ("name", "address_line1", "city", "postcode", "country") if not address[key]]
    if missing:
        flash(f"Complete the delivery address before sending: {', '.join(missing)}.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    speed = str(request.form.get("shipping_speed") or "Standard").strip().title()
    if speed not in {"Standard", "Expedited", "Priority"}:
        speed = "Standard"

    mcf = MCFOrder(
        source_order_id=anchor.marketplace_order_id,
        source_channel="eBay",
        source_store_id=anchor.store_id,
        fba_store_id=fba_store.id,
        seller_fulfillment_order_id=_safe_seller_fulfillment_id(anchor),
        displayable_order_id=(anchor.marketplace_order_id or "")[:50],
        displayable_comment=f"eBay order {anchor.marketplace_order_id}",
        destination_name=address["name"],
        destination_address_line1=address["address_line1"],
        destination_address_line2=address["address_line2"],
        destination_city=address["city"],
        destination_state=address["state"],
        destination_postcode=address["postcode"],
        destination_country=address["country"],
        destination_phone=address["phone"],
        shipping_speed=speed,
        status="pending",
        order_total=sum((line.line_total or 0) for line in lines),
        platform_fees=sum((line.platform_fee or 0) for line in lines),
        currency="GBP",
        created_by_id=getattr(current_user, "id", None),
    )
    db.session.add(mcf)
    db.session.flush()

    service = MCFService()
    fee_items = []
    total_product_cost = 0.0
    for view in views:
        line = view["line"]
        fba = view["fba"]
        fee = service.fee_calculator.calculate_item_fee(line.quantity or 1, speed, 0.3)
        db.session.add(MCFOrderItem(
            mcf_order_id=mcf.id,
            source_sku=line.sku,
            fba_listing_id=None,
            fba_sku=fba.seller_sku,
            asin=fba.asin,
            fnsku=fba.fnsku,
            quantity=line.quantity or 1,
            unit_price=line.unit_price or 0,
            product_cost=line.product_cost or 0,
            mcf_fulfillment_fee=fee["total_fee"],
            mcf_first_unit_fee=fee["first_unit_fee"],
            mcf_additional_unit_fee=fee["additional_unit_fee"],
            status="pending",
        ))
        fee_items.append({"quantity": line.quantity or 1, "weight_kg": 0.3})
        total_product_cost += (line.product_cost or 0) * (line.quantity or 1)

    order_fee = service.fee_calculator.calculate_order_fee(fee_items, speed)
    mcf.mcf_per_shipment_fee = order_fee["per_shipment_fee"]
    mcf.mcf_fulfillment_fee = order_fee["fulfillment_fee"]
    mcf.total_mcf_fee = order_fee["total_fee"]
    mcf.product_cost = total_product_cost
    mcf.calculate_totals()
    db.session.commit()

    submitted, message = service.submit_mcf_to_amazon(mcf)
    if not submitted:
        flash(message, "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    for line in lines:
        line.mcf_order_id = mcf.id
        line.fulfillment_type = "FBA"
        line.status = "mcf_accepted"
        line.processed_at = datetime.utcnow()
    db.session.commit()

    dispatch = complete_sale(anchor)
    if dispatch.get("success"):
        for line in lines:
            line.shipped_at = anchor.shipped_at
            line.status = "mcf_dispatched_tracking_pending"
        db.session.commit()
        flash("Amazon accepted the MCF order and eBay was marked dispatched. Tracking is pending.", "success")
    else:
        for line in lines:
            line.status = "mcf_accepted_dispatch_failed"
            line.error_message = dispatch.get("error")
        db.session.commit()
        flash(f"Amazon accepted MCF, but eBay dispatch failed: {dispatch.get('error')}", "danger")

    return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))


@governed_mcf_bp.post("/governed/orders-mcf/<int:order_id>/refresh")
@login_required
def refresh_mcf_order(order_id: int):
    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None or not anchor.mcf_order:
        flash("No MCF order is linked to this order.", "danger")
        return redirect(url_for("governed_mcf.orders_mcf_page"))

    mcf = anchor.mcf_order
    service = MCFService()
    success, result = service.get_mcf_order_status(mcf)
    if not success:
        flash(f"Amazon MCF status refresh failed: {result.get('error')}", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    lines = _order_lines(anchor)
    if mcf.tracking_number:
        ebay_guard = _guard(anchor.store, "mcf_tracking_ebay_enrichment")
        if not ebay_guard.get("allowed"):
            flash(f"Tracking received, but eBay update is blocked: {ebay_guard.get('reason')}", "warning")
        else:
            dispatch = complete_sale(anchor, carrier=mcf.carrier or "Other", tracking_number=mcf.tracking_number)
            if dispatch.get("success"):
                for line in lines:
                    line.carrier = mcf.carrier
                    line.tracking_number = mcf.tracking_number
                    line.shipped_at = anchor.shipped_at or datetime.utcnow()
                    line.status = "mcf_tracking_updated"
                    line.error_message = None
                db.session.commit()
                flash("Amazon tracking was added to the existing eBay dispatch.", "success")
            else:
                for line in lines:
                    line.status = "mcf_tracking_update_failed"
                    line.error_message = dispatch.get("error")
                db.session.commit()
                flash(f"Amazon tracking received, but eBay update failed: {dispatch.get('error')}", "danger")
    else:
        flash(f"Amazon status updated to {mcf.amazon_status or mcf.status}; tracking is not available yet.", "info")

    return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))
