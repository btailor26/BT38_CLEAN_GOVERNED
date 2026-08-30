"""Bound the existing FBM page and shipping-options reads without a second workflow.

This alignment keeps the registered /fbm workspace, existing template, shipping
handlers and persisted authorities unchanged. An ordinary page refresh hydrates
only the latest 15 distinct FBM orders, while Shipping options reads only the
clicked/selected order IDs from BT38 persistence. Live marketplace/provider
reads stay deferred to the explicit provider actions that actually require them.
Older records remain user-expanded in 15-order increments.

For eBay, the workspace exposes the existing Seller Hub order handoff as a
clickable browser action while keeping native eBay label buying capability-gated.
Connected in-BT38 providers and governed manual dispatch remain unchanged.

No marketplace/provider calls, inventory writes, reconciliation, scheduler or
MCF path is introduced here.
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
_FBM_DISCOVERY_MULTIPLIER = 4
_FBM_MAX_SELECTED = 50


def _requested_limit() -> int:
    try:
        requested = int(request.args.get("limit") or _FBM_PAGE_SIZE)
    except (TypeError, ValueError):
        requested = _FBM_PAGE_SIZE
    requested = max(_FBM_PAGE_SIZE, requested)
    return min(_FBM_MAX_EXPANDED, ((requested + _FBM_PAGE_SIZE - 1) // _FBM_PAGE_SIZE) * _FBM_PAGE_SIZE)


def _selected_order_ids() -> list[int]:
    """Return only the explicit Shipping-options selection, capped defensively."""
    raw_ids = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for value in raw_ids.split(","):
        try:
            order_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in order_ids:
            order_ids.append(order_id)
        if len(order_ids) >= _FBM_MAX_SELECTED:
            break
    return order_ids


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


def _workspace_fbm_eligible(row: MarketplaceOrder, profile: FBMOrderProfile | None = None) -> bool:
    """Require positive seller-fulfilled truth before Amazon enters the FBM desk.

    The shared persisted fulfillment_type remains the first guard for every
    marketplace. Amazon also has a persisted FBMOrderProfile populated from the
    marketplace FulfillmentChannel. That marketplace fact wins when available,
    so a stale/legacy MarketplaceOrder row can never expose an AFN/FBA order on
    this page or through Shipping options.
    """
    if not _is_fbm_eligible(row):
        return False

    if _platform(row).strip().lower() != "amazon":
        return True

    fulfillment = str(getattr(row, "fulfillment_type", "") or "").strip().upper()
    profile_channel = str(getattr(profile, "fulfillment_channel", "") or "").strip().upper() if profile else ""

    if profile_channel in {"AFN", "FBA", "MCF"}:
        return False
    if profile_channel in {"MFN", "FBM"}:
        return True
    return fulfillment in {"MFN", "FBM"}


def _latest_distinct_fbm_rows(limit: int) -> tuple[list[MarketplaceOrder], bool]:
    """Read the newest persisted FBM rows from a bounded candidate window.

    Marketplace events can leave more than one persisted row for an order. The
    previous read grouped the complete eligible history on every page request.
    This keeps the same latest-row authority but first limits discovery to the
    newest candidate rows, then de-duplicates that small set in process.
    """
    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )

    query = db.session.query(MarketplaceOrder).filter(*eligible)

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

    candidate_limit = min(_FBM_MAX_EXPANDED * _FBM_DISCOVERY_MULTIPLIER, max(limit + 1, (limit + 1) * _FBM_DISCOVERY_MULTIPLIER))
    candidates = (
        query
        .options(
            joinedload(MarketplaceOrder.store),
            joinedload(MarketplaceOrder.warehouse_stock),
        )
        .order_by(MarketplaceOrder.id.desc())
        .limit(candidate_limit)
        .all()
    )

    rows: list[MarketplaceOrder] = []
    seen: set[tuple[int, str]] = set()
    for row in candidates:
        if row.store_id is None or not row.marketplace_order_id:
            continue
        key = (int(row.store_id), str(row.marketplace_order_id))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= limit + 1:
            break

    rows.sort(key=lambda row: (row.created_at is not None, row.created_at, row.id), reverse=True)
    has_more = len(rows) > limit or len(candidates) == candidate_limit
    return rows[:limit], has_more


def _workspace_shipping_mode(row: MarketplaceOrder, platform: str, profile: FBMOrderProfile | None) -> dict:
    """Expose only shipping routes that are executable from the BT38 workspace."""
    mode = dict(_marketplace_shipping_mode(row, platform, profile))
    if platform.strip().lower() == "ebay":
        mode.update({
            "recommended": "Packlink / connected carrier",
            "marketplace_buy_shipping": False,
            "external_provider": True,
            "manual": True,
            "prime_locked": False,
            "profile_known": True,
            "reason": "Use an in-BT38 connected provider or governed manual dispatch. eBay Shipping opens the exact order in Seller Hub; BT38 does not buy native eBay labels through an API.",
        })
    return mode


def _workspace_provider_options(row: MarketplaceOrder, profile: FBMOrderProfile | None) -> list[dict]:
    """Keep eBay Seller Hub handoff clickable without claiming native label API support."""
    options = [dict(option) for option in _shipping_provider_options(row, profile, None)]
    if _platform(row).strip().lower() == "ebay":
        for option in options:
            if str(option.get("provider") or "") != "ebay_shipping":
                continue
            option.update({
                "available": True,
                "recommended": False,
                "label_formats": [],
                "auto_print_supported": False,
                "requires_terms_acceptance": False,
                "message": "Open this exact order in eBay Seller Hub to check or buy marketplace postage. BT38 does not purchase native eBay labels through an API.",
            })
    return options


def _neutralise_legacy_ebay_handoff(html: str) -> str:
    """Disable the obsolete DOM observer; the template owns the eBay handoff."""
    marker = 'id="fbmShippingOrders"'
    if marker not in html:
        return html
    return html.replace(
        marker,
        'id="fbmShippingOrders" data-ebay-shipping-handoff-installed="1"',
        1,
    )


def _positive_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _persisted_pack_mapping_parcel(row: MarketplaceOrder) -> dict:
    """Rehydrate the parcel defaults already persisted by the FBM order mapper.

    The explicit provider action remains the writer. Shipping-options only reads
    the existing one-unit ProductPackMapping for the selected SKU, avoiding the
    broader order/warehouse hydration that previously made the eBay modal slow.
    """
    try:
        quantity = max(1, int(getattr(row, "quantity", 1) or 1))
    except (TypeError, ValueError):
        quantity = 1
    sku = str(getattr(row, "sku", None) or "").strip()
    if quantity != 1 or not sku:
        return {
            "weight_kg": None,
            "length_cm": None,
            "width_cm": None,
            "height_cm": None,
            "source": "selected_row_deferred_parcel",
            "complete": False,
        }

    mapping = (
        db.session.query(ProductPackMapping)
        .filter_by(single_sku=sku, is_active=True)
        .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
        .first()
    )
    if mapping is None:
        return {
            "weight_kg": None,
            "length_cm": None,
            "width_cm": None,
            "height_cm": None,
            "source": "selected_row_deferred_parcel",
            "complete": False,
        }

    try:
        units = int(getattr(mapping, "units_per_carton", None) or 1)
    except (TypeError, ValueError):
        units = 1
    if units != 1:
        return {
            "weight_kg": None,
            "length_cm": None,
            "width_cm": None,
            "height_cm": None,
            "source": "selected_row_deferred_parcel",
            "complete": False,
        }

    parcel = {
        "weight_kg": _positive_float(getattr(mapping, "carton_weight_kg", None)),
        "length_cm": _positive_float(getattr(mapping, "carton_length_cm", None)),
        "width_cm": _positive_float(getattr(mapping, "carton_width_cm", None)),
        "height_cm": _positive_float(getattr(mapping, "carton_height_cm", None)),
        "source": "pack_mapping",
    }
    parcel["complete"] = all(parcel[name] is not None for name in ("weight_kg", "length_cm", "width_cm", "height_cm"))
    return parcel


def _selected_row_parcel(row: MarketplaceOrder) -> dict:
    """Return lightweight persisted parcel facts for the selected DB row."""
    saved = _persisted_pack_mapping_parcel(row)
    if any(saved.get(name) for name in ("weight_kg", "length_cm", "width_cm", "height_cm")):
        return saved

    try:
        quantity = max(1, int(getattr(row, "quantity", 1) or 1))
    except (TypeError, ValueError):
        quantity = 1
    warehouse = getattr(row, "warehouse_stock", None)
    try:
        unit_weight = float(getattr(warehouse, "product_weight_kg", 0) or 0) if warehouse is not None else 0.0
    except (TypeError, ValueError):
        unit_weight = 0.0
    weight = unit_weight * quantity if unit_weight > 0 else None
    return {
        "weight_kg": weight,
        "length_cm": None,
        "width_cm": None,
        "height_cm": None,
        "source": "selected_row_persisted_weight" if weight else "selected_row_missing_parcel",
        "complete": False,
    }


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
        f'<div class="d-flex gap-2">{"".join(actions)}</div>'
        '</div>'
    )
    marker = "</tbody></table></div>\n</div>"
    if marker not in html:
        return html
    return html.replace(marker, f"</tbody></table></div>\n{control}\n</div>", 1)


def install_governed_fbm_page_alignment(app) -> None:
    """Bound the existing FBM page and Shipping-options read endpoints only."""
    if getattr(app, "_bt38_fbm_page_alignment_installed", False):
        return

    page_endpoint = "governed_fbm.fbm_page"
    shipping_options_endpoint = "governed_fbm.fbm_shipping_options"
    if page_endpoint not in app.view_functions:
        raise RuntimeError("governed FBM page endpoint is not registered")
    if shipping_options_endpoint not in app.view_functions:
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
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key)
            if not _workspace_fbm_eligible(row, profile):
                continue
            platform = _platform(row)
            route_state = _route_state(row)
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
                "shipping_mode": _workspace_shipping_mode(row, platform, profile),
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
        html = _neutralise_legacy_ebay_handoff(html)
        return _expand_control(html, visible_limit=visible_limit, has_more=has_more)

    @login_required
    def bounded_shipping_options():
        """Open Shipping options from persisted facts for only selected orders."""
        order_ids = _selected_order_ids()
        if not order_ids:
            return jsonify({"success": False, "message": "Select at least one FBM order."}), 400

        rows = (
            db.session.query(MarketplaceOrder)
            .filter(MarketplaceOrder.id.in_(order_ids))
            .options(joinedload(MarketplaceOrder.store))
            .all()
        )
        amazon_rows = [
            row for row in rows
            if _platform(row).strip().lower() == "amazon"
        ]
        profiles = _profile_map(amazon_rows)
        by_id = {}
        for row in rows:
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
            if _workspace_fbm_eligible(row, profile):
                by_id[row.id] = row
        result = []

        for order_id in order_ids:
            row = by_id.get(order_id)
            if row is None:
                continue
            platform = _platform(row).strip().lower()
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if platform == "amazon" else None
            try:
                quantity = max(1, int(getattr(row, "quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            result.append({
                "id": row.id,
                "marketplace_order_id": row.marketplace_order_id,
                "platform": _platform(row),
                "store_name": _store_name(row),
                "sku": getattr(row, "sku", None),
                "quantity": quantity,
                "postcode": getattr(row, "ship_to_postcode", None),
                "route_state": _route_state(row),
                "is_prime": profile.is_prime if profile else None,
                "prime_profile_error": None,
                "parcel": _selected_row_parcel(row),
                "providers": _workspace_provider_options(row, profile),
            })

        if not result:
            return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404

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
            "message": "Shipping routes and selected-row persisted defaults prepared. Complete provider reads remain deferred until an explicit shipping action.",
        })

    app.view_functions[page_endpoint] = bounded_fbm_page
    app.view_functions[shipping_options_endpoint] = bounded_shipping_options
    app._bt38_fbm_page_alignment_installed = True
    app.logger.info(
        "BT38 FBM alignment installed: bounded page discovery, selected-row Shipping options DB read, 15-order expansion and eBay Seller Hub handoff"
    )
