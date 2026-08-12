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


def _apply_exact_fba_warehouse_handoff(
    *,
    group_id: int,
    warehouse_stock_id: int,
):
    """Move confirmed exact FBA truth into Warehouse before group propagation.

    This is deliberately available only to the exact internal Amazon webhook
    handoff. Generic FBA hydration remains read-only with respect to Warehouse,
    and browser/request quantities never become Warehouse authority.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    stock = db.session.get(WarehouseStock, int(warehouse_stock_id))
    if stock is None:
        return False, "warehouse_stock_not_found"

    listing = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.warehouse_stock_id == int(warehouse_stock_id),
            MarketplaceListing.master_product_group_id == int(group_id),
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .order_by(MarketplaceListing.id.desc())
        .first()
    )
    if listing is None:
        return False, "grouped_fba_listing_not_found"

    platform = str(
        getattr(getattr(listing, "store", None), "platform", "") or ""
    ).strip().lower()
    channel = str(
        getattr(listing, "normalized_amazon_fulfillment_channel", None)
        or getattr(listing, "amazon_fulfillment_channel", None)
        or ""
    ).strip().upper()
    explicit_fba = bool(getattr(listing, "is_fba", False))
    if "amazon" not in platform or not (
        explicit_fba or channel in {"AFN", "FBA", "AMAZON"}
    ):
        return False, "authority_listing_is_not_fba"

    confirmed_quantity = getattr(listing, "last_marketplace_qty", None)
    if confirmed_quantity is None:
        return False, "confirmed_fba_quantity_missing"

    try:
        confirmed_quantity = max(0, int(confirmed_quantity))
    except (TypeError, ValueError):
        return False, "confirmed_fba_quantity_invalid"

    reserved = int(getattr(stock, "reserved_quantity", 0) or 0)
    allocated = int(getattr(stock, "allocated_quantity", 0) or 0)
    stock.available_quantity = int(
        confirmed_quantity + reserved + allocated
    )
    db.session.commit()
    return True, confirmed_quantity


def run_governed_group_propagation(
    group_id: int,
    *,
    payload=None,
):
    """Compatibility shortcut into the single governed group-push service.

    Warehouse remains quantity authority. Product Linking/group push routes are
    shortcuts only and must not implement marketplace execution themselves.
    The exact internal FBA webhook handoff first transfers confirmed Amazon FBA
    truth into its linked Warehouse authority row, then uses the same group path.
    """
    from services.governed_push_execution import push_group_listings

    body = dict(payload or {})
    authority_warehouse_stock_id = body.get("warehouse_stock_id")

    # Never trust arbitrary request source strings. Only the exact internal
    # handoff marker may change Warehouse authority or classify the push as an
    # automatic webhook action. HTTP Product Linking remains a manual shortcut.
    requested_source = str(body.get("source") or "").strip().lower()
    exact_fba_handoff = (
        requested_source == "amazon_webhook_exact_fba_handoff"
        and authority_warehouse_stock_id not in (None, "")
    )
    source = "governed_group_propagation"
    fba_handoff = None

    if exact_fba_handoff:
        try:
            authority_stock_id = int(authority_warehouse_stock_id)
        except (TypeError, ValueError):
            authority_stock_id = None

        if authority_stock_id is not None:
            applied, detail = _apply_exact_fba_warehouse_handoff(
                group_id=int(group_id),
                warehouse_stock_id=authority_stock_id,
            )
            fba_handoff = {
                "applied": bool(applied),
                "detail": detail,
                "warehouse_stock_id": authority_stock_id,
            }
            if not applied:
                return jsonify({
                    "success": False,
                    "ok": False,
                    "governed": True,
                    "execution_blocked": True,
                    "reason": "exact_fba_warehouse_handoff_failed",
                    "group_id": int(group_id),
                    "fba_handoff": fba_handoff,
                }), 409
            source = "webhook_amazon_exact_fba_handoff"

    result = push_group_listings(
        group_id=int(group_id),
        actor=_actor(),
        source=source,
        authority_warehouse_stock_id=authority_warehouse_stock_id,
        dry_run=bool(body.get("dry_run", False)),
    )

    if fba_handoff is not None:
        result["fba_warehouse_handoff"] = fba_handoff
        result["automatic_webhook_push"] = True

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