from __future__ import annotations

from flask import Blueprint, jsonify, request
try:
    from flask_login import current_user
except Exception:
    current_user = None


governed_group_propagation_bp = Blueprint("governed_group_propagation", __name__)


# Disabled duplicate destructive unlink route.
# Single unlink authority lives in governed_group_routes.py.
# This blueprint owns only the HTTP shortcut into the shared group push service.

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


def run_governed_group_propagation(
    group_id: int,
    *,
    payload=None,
):
    """Thin adapter into the single governed group push service.

    Product Linking and Warehouse shortcuts report relationship identity only.
    Quantity authority is resolved inside services.governed_push_execution from
    the validated Warehouse row and then shared across the whole current group.
    """
    from services.governed_push_execution import push_group_listings

    body = dict(payload or {})
    requested_warehouse_stock_id = body.get("warehouse_stock_id")
    dry_run = bool(body.get("dry_run", False))

    result = push_group_listings(
        group_id=int(group_id),
        actor=_actor(),
        source=str(
            body.get("source")
            or "product_linking_warehouse_shortcut"
        ),
        actor_user=(
            current_user
            if _authenticated_user_available()
            else None
        ),
        authority_warehouse_stock_id=requested_warehouse_stock_id,
        dry_run=dry_run,
    )

    status = 200 if bool(result.get("ok") or result.get("success")) else 409
    return jsonify(result), status


@governed_group_propagation_bp.post(
    "/governed/groups/<int:group_id>/propagate-quantity"
)
def governed_group_propagate_quantity(group_id: int):
    """HTTP shortcut into the single governed group push service."""
    return run_governed_group_propagation(
        group_id,
        payload=request.get_json(silent=True) or {},
    )


def _authenticated_user_available() -> bool:
    try:
        return bool(current_user and current_user.is_authenticated)
    except Exception:
        return False


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
