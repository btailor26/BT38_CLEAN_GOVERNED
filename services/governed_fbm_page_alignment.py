"""Bound the existing FBM reads without introducing a second workflow.

This alignment keeps the registered FBM endpoints, existing template and
shipping execution handlers unchanged. Ordinary page refresh hydrates the
latest 15 distinct FBM orders; older records are user-expanded in 15-order
increments. Ready-to-Ship preparation reads only persisted BT38 facts for the
selected orders and never hydrates a marketplace from a UI GET.

No marketplace/provider calls, DB writes, reconciliation, scheduler or MCF path
is introduced here.
"""
from __future__ import annotations

from urllib.parse import urlencode

from flask import jsonify, request, render_template
from flask_login import login_required
from sqlalchemy import func, tuple_
from sqlalchemy.orm import joinedload

from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder, ProductPackMapping
from governed_fbm_routes import (
    _is_fbm_eligible,
    _marketplace_shipping_mode,
    _platform,
    _route_state,
    _shipment_map,
    _shipping_provider_options,
    _store_name,
)
from services.fbm_shipping_state import provider_case_eligibility, shipment_confirmation_state


_FBM_PAGE_SIZE = 15
_FBM_MAX_EXPANDED = 300
_FBM_SHIPPING_SELECTION_MAX = 50


def _requested_limit() -> int:
    try:
        requested = int(request.args.get("limit") or _FBM_PAGE_SIZE)
    except (TypeError, ValueError):
        requested = _FBM_PAGE_SIZE
    requested = max(_FBM_PAGE_SIZE, requested)
    return min(_FBM_MAX_EXPANDED, ((requested + _FBM_PAGE_SIZE - 1) // _FBM_PAGE_SIZE) * _FBM_PAGE_SIZE)


def _profile_map(rows: list[MarketplaceOrder]) -> dict[tuple[int, str], FBMOrderProfile]:
    identities = sorted({
        (int(row.store_id), str(row.marketplace_order_id))
        for row in rows
        if row.store_id is not None and row.marketplace_order_id
    })
    if not identities:
        return {}

    profiles = (
        db.session.query(FBMOrderProfile)
        .filter(tuple_(FBMOrderProfile.store_id, FBMOrderProfile.marketplace_order_id).in_(identities))
        .order_by(FBMOrderProfile.id.desc())
        .all()
    )
    result: dict[tuple[int, str], FBMOrderProfile] = {}
    for profile in profiles:
        key = (int(profile.store_id), str(profile.marketplace_order_id))
        if key not in result:
            result[key] = profile
    return result


def _latest_distinct_fbm_rows(limit: int) -> tuple[list[MarketplaceOrder], bool]:
    """Read only the requested distinct persisted FBM orders plus one sentinel."""
    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )

    latest_ids = (
        db.session.query(func.max(MarketplaceOrder.id).label("id"))
        .filter(*eligible)
        .group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)
        .subquery()
    )

    query = (
        db.session.query(MarketplaceOrder)
        .join(latest_ids, MarketplaceOrder.id == latest_ids.c.id)
        .options(
            joinedload(MarketplaceOrder.store),
            joinedload(MarketplaceOrder.warehouse_stock),
        )
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
    )

    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()

    if platform_filter:
        query = query.filter(MarketplaceOrder.store.has(platform=platform_filter))

    tracking_present = MarketplaceOrder.tracking_number.isnot(None) & (MarketplaceOrder.tracking_number != "")
    if status_filter == "tracking recorded":
        query = query.filter(tracking_present)
    elif status_filter == "dispatched":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.isnot(None))
    elif status_filter == "ready for fbm routing":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.is_(None))

    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    return rows[:limit], has_more


def _expand_control(html: str, *, visible_limit: int, has_more: bool) -> str:
    """Add the bounded expansion control to the existing FBM card only."""
    if not html:
        return html

    params = {}
    platform_filter = str(request.args.get("platform") or "").strip()
    status_filter = str(request.args.get("status") or "").strip()
    if platform_filter:
        params["platform"] = platform_filter
    if status_filter:
        params["status"] = status_filter

    actions = []
    if visible_limit > _FBM_PAGE_SIZE:
        collapse_params = dict(params)
        collapse_params["limit"] = _FBM_PAGE_SIZE
        actions.append(
            f'<a class="btn btn-sm btn-outline-secondary" href="{request.path}?{urlencode(collapse_params)}">Show latest 15</a>'
        )
    if has_more and visible_limit < _FBM_MAX_EXPANDED:
        expand_params = dict(params)
        expand_params["limit"] = min(_FBM_MAX_EXPANDED, visible_limit + _FBM_PAGE_SIZE)
        actions.append(
            f'<a id="fbmExpandOrders" class="btn btn-sm btn-outline-primary" href="{request.path}?{urlencode(expand_params)}">Show 15 more</a>'
        )

    if not actions:
        return html

    control = (
        '<div class="card-footer d-flex justify-content-between align-items-center flex-wrap gap-2">'
        f'<span class="small text-muted">Showing the latest {visible_limit} FBM orders. Older orders load only when expanded.</span>'
        f'<div class="d-flex gap-2'>{"".join(actions)}</div>'
        '</div>'
    )
    marker = "</tbody></table></div>\n</div>"
    if marker not in html:
        return html
    return html.replace(marker, f"</tbody></table></div>\n{control}\n</div>", 1)


