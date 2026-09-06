"""Wire DB-first parcel review and same-address consolidation into FBM.

No marketplace/provider read is performed by ordinary Shipping Options or parcel
review. Live provider confirmation stays at the final label purchase/print
boundary. Shared-parcel links reuse one existing FBMShipment and never buy a
second label for a secondary linked order.
"""
from __future__ import annotations

from functools import wraps

from flask import jsonify, request
from flask_login import current_user, login_required

from extensions import db
from fbm_models import FBMShipment
from fbm_parcel_models import FBMParcelCombinationMapping, FBMShipmentOrderLink
from models import MarketplaceOrder
from services.fbm_order_mapper import parcel_from_db
from services.fbm_parcel_grouping import (
    canonical_order_rows,
    consolidation_eligibility,
    link_orders_to_existing_shipment,
    linked_physical_shipment_for_order,
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
            existing_shared = linked_physical_shipment_for_order(row)
            item["packing"] = {
                "parcel": parcel,
                "mapping_review_required": bool(parcel.get("mapping_review_required")),
                "same_address_candidates": [_serialize_candidate(candidate) for candidate in matches],
                "can_offer_combine": bool(matches),
                "existing_shared_shipment_id": existing_shared.shipment_id if existing_shared else None,
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


def _install_secondary_purchase_guards(app) -> None:
    """Prevent a linked secondary order from buying another original label."""
    guarded = {
        "governed_fbm.amazon_purchase": "amazon",
        "governed_fbm.packlink_create_draft": "packlink",
        "governed_fbm.manual_dispatch": "manual",
    }
    for endpoint, kind in guarded.items():
        current = app.view_functions.get(endpoint)
        if current is None or getattr(current, "_bt38_shared_parcel_purchase_guarded", False):
            continue

        @wraps(current)
        def guarded_purchase(*args, __current=current, __kind=kind, **kwargs):
            order_id = kwargs.get("order_id")
            try:
                order_id = int(order_id)
            except (TypeError, ValueError):
                return __current(*args, **kwargs)
            order = db.session.get(MarketplaceOrder, order_id)
            if order is None:
                return __current(*args, **kwargs)

            # A genuine Packlink return/replacement is an additional governed
            # shipment, not a duplicate original shared-parcel purchase.
            if __kind == "packlink":
                body = request.get_json(silent=True) or {}
                if str(body.get("shipment_purpose") or "").strip().lower() in {"return", "replacement"}:
                    return __current(*args, **kwargs)

            existing = linked_physical_shipment_for_order(order)
            if existing is not None and not bool(existing.is_primary):
                return jsonify({
                    "success": False,
                    "message": "This order is already packed inside an existing shared physical shipment. BT38 will not buy a second original label.",
                    "shared_shipment_id": existing.shipment_id,
                    "duplicate_postage_blocked": True,
                }), 409
            return __current(*args, **kwargs)

        guarded_purchase._bt38_shared_parcel_purchase_guarded = True
        app.view_functions[endpoint] = guarded_purchase


def _release_already_confirmed_shared_shipment(shipment: FBMShipment) -> dict | None:
    """After late linking, release secondary marketplace confirmations only.

    This never buys postage. It matters for a manual/external label where the
    primary order may have been confirmed before the user attached the remaining
    same-address orders. If mapping is still under review, the existing mapping
    verification release path will handle the linked orders later.
    """
    if not shipment.marketplace_confirmed_at or not str(shipment.tracking_number or "").strip():
        return None
    if str(shipment.provider or "").strip().lower() == "amazon_buy_shipping":
        return None
    review = getattr(shipment, "mapping_review", None)
    mapping = getattr(review, "mapping", None) if review is not None else None
    if mapping is None or str(getattr(mapping, "verification_status", "") or "") != "verified":
        return None
    from services.fbm_shared_shipment_confirmation import confirm_linked_external_orders
    return confirm_linked_external_orders(shipment=shipment, mapping=mapping)


def install_governed_fbm_parcel_grouping_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_parcel_grouping_installed", False):
        return

    with app.app_context():
        FBMParcelCombinationMapping.__table__.create(bind=db.engine, checkfirst=True)
        FBMShipmentOrderLink.__table__.create(bind=db.engine, checkfirst=True)

    _install_db_only_profile_read()
    _install_shipping_options_enrichment(app)
    _install_secondary_purchase_guards(app)

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

    if "bt38_fbm_packing_link_shipment" not in app.view_functions:
        @app.post("/fbm/packing/link-shipment", endpoint="bt38_fbm_packing_link_shipment")
        @login_required
        def packing_link_shipment():
            """Attach explicitly confirmed same-address orders to one shipment."""
            body = request.get_json(silent=True) or {}
            if body.get("confirm_pack_together") != "PACK_TOGETHER":
                return jsonify({"success": False, "message": "Explicit PACK_TOGETHER confirmation is required."}), 400

            order_ids = _parse_ids(body.get("order_ids"))
            rows = _selected_rows(order_ids)
            if len(rows) < 2:
                return jsonify({"success": False, "message": "Select at least two FBM orders to pack together."}), 400

            try:
                shipment_id = int(body.get("shipment_id"))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "Valid shipment_id is required."}), 400

            shipment = db.session.get(FBMShipment, shipment_id)
            if shipment is None:
                return jsonify({"success": False, "message": "Physical FBM shipment not found."}), 404

            identities = {
                marketplace_order_identity(row)
                for row in rows
                if marketplace_order_identity(row) is not None
            }
            shipment_identity = (int(shipment.store_id), str(shipment.marketplace_order_id or "").strip())
            if shipment_identity not in identities:
                return jsonify({
                    "success": False,
                    "message": "The physical shipment must belong to one of the selected marketplace orders.",
                }), 409

            # Amazon Buy Shipping is marketplace-native to one exact Amazon order.
            # It can never become the physical authority for several separate
            # marketplace orders, even if browser controls are bypassed.
            if str(shipment.provider or "").strip().lower() == "amazon_buy_shipping":
                return jsonify({
                    "success": False,
                    "message": "Amazon Buy Shipping labels belong to one exact Amazon order and cannot be shared across packed-together orders. Use an eligible external/manual shipment for one-box consolidation.",
                    "amazon_native_shared_parcel_blocked": True,
                }), 409

            try:
                links = link_orders_to_existing_shipment(shipment, rows)
            except ValueError as exc:
                return jsonify({"success": False, "message": str(exc)}), 409

            parcel = resolve_combined_parcel(rows, record_usage=True)
            linked_confirmation = _release_already_confirmed_shared_shipment(shipment)
            return jsonify({
                "success": True,
                "shipment_id": shipment.id,
                "linked_orders": [
                    {
                        "store_id": link.store_id,
                        "marketplace_order_id": link.marketplace_order_id,
                        "is_primary": bool(link.is_primary),
                    }
                    for link in links
                ],
                "linked_marketplace_confirmation": linked_confirmation,
                "parcel": parcel,
                "provider_call_made": False,
                "duplicate_postage_blocked": True,
                "message": "Orders linked to one existing physical shipment. Marketplace orders remain separate and secondary original-label purchase is blocked.",
            })

    app._bt38_fbm_parcel_grouping_installed = True
