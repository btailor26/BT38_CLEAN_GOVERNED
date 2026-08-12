from __future__ import annotations

from flask import Blueprint, jsonify, request
try:
    from flask_login import current_user
except Exception:
    current_user = None


governed_group_propagation_bp = Blueprint("governed_group_propagation", __name__)


# Disabled duplicate destructive unlink route.
# Single unlink authority lives in governed_group_routes.py.
# This blueprint owns the compatibility propagation shortcut only.
@governed_group_propagation_bp.post(
    "/governed/groups/<int:group_id>/unlink-disabled"
)
def governed_group_unlink_listing_disabled(group_id: int):
    """Retired duplicate relationship writer."""
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": "legacy_product_linking_disabled",
        "group_id": group_id,
        "message": (
            "Duplicate unlink route is retired. "
            "Use POST /governed/groups/<group_id>/unlink."
        ),
        "full_page_refresh": False,
        "full_dataset_refresh": False,
    }), 409


def _apply_fba_group_authority(*, group_id: int):
    """Copy the current read-only FBA truth into its Warehouse row.

    FBA-led groups already use Amazon as quantity authority. This helper does
    not push to a marketplace and does not change relationships; it only
    restores the established FBA -> Warehouse handoff before the one shared
    group-push service runs. Non-FBA groups are left untouched.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    grouped_listings = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.master_product_group_id == int(group_id),
            MarketplaceListing.is_active == True,  # noqa: E712
            MarketplaceListing.warehouse_stock_id.isnot(None),
        )
        .order_by(MarketplaceListing.id)
        .all()
    )

    fba_listing = None
    for listing in grouped_listings:
        platform = str(
            getattr(getattr(listing, "store", None), "platform", "") or ""
        ).strip().lower()
        channel = str(
            getattr(listing, "normalized_amazon_fulfillment_channel", None)
            or getattr(listing, "amazon_fulfillment_channel", None)
            or ""
        ).strip().upper()
        explicit_fba = bool(getattr(listing, "is_fba", False))
        is_fbm = channel in {"MFN", "FBM", "MERCHANT"}
        if "amazon" in platform and (explicit_fba or not is_fbm):
            fba_listing = listing
            break

    if fba_listing is None:
        return {
            "fba_led": False,
            "applied": False,
            "reason": "group_has_no_fba_authority",
        }

    confirmed_quantity = getattr(fba_listing, "last_marketplace_qty", None)
    if confirmed_quantity is None:
        return {
            "fba_led": True,
            "applied": False,
            "reason": "confirmed_fba_quantity_missing",
            "listing_id": int(fba_listing.id),
            "warehouse_stock_id": int(fba_listing.warehouse_stock_id),
        }

    try:
        confirmed_quantity = max(0, int(confirmed_quantity))
    except (TypeError, ValueError):
        return {
            "fba_led": True,
            "applied": False,
            "reason": "confirmed_fba_quantity_invalid",
            "listing_id": int(fba_listing.id),
            "warehouse_stock_id": int(fba_listing.warehouse_stock_id),
        }

    stock = db.session.get(
        WarehouseStock,
        int(fba_listing.warehouse_stock_id),
    )
    if stock is None:
        return {
            "fba_led": True,
            "applied": False,
            "reason": "fba_warehouse_stock_not_found",
            "listing_id": int(fba_listing.id),
            "warehouse_stock_id": int(fba_listing.warehouse_stock_id),
        }

    reserved = int(getattr(stock, "reserved_quantity", 0) or 0)
    allocated = int(getattr(stock, "allocated_quantity", 0) or 0)
    stock.available_quantity = int(confirmed_quantity + reserved + allocated)
    db.session.commit()

    return {
        "fba_led": True,
        "applied": True,
        "listing_id": int(fba_listing.id),
        "warehouse_stock_id": int(stock.id),
        "quantity": confirmed_quantity,
    }


def run_governed_group_propagation(
    group_id: int,
    *,
    payload=None,
):
    """Compatibility shortcut into the single governed group-push service.

    Product Linking/group push remains a shortcut only. For an FBA-led group,
    the already-confirmed Amazon quantity is handed into Warehouse first; the
    existing shared group service then aligns Warehouse members and pushes only
    writable marketplace listings. FBA itself remains read-only.
    """
    from flask import has_request_context
    from services.governed_push_execution import push_group_listings

    body = dict(payload or {})
    requested_authority_stock_id = body.get("warehouse_stock_id")

    fba_authority = _apply_fba_group_authority(group_id=int(group_id))
    if fba_authority.get("fba_led") and not fba_authority.get("applied"):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "execution_blocked": True,
            "reason": "fba_group_authority_handoff_failed",
            "group_id": int(group_id),
            "fba_authority": fba_authority,
        }), 409

    authority_warehouse_stock_id = (
        fba_authority.get("warehouse_stock_id")
        if fba_authority.get("fba_led")
        else requested_authority_stock_id
    )

    requested_source = str(body.get("source") or "").strip().lower()
    internal_exact_fba_handoff = (
        not has_request_context()
        and requested_source == "amazon_webhook_exact_fba_handoff"
    )
    source = (
        "webhook_amazon_exact_fba_handoff"
        if internal_exact_fba_handoff
        else "governed_group_propagation"
    )

    result = push_group_listings(
        group_id=int(group_id),
        actor=_actor(),
        source=source,
        authority_warehouse_stock_id=authority_warehouse_stock_id,
        dry_run=bool(body.get("dry_run", False)),
    )

    if fba_authority.get("fba_led"):
        result["fba_group_authority"] = fba_authority
        result["fba_read_only_authority_used"] = True

    status = 200 if (
        result.get("ok")
        or result.get("success")
        or result.get("execution_blocked")
    ) else 400
    return jsonify(result), status


@governed_group_propagation_bp.post(
    "/governed/groups/<int:group_id>/propagate-quantity"
)
def governed_group_propagate_quantity(group_id: int):
    """Thin HTTP adapter into the shared Warehouse group process."""
    return run_governed_group_propagation(
        group_id,
        payload=request.get_json(silent=True) or {},
    )


def _actor() -> str:
    from flask import has_request_context

    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass

    if not has_request_context():
        return "governed-group-propagation"

    return request.headers.get(
        "X-Actor",
        "governed-group-propagation",
    )
