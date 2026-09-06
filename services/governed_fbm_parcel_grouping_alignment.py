"""Wire DB-first parcel review and same-address consolidation into FBM.

No marketplace/provider read is performed by this alignment. Shipping Options
reads persisted order/profile/parcel facts only. Live provider confirmation stays
at the final label purchase/print boundary.
"""
from __future__ import annotations

from functools import wraps

from flask import jsonify, request
from flask_login import current_user, login_required

from extensions import db
from fbm_parcel_models import FBMParcelCombinationMapping, FBMShipmentOrderLink
from models import MarketplaceOrder
from services.fbm_order_mapper import parcel_from_db
from services.fbm_parcel_grouping import (
    canonical_order_rows,
    consolidation_eligibility,
    marketplace_order_identity,
    resolve_combined_parcel,
    same_address_candidates,
    save_combination_mapping,
)


def _parse_ids(raw) -> list[int]:
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw or "").split(",")
    result: list[int] = []
    for value in values:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in result:
            result.append(order_id)
        if len(result) >= 50:
            break
    return result


def _selected_rows(order_ids: list[int]) -> list[MarketplaceOrder]:
    if not order_ids:
        return []
    rows = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all()
    by_id = {row.id: row for row in rows}
    return canonical_order_rows([by_id[order_id] for order_id in order_ids if order_id in by_id])


def _serialize_candidate(row: MarketplaceOrder) -> dict:
    return {
        "id": row.id,
        "store_id": row.store_id,
        "marketplace_order_id": row.marketplace_order_id,
        "sku": getattr(row, "sku", None),
        "quantity": getattr(row, "quantity", None),
        "platform": str(getattr(getattr(row, "store", None), "platform", "") or ""),
    }


def _install_db_only_profile_read() -> None:
    """Normal FBM preparation may not wake Amazon just to rediscover DB truth."""
    import governed_fbm_routes as routes

    if getattr(routes, "_bt38_db_only_shipping_profile_patched", False):
        return

    original = routes._amazon_profile

    def db_first_amazon_profile(order, *, refresh=False):
        if refresh:
            return original(order, refresh=True)
        if routes._platform(order).strip().lower() != "amazon":
            return None, None
        profile = routes._profile_for(order)
        if profile is None:
            return None, "Amazon shipping profile is not yet persisted; final label confirmation can refresh this exact order."
        return profile, None

    routes._amazon_profile = db_first_amazon_profile
    routes._bt38_db_only_shipping_profile_patched = True


def _install_shipping_options_enrichment(app) -> None:
    endpoint = "governed_fbm.fbm_shipping_options"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_parcel_grouping_enriched", False):
        return

    @wraps(current)
    def aligned_shipping_options(*args, **kwargs):
        response = current(*args, **kwargs)
        status = None
        headers = None
        base = response
        if isinstance(response, tuple):
            base = response[0]
            if len(response) > 1:
                status = response[1]
            if len(response) > 2:
                headers = response[2]
        payload = base.get_json(silent=True) if hasattr(base, "get_json") else None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return response

        order_ids = [
            int(item.get("id"))
            for item in payload.get("orders") or []
            if isinstance(item, dict) and item.get("id") is not None
        ]
        rows = _selected_rows(order_ids)
        candidates = same_address_candidates(rows)
        rows_by_id = {row.id: row for row in rows}

        for item in payload.get("orders") or []:
            if not isinstance(item, dict):
                continue
            row = rows_by_id.get(item.get("id"))
            if row is None:
                continue
            identity = marketplace_order_identity(row)
            parcel = parcel_from_db(row).to_dict()
            matches = candidates.get(identity, []) if identity else []
            item["packing"] = {
                "parcel": parcel,
                "mapping_review_required": bool(parcel.get("mapping_review_required")),
                "same_address_candidates": [_serialize_candidate(candidate) for candidate in matches],
                "can_offer_combine": bool(matches),
                "provider_call_made": False,
            }

        payload["packing"] = {
            "db_only": True,
            "provider_call_made": False,
            "rule": "Live rate/provider confirmation occurs only at final label purchase/print confirmation.",
        }
        enriched = jsonify(payload)
        if status is None:
            return enriched
        if headers is None:
            return enriched, status
        return enriched, status, headers

    aligned_shipping_options._bt38_parcel_grouping_enriched = True
    app.view_functions[endpoint] = aligned_shipping_options