def _selected_order_ids() -> list[int]:
    raw_ids = str(request.args.get("order_ids") or "")
    result: list[int] = []
    for value in raw_ids.split(","):
        try:
            order_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in result:
            result.append(order_id)
        if len(result) >= _FBM_SHIPPING_SELECTION_MAX:
            break
    return result


def _selected_rows(order_ids: list[int]) -> list[MarketplaceOrder]:
    if not order_ids:
        return []
    rows = (
        db.session.query(MarketplaceOrder)
        .options(
            joinedload(MarketplaceOrder.store),
            joinedload(MarketplaceOrder.warehouse_stock),
        )
        .filter(MarketplaceOrder.id.in_(order_ids))
        .all()
    )
    by_id = {row.id: row for row in rows if _is_fbm_eligible(row)}
    return [by_id[order_id] for order_id in order_ids if order_id in by_id]


def _order_lines_map(rows: list[MarketplaceOrder]) -> dict[tuple[int, str], list[MarketplaceOrder]]:
    identities = sorted({
        (int(row.store_id), str(row.marketplace_order_id))
        for row in rows
        if row.store_id is not None and row.marketplace_order_id
    })
    if not identities:
        return {}
    lines = (
        db.session.query(MarketplaceOrder)
        .options(joinedload(MarketplaceOrder.warehouse_stock))
        .filter(tuple_(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id).in_(identities))
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    result: dict[tuple[int, str], list[MarketplaceOrder]] = {}
    for line in lines:
        key = (int(line.store_id), str(line.marketplace_order_id))
        result.setdefault(key, []).append(line)
    return result


def _pack_mapping_by_sku(lines_by_order: dict[tuple[int, str], list[MarketplaceOrder]]) -> dict[str, ProductPackMapping]:
    skus = sorted({
        str(lines[0].sku).strip()
        for lines in lines_by_order.values()
        if len(lines) == 1 and getattr(lines[0], "sku", None)
    })
    if not skus:
        return {}
    mappings = (
        db.session.query(ProductPackMapping)
        .filter(ProductPackMapping.single_sku.in_(skus), ProductPackMapping.is_active == True)  # noqa: E712
        .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
        .all()
    )
    result: dict[str, ProductPackMapping] = {}
    for mapping in mappings:
        sku = str(mapping.single_sku or "").strip()
        if sku and sku not in result:
            result[sku] = mapping
    return result


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _persisted_parcel(lines: list[MarketplaceOrder], mappings: dict[str, ProductPackMapping]) -> dict:
    total_weight = 0.0
    all_weights_known = bool(lines)
    for line in lines:
        warehouse = getattr(line, "warehouse_stock", None)
        unit_weight = _positive_float(getattr(warehouse, "product_weight_kg", None)) if warehouse else None
        if not unit_weight:
            all_weights_known = False
            continue
        try:
            quantity = max(1, int(getattr(line, "quantity", 1) or 1))
        except (TypeError, ValueError):
            quantity = 1
        total_weight += unit_weight * quantity

    weight = total_weight if all_weights_known and total_weight > 0 else None
    length = width = height = None
    sources = ["warehouse_order_weight"] if weight else []

    if len(lines) == 1:
        line = lines[0]
        sku = str(getattr(line, "sku", None) or "").strip()
        mapping = mappings.get(sku)
        if mapping is not None:
            try:
                quantity = max(1, int(getattr(line, "quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            try:
                units = int(getattr(mapping, "units_per_carton", None) or 1)
            except (TypeError, ValueError):
                units = 1
            if quantity == 1 and units == 1:
                mapped_weight = _positive_float(getattr(mapping, "carton_weight_kg", None))
                if mapped_weight:
                    weight = mapped_weight
                    sources = [source for source in sources if source != "warehouse_order_weight"]
                    sources.append("pack_mapping_weight")
                length = _positive_float(getattr(mapping, "carton_length_cm", None))
                width = _positive_float(getattr(mapping, "carton_width_cm", None))
                height = _positive_float(getattr(mapping, "carton_height_cm", None))
                if any((length, width, height)):
                    sources.append("pack_mapping")
    elif len(lines) > 1:
        sources.append("multi_item_dimensions_required")

    return {
        "weight_kg": weight,
        "length_cm": length,
        "width_cm": width,
        "height_cm": height,
        "source": "+".join(sources) if sources else "missing",
        "complete": all(value is not None and float(value) > 0 for value in (weight, length, width, height)),
    }


def install_governed_fbm_page_alignment(app) -> None:
    """Replace expensive FBM read endpoints while preserving action execution."""
    if getattr(app, "_bt38_fbm_page_alignment_installed", False):
        return

    page_endpoint = "governed_fbm.fbm_page"
    shipping_endpoint = "governed_fbm.fbm_shipping_options"
    if page_endpoint not in app.view_functions:
        raise RuntimeError("governed FBM page endpoint is not registered")
    if shipping_endpoint not in app.view_functions:
        raise RuntimeError("governed FBM shipping-options endpoint is not registered")

    @login_required
    def bounded_fbm_page():
        platform_filter = str(request.args.get("platform") or "").strip().lower()
        status_filter = str(request.args.get("status") or "").strip().lower()
        visible_limit = _requested_limit()

        rows, has_more = _latest_distinct_fbm_rows(visible_limit)
        shipments = _shipment_map(rows)
        profiles = _profile_map(rows)

        orders = []
        for row in rows:
            if not _is_fbm_eligible(row):
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            platform = _platform(row)
            route_state = _route_state(row)
            profile = profiles.get(key)
            shipment = shipments.get(key)
            shipment_state = shipment_confirmation_state(shipment) if shipment else "not_started"
            case = (
                provider_case_eligibility(shipment)
                if shipment
                else {"eligible": False, "reason": "shipment_not_started", "case_type": None}
            )
            mapping_review = getattr(shipment, "mapping_review", None) if shipment else None
            orders.append({
                "order": row,
                "platform": platform,
                "store_name": _store_name(row),
                "route_state": route_state,
                "shipping_mode": _marketplace_shipping_mode(row, platform, profile),
                "shipment": shipment,
                "shipment_state": shipment_state,
                "case": case,
                "profile": profile,
                "mapping_review": mapping_review,
            })

        counts = {
            "total": len(orders),
            "ready": sum(1 for item in orders if item["route_state"] == "Ready for FBM routing"),
            "tracking": sum(1 for item in orders if item["route_state"] == "Tracking recorded"),
            "dispatched": sum(1 for item in orders if item["route_state"] == "Dispatched"),
            "marketplace_shipping": sum(1 for item in orders if item["shipping_mode"]["marketplace_buy_shipping"]),
            "awaiting_acceptance": sum(1 for item in orders if item["shipment_state"] == "awaiting_carrier_acceptance"),
            "overdue": sum(1 for item in orders if item["shipment_state"] == "acceptance_overdue"),
            "mapping_review": sum(
                1 for item in orders
                if item["mapping_review"] and item["mapping_review"].status == "under_review"
            ),
        }

        html = render_template(
            "fbm.html",
            orders=orders,
            counts=counts,
            platform_filter=platform_filter,
            status_filter=status_filter,
        )
        return _expand_control(html, visible_limit=visible_limit, has_more=has_more)

    @login_required
    def persisted_fbm_shipping_options():
        order_ids = _selected_order_ids()
        if not order_ids:
            return jsonify({"success": False, "message": "Select at least one FBM order."}), 400

        rows = _selected_rows(order_ids)
        if not rows:
            return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404

        profiles = _profile_map(rows)
        lines_by_order = _order_lines_map(rows)
        mappings = _pack_mapping_by_sku(lines_by_order)
        result = []

        for row in rows:
            key = (int(row.store_id), str(row.marketplace_order_id))
            lines = lines_by_order.get(key) or [row]
            profile = profiles.get(key)
            platform = _platform(row)
            parcel = _persisted_parcel(lines, mappings)
            result.append({
                "id": row.id,
                "marketplace_order_id": row.marketplace_order_id,
                "platform": platform,
                "store_name": _store_name(row),
                "sku": getattr(row, "sku", None),
                "quantity": sum(max(1, int(getattr(line, "quantity", 1) or 1)) for line in lines),
                "postcode": getattr(row, "ship_to_postcode", None),
                "route_state": _route_state(row),
                "is_prime": profile.is_prime if profile else None,
                "prime_profile_error": None,
                "parcel": parcel,
                "providers": _shipping_provider_options(row, profile, None),
            })

        return jsonify({
            "success": True,
            "orders": result,
            "selected_count": len(result),
            "printing": {
                "mode": "qz_tray",
                "auto_print_after_purchase": True,
                "printer_preference_required": True,
                "fallback": "download_label",
            },
            "message": "Shipping routes prepared from persisted BT38 order facts.",
        })

    app.view_functions[page_endpoint] = bounded_fbm_page
    app.view_functions[shipping_endpoint] = persisted_fbm_shipping_options
    app._bt38_fbm_page_alignment_installed = True
    app.logger.info(
        "BT38 FBM read alignment installed: 15-order page and persisted Ready-to-Ship preparation"
    )
