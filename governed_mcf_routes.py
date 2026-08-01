"""Governed manual MCF workflow.

Page reads are database-only. Marketplace writes happen only through explicit
POST actions and the existing runtime push guard.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import re

from flask import (
    Blueprint,
    flash,
    has_request_context,
    redirect,
    render_template,
    request,
    url_for,
)
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


def _source_is_non_amazon_marketplace(
    order: MarketplaceOrder,
) -> bool:
    """
    Amazon-origin orders must never enter Amazon MCF again.

    MCF work is only for orders received from an external/non-Amazon
    marketplace whose linked Product Linking group resolves to canonical
    Amazon FBA inventory.
    """
    store = order.store

    if store is None:
        return False

    platform = str(store.platform or "").strip().lower()

    return bool(platform) and "amazon" not in platform


def _bulk_orders(limit: int = 100) -> list[dict]:
    """
    Bulk-load external marketplace orders only when their complete linked group
    resolves to canonical, MCF-enabled Amazon FBA inventory.

    Amazon-origin orders are already within Amazon fulfilment and must never be
    presented as new MCF work. Search, filtering and paging remain browser-local.
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
    group_ids = set()

    for line in lines:
        lines_by_key[
            (line.store_id, line.marketplace_order_id)
        ].append(line)

        stock = line.warehouse_stock
        if stock and stock.master_product_group_id:
            group_ids.add(int(stock.master_product_group_id))

    group_members = (
        WarehouseStock.query
        .filter(
            WarehouseStock.master_product_group_id.in_(group_ids)
        )
        .all()
        if group_ids else []
    )

    stock_ids_by_group = defaultdict(set)

    for stock in group_members:
        if stock.master_product_group_id:
            stock_ids_by_group[
                int(stock.master_product_group_id)
            ].add(int(stock.id))

    all_group_stock_ids = {
        stock_id
        for values in stock_ids_by_group.values()
        for stock_id in values
    }

    amazon_store_ids = _amazon_store_ids()

    amazon_listings = (
        MarketplaceListing.query
        .filter(
            MarketplaceListing.store_id.in_(amazon_store_ids),
            MarketplaceListing.warehouse_stock_id.in_(
                all_group_stock_ids
            ),
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .all()
        if amazon_store_ids and all_group_stock_ids else []
    )

    listing_skus_by_stock = defaultdict(set)
    listing_fnskus_by_stock = defaultdict(set)

    for listing in amazon_listings:
        stock_id = int(listing.warehouse_stock_id)

        seller_sku = str(listing.external_sku or "").strip()
        fnsku = str(listing.fnsku or "").strip()

        if seller_sku:
            listing_skus_by_stock[stock_id].add(seller_sku)
        if fnsku:
            listing_fnskus_by_stock[stock_id].add(fnsku)

    all_seller_skus = {
        sku
        for values in listing_skus_by_stock.values()
        for sku in values
    }

    all_fnskus = {
        fnsku
        for values in listing_fnskus_by_stock.values()
        for fnsku in values
    }

    inventory_rows = []

    if amazon_store_ids and (all_seller_skus or all_fnskus):
        inventory_query = AmazonFBAInventory.query.filter(
            AmazonFBAInventory.store_id.in_(amazon_store_ids),
            AmazonFBAInventory.is_active == True,  # noqa: E712
            AmazonFBAInventory.is_archived == False,  # noqa: E712
            AmazonFBAInventory.mcf_enabled == True,  # noqa: E712
        )

        if all_seller_skus and all_fnskus:
            inventory_query = inventory_query.filter(
                db.or_(
                    AmazonFBAInventory.seller_sku.in_(
                        all_seller_skus
                    ),
                    AmazonFBAInventory.fnsku.in_(all_fnskus),
                )
            )
        elif all_seller_skus:
            inventory_query = inventory_query.filter(
                AmazonFBAInventory.seller_sku.in_(
                    all_seller_skus
                )
            )
        else:
            inventory_query = inventory_query.filter(
                AmazonFBAInventory.fnsku.in_(all_fnskus)
            )

        inventory_rows = inventory_query.all()

    inventory_by_sku = defaultdict(list)
    inventory_by_fnsku = defaultdict(list)

    for inventory in inventory_rows:
        seller_sku = str(inventory.seller_sku or "").strip()
        fnsku = str(inventory.fnsku or "").strip()

        if seller_sku:
            inventory_by_sku[seller_sku].append(inventory)
        if fnsku:
            inventory_by_fnsku[fnsku].append(inventory)

    orders = []

    for key in keys:
        order_lines = lines_by_key.get(key, [])
        if not order_lines:
            continue

        anchor = order_lines[0]

        # MCF is only for external/non-Amazon marketplace sales. Amazon orders
        # must not be sent back into Amazon through MCF, even when their SKU is
        # linked to an FBA-backed Product Linking group.
        if not _source_is_non_amazon_marketplace(anchor):
            continue

        # Persistent queue cleanup. The marketplace order and any linked MCF
        # record remain untouched and available for audit/tracking.
        if any(
            bool(line.mcf_queue_hidden)
            for line in order_lines
        ):
            continue

        views = []

        for line in order_lines:
            stock = line.warehouse_stock
            group_id = (
                int(stock.master_product_group_id)
                if stock and stock.master_product_group_id
                else None
            )

            candidates = []

            if group_id:
                for stock_id in stock_ids_by_group.get(
                    group_id,
                    set(),
                ):
                    for seller_sku in listing_skus_by_stock.get(
                        stock_id,
                        set(),
                    ):
                        candidates.extend(
                            inventory_by_sku.get(seller_sku, [])
                        )

                    for fnsku in listing_fnskus_by_stock.get(
                        stock_id,
                        set(),
                    ):
                        candidates.extend(
                            inventory_by_fnsku.get(fnsku, [])
                        )

            candidates = sorted(
                {
                    int(row.id): row
                    for row in candidates
                }.values(),
                key=lambda row: (
                    int(row.available_quantity or 0),
                    row.updated_at or datetime.min,
                    int(row.id or 0),
                ),
                reverse=True,
            )

            fba = candidates[0] if candidates else None

            views.append({
                "line": line,
                "group_id": group_id,
                "fba": fba,
                "eligible": bool(
                    fba
                    and int(fba.available_quantity or 0)
                    >= int(line.quantity or 0)
                ),
            })

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

        # Non-FBA and partially resolved groups never appear as new MCF work.
        if not eligible and not mcf:
            continue

        state = (
            "ready"
            if eligible and not mcf
            else "failed"
            if mcf and mcf.status == "failed"
            else "submitted"
        )

        orders.append({
            "anchor": anchor,
            "lines": order_lines,
            "views": views,
            "eligible": eligible,
            "mcf": mcf,
            "state": state,
            "auto_release_at": (
                _mcf_auto_release_at(anchor)
                if state == "ready"
                else None
            ),
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


def _guard(
    store: Store,
    context: str,
    *,
    actor_user=None,
    manual: bool = True,
) -> dict:
    """Apply the existing governed push guard for UI or runtime callers."""
    resolved_actor = actor_user

    if resolved_actor is None and has_request_context():
        resolved_actor = current_user

    return is_runtime_action_allowed(
        store,
        "push",
        manual=manual,
        context={
            "actor_user": resolved_actor,
            "context": context,
        },
    )


def _mcf_auto_release_at(
    order: MarketplaceOrder,
) -> datetime | None:
    """Return the server-controlled MCF release time.

    The existing governed runtime wakes for the exact order identity when this
    time is reached. No browser page is required.
    """
    if order.created_at is None:
        return None

    return order.created_at + timedelta(hours=1)


def _resolve_mcf_submission_inputs(
    anchor: MarketplaceOrder,
    *,
    form_data=None,
) -> dict:
    """Resolve delivery and speed inputs without requiring a Flask request."""
    data = dict(form_data or {})

    address = {
        "name": str(
            data.get("name")
            or anchor.ship_to_name
            or ""
        ).strip(),
        "address_line1": str(
            data.get("address_line1")
            or anchor.ship_to_address
            or ""
        ).strip(),
        "address_line2": str(
            data.get("address_line2")
            or ""
        ).strip(),
        "city": str(
            data.get("city")
            or anchor.ship_to_city
            or ""
        ).strip(),
        "state": str(
            data.get("state")
            or ""
        ).strip(),
        "postcode": str(
            data.get("postcode")
            or anchor.ship_to_postcode
            or ""
        ).strip(),
        "country": str(
            data.get("country")
            or anchor.ship_to_country
            or "GB"
        ).strip().upper(),
        "email": str(
            data.get("email")
            or anchor.ship_to_email
            or ""
        ).strip(),
        "phone": str(
            data.get("phone")
            or anchor.ship_to_phone
            or ""
        ).strip(),
    }

    speed = str(
        data.get("shipping_speed")
        or "Standard"
    ).strip().title()

    if speed not in {"Standard", "Expedited", "Priority"}:
        speed = "Standard"

    missing = [
        key
        for key in (
            "name",
            "address_line1",
            "city",
            "postcode",
            "country",
        )
        if not address[key]
    ]

    return {
        "address": address,
        "shipping_speed": speed,
        "missing": missing,
    }


def _safe_seller_fulfillment_id(order: MarketplaceOrder) -> str:
    suffix = re.sub(r"[^A-Za-z0-9-]", "", order.marketplace_order_id or "")[-20:]
    return f"MCF-EBAY-{order.id}-{suffix}"[:50]


@governed_mcf_bp.get("/orders-mcf")
@login_required
def orders_mcf_page():
    orders = _bulk_orders(limit=100)
    return render_template("mcf_orders.html", orders=orders)


@governed_mcf_bp.post(
    "/governed/orders-mcf/remove-selected"
)
@login_required
def remove_selected_mcf_orders():
    raw_ids = request.form.getlist("order_ids")
    order_ids = []

    for value in raw_ids[:100]:
        try:
            order_ids.append(int(value))
        except (TypeError, ValueError):
            continue

    if not order_ids:
        flash("Select at least one order.", "warning")
        return redirect(
            url_for("governed_mcf.orders_mcf_page")
        )

    anchors = (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.id.in_(order_ids))
        .all()
    )

    removed_orders = 0
    seen_keys = set()

    for anchor in anchors:
        key = (
            anchor.store_id,
            anchor.marketplace_order_id,
        )

        if key in seen_keys:
            continue

        seen_keys.add(key)

        for line in _order_lines(anchor):
            line.mcf_queue_hidden = True

        removed_orders += 1

    db.session.commit()

    flash(
        f"Removed {removed_orders} order(s) from the MCF queue. "
        "Marketplace orders and MCF records were not deleted.",
        "success",
    )

    return redirect(
        url_for("governed_mcf.orders_mcf_page")
    )


@governed_mcf_bp.get("/orders-mcf/<int:order_id>")
@login_required
def order_mcf_detail_page(order_id: int):
    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None:
        flash("Order not found.", "danger")
        return redirect(
            url_for("governed_mcf.orders_mcf_page")
        )

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


def run_governed_mcf_submission(
    order_id: int,
    *,
    auto_release: bool = False,
    form_data=None,
    actor_user=None,
) -> dict:
    """Run the existing governed MCF submission without an HTTP dependency."""
    data = dict(form_data or {})
    captured_messages = []

    def _capture_flash(message, category="info"):
        captured_messages.append({
            "message": str(message),
            "category": str(category or "info"),
        })

    def _capture_url_for(endpoint, **kwargs):
        return {
            "endpoint": endpoint,
            "kwargs": kwargs,
        }

    def _capture_redirect(target):
        latest = (
            captured_messages[-1]
            if captured_messages
            else {
                "message": "MCF action completed.",
                "category": "info",
            }
        )

        category = latest["category"]

        return {
            "success": category == "success",
            "skipped": category in {"warning", "info"},
            "message": latest["message"],
            "category": category,
            "redirect": target,
            "auto_release": bool(auto_release),
            "marketplace_order_row_id": order_id,
        }

    # Preserve the established route control flow while converting its
    # presentation effects into a plain result contract.
    flash = _capture_flash
    url_for = _capture_url_for
    redirect = _capture_redirect

    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None:
        flash("Order not found.", "danger")
        return redirect(url_for("governed_mcf.orders_mcf_page"))

    lines = _order_lines(anchor)

    cancelled_statuses = {
        "cancelled",
        "canceled",
        "cancellation",
        "cancel_requested",
    }

    if any(
        str(line.status or "").strip().lower()
        in cancelled_statuses
        for line in lines
    ):
        flash(
            "MCF submission stopped because the source order is cancelled.",
            "warning",
        )
        return redirect(
            url_for(
                "governed_mcf.order_mcf_detail_page",
                order_id=anchor.id,
            )
        )

    if auto_release:
        release_at = _mcf_auto_release_at(anchor)

        if release_at is None or datetime.utcnow() < release_at:
            flash(
                "The one-hour MCF cancellation window has not completed.",
                "warning",
            )
            return redirect(
                url_for("governed_mcf.orders_mcf_page")
            )

    existing = next(
        (
            line.mcf_order
            for line in lines
            if line.mcf_order_id
        ),
        None,
    )

    retry_existing = bool(
        existing
        and str(existing.status or "").lower()
        == "failed"
    )

    if existing and not retry_existing:
        flash(
            "Order already has MCF reference "
            f"{existing.seller_fulfillment_order_id}.",
            "warning",
        )
        return redirect(
            url_for(
                "governed_mcf.order_mcf_detail_page",
                order_id=anchor.id,
            )
        )

    if not _source_is_ebay(anchor):
        flash("The first governed MCF path currently accepts eBay source orders only.", "danger")
        return redirect(
            url_for(
                "governed_mcf.order_mcf_detail_page",
                order_id=anchor.id,
            )
        )

    fba_store = _active_fba_store()
    if fba_store is None:
        flash("No active Amazon FBA store is configured.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    amazon_guard = _guard(
        fba_store,
        (
            "warehouse_one_hour_auto_mcf_submit"
            if auto_release
            else "manual_mcf_submit"
        ),
        actor_user=actor_user,
        manual=not auto_release,
    )
    ebay_guard = _guard(
        anchor.store,
        "mcf_acceptance_ebay_dispatch",
        actor_user=actor_user,
        manual=not auto_release,
    )
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

    submission_inputs = _resolve_mcf_submission_inputs(
        anchor,
        form_data=data,
    )
    address = submission_inputs["address"]
    missing = submission_inputs["missing"]
    if missing:
        flash(f"Complete the delivery address before sending: {', '.join(missing)}.", "danger")
        return redirect(url_for("governed_mcf.order_mcf_detail_page", order_id=anchor.id))

    # Preserve any reviewed/manual contact corrections on every line of the
    # source marketplace order.
    for line in lines:
        if address["email"]:
            line.ship_to_email = address["email"]
        if address["phone"]:
            line.ship_to_phone = address["phone"]

    speed = submission_inputs["shipping_speed"]

    service = MCFService()

    if retry_existing:
        mcf = existing

        mcf.fba_store_id = fba_store.id
        mcf.destination_name = address["name"]
        mcf.destination_address_line1 = (
            address["address_line1"]
        )
        mcf.destination_address_line2 = (
            address["address_line2"]
        )
        mcf.destination_city = address["city"]
        mcf.destination_state = address["state"]
        mcf.destination_postcode = address["postcode"]
        mcf.destination_country = address["country"]
        mcf.destination_phone = address["phone"]
        mcf.shipping_speed = speed
        mcf.status = "pending"
        mcf.last_error = None

        for item in mcf.items.all():
            item.status = "pending"

        db.session.commit()
    else:
        mcf = MCFOrder(
            source_order_id=anchor.marketplace_order_id,
            source_channel="eBay",
            source_store_id=anchor.store_id,
            fba_store_id=fba_store.id,
            seller_fulfillment_order_id=(
                _safe_seller_fulfillment_id(anchor)
            ),
            displayable_order_id=(
                anchor.marketplace_order_id or ""
            )[:50],
            displayable_comment=(
                f"eBay order "
                f"{anchor.marketplace_order_id}"
            ),
            destination_name=address["name"],
            destination_address_line1=(
                address["address_line1"]
            ),
            destination_address_line2=(
                address["address_line2"]
            ),
            destination_city=address["city"],
            destination_state=address["state"],
            destination_postcode=address["postcode"],
            destination_country=address["country"],
            destination_phone=address["phone"],
            shipping_speed=speed,
            status="pending",
            order_total=sum(
                (line.line_total or 0)
                for line in lines
            ),
            platform_fees=sum(
                (line.platform_fee or 0)
                for line in lines
            ),
            currency="GBP",
            created_by_id=getattr(
                actor_user,
                "id",
                None,
            ),
        )

        db.session.add(mcf)
        db.session.flush()

        fee_items = []
        total_product_cost = 0.0

        for view in views:
            line = view["line"]
            fba = view["fba"]

            fee = (
                service.fee_calculator
                .calculate_item_fee(
                    line.quantity or 1,
                    speed,
                    0.3,
                )
            )

            db.session.add(
                MCFOrderItem(
                    mcf_order_id=mcf.id,
                    source_sku=line.sku,
                    fba_listing_id=None,
                    fba_sku=fba.seller_sku,
                    asin=fba.asin,
                    fnsku=fba.fnsku,
                    quantity=line.quantity or 1,
                    unit_price=line.unit_price or 0,
                    product_cost=line.product_cost or 0,
                    mcf_fulfillment_fee=(
                        fee["total_fee"]
                    ),
                    mcf_first_unit_fee=(
                        fee["first_unit_fee"]
                    ),
                    mcf_additional_unit_fee=(
                        fee["additional_unit_fee"]
                    ),
                    status="pending",
                )
            )

            fee_items.append({
                "quantity": line.quantity or 1,
                "weight_kg": 0.3,
            })

            total_product_cost += (
                (line.product_cost or 0)
                * (line.quantity or 1)
            )

        order_fee = (
            service.fee_calculator
            .calculate_order_fee(
                fee_items,
                speed,
            )
        )

        mcf.mcf_per_shipment_fee = (
            order_fee["per_shipment_fee"]
        )
        mcf.mcf_fulfillment_fee = (
            order_fee["fulfillment_fee"]
        )
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

    if auto_release:
        return redirect(
            url_for("governed_mcf.orders_mcf_page")
        )

    return redirect(
        url_for(
            "governed_mcf.order_mcf_detail_page",
            order_id=anchor.id,
        )
    )


@governed_mcf_bp.post(
    "/governed/orders-mcf/<int:order_id>/send"
)
@login_required
def send_order_to_mcf(order_id: int):
    """HTTP adapter into the shared governed MCF submission process."""
    auto_release = (
        str(request.form.get("auto_release") or "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )

    result = run_governed_mcf_submission(
        order_id,
        auto_release=auto_release,
        form_data=request.form,
        actor_user=current_user,
    )

    flash(
        result.get("message") or "MCF action completed.",
        result.get("category") or "info",
    )

    target = result.get("redirect") or {}
    endpoint = (
        target.get("endpoint")
        or "governed_mcf.order_mcf_detail_page"
    )
    kwargs = dict(target.get("kwargs") or {})

    if endpoint.endswith("order_mcf_detail_page"):
        kwargs.setdefault("order_id", order_id)

    return redirect(url_for(endpoint, **kwargs))


@governed_mcf_bp.post("/governed/orders-mcf/<int:order_id>/refresh")
@login_required
def refresh_mcf_order(order_id: int):
    return_to_list = (
        str(request.form.get("return_to") or "").strip().lower()
        == "list"
    )

    def _refresh_redirect(anchor_id: int | None = None):
        if return_to_list:
            return redirect(
                url_for("governed_mcf.orders_mcf_page")
            )

        if anchor_id is not None:
            return redirect(
                url_for(
                    "governed_mcf.order_mcf_detail_page",
                    order_id=anchor_id,
                )
            )

        return redirect(
            url_for("governed_mcf.orders_mcf_page")
        )

    anchor = db.session.get(MarketplaceOrder, order_id)
    if anchor is None or not anchor.mcf_order:
        flash("No MCF order is linked to this order.", "danger")
        return redirect(url_for("governed_mcf.orders_mcf_page"))

    mcf = anchor.mcf_order
    service = MCFService()
    success, result = service.get_mcf_order_status(mcf)
    if not success:
        flash(f"Amazon MCF status refresh failed: {result.get('error')}", "danger")
        return _refresh_redirect(anchor.id)

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

    return _refresh_redirect(anchor.id)