def install_governed_fbm_parcel_grouping_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_parcel_grouping_installed", False):
        return

    # BT38 already uses create-all/check-first schema creation. These two tables
    # are additive packing/link authorities only; no existing table is altered.
    with app.app_context():
        FBMParcelCombinationMapping.__table__.create(bind=db.engine, checkfirst=True)
        FBMShipmentOrderLink.__table__.create(bind=db.engine, checkfirst=True)

    _install_db_only_profile_read()
    _install_shipping_options_enrichment(app)

    if "bt38_fbm_packing_preview" not in app.view_functions:
        @app.post("/fbm/packing/preview", endpoint="bt38_fbm_packing_preview")
        @login_required
        def packing_preview():
            body = request.get_json(silent=True) or {}
            order_ids = _parse_ids(body.get("order_ids"))
            rows = _selected_rows(order_ids)
            if len(rows) < 1:
                return jsonify({"success": False, "message": "Select at least one FBM order."}), 400

            eligibility = consolidation_eligibility(rows) if len(rows) > 1 else {
                "eligible": True,
                "blockers": [],
                "order_count": 1,
                "same_address": True,
                "amazon_buy_shipping_compatible": True,
                "requires_user_confirmation": False,
            }
            parcel = resolve_combined_parcel(rows)
            return jsonify({
                "success": True,
                "orders": [_serialize_candidate(row) for row in rows],
                "consolidation": eligibility,
                "parcel": parcel,
                "provider_call_made": False,
            })

    if "bt38_fbm_packing_mapping_save" not in app.view_functions:
        @app.post("/fbm/packing/mapping", endpoint="bt38_fbm_packing_mapping_save")
        @login_required
        def packing_mapping_save():
            body = request.get_json(silent=True) or {}
            if body.get("confirm_mapping") != "SAVE_PACK_MAPPING":
                return jsonify({"success": False, "message": "Explicit SAVE_PACK_MAPPING confirmation is required."}), 400
            order_ids = _parse_ids(body.get("order_ids"))
            rows = _selected_rows(order_ids)
            if not rows:
                return jsonify({"success": False, "message": "Select at least one FBM order."}), 400
            if len(rows) > 1:
                eligibility = consolidation_eligibility(rows)
                if not eligibility["eligible"]:
                    return jsonify({
                        "success": False,
                        "message": "Selected orders cannot be saved as one parcel.",
                        "blockers": eligibility["blockers"],
                    }), 409
            try:
                mapping = save_combination_mapping(
                    rows,
                    weight_kg=body.get("weight_kg"),
                    length_cm=body.get("length_cm"),
                    width_cm=body.get("width_cm"),
                    height_cm=body.get("height_cm"),
                    verified_by=str(getattr(current_user, "username", "") or getattr(current_user, "email", "") or "user"),
                )
            except ValueError as exc:
                return jsonify({"success": False, "message": str(exc)}), 422
            return jsonify({
                "success": True,
                "mapping_id": mapping.id,
                "combination_key": mapping.combination_key,
                "items": mapping.items,
                "total_units": mapping.total_units,
                "weight_kg": mapping.weight_kg,
                "length_cm": mapping.length_cm,
                "width_cm": mapping.width_cm,
                "height_cm": mapping.height_cm,
                "verification_status": mapping.verification_status,
                "provider_call_made": False,
                "message": "Packing mapping saved. Future matching SKU/quantity combinations will reuse it.",
            })

    app._bt38_fbm_parcel_grouping_installed = True
