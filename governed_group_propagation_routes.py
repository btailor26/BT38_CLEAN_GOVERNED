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


def run_governed_group_propagation(
    group_id: int,
    *,
    payload=None,
):
    """Compatibility shortcut into the single governed group-push service.

    Warehouse remains quantity authority. Product Linking/group push routes are
    shortcuts only and must not implement marketplace execution themselves.
    """
    from services.governed_push_execution import push_group_listings

    body = dict(payload or {})
    authority_warehouse_stock_id = body.get("warehouse_stock_id")

    result = push_group_listings(
        group_id=int(group_id),
        actor=_actor(),
        source="governed_group_propagation",
        authority_warehouse_stock_id=authority_warehouse_stock_id,
        dry_run=bool(body.get("dry_run", False)),
    )

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
