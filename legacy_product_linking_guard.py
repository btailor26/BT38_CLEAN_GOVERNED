from __future__ import annotations

from flask import jsonify, request


LEGACY_PRODUCT_LINKING_PATHS = {
    "/governed/groups/unlink-disabled",
}

LEGACY_GOVERNED_ACTIONS = {
    "link-listing-to-warehouse",
    "unlink-listing",
    "product-linking-link",
}


def block_legacy_product_linking_request():
    """Fail closed before any retired Product Linking writer can execute."""
    path = (request.path or "").rstrip("/")

    is_disabled_group_unlink = (
        path.startswith("/governed/groups/")
        and path.endswith("/unlink-disabled")
    )

    action = ""
    governed_actions_prefix = "/governed/actions/"
    if path.startswith(governed_actions_prefix):
        action = path[len(governed_actions_prefix):].strip("/")

    if not is_disabled_group_unlink and action not in LEGACY_GOVERNED_ACTIONS:
        return None

    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": "legacy_product_linking_disabled",
        "message": (
            "Legacy Product Linking writes are disabled. "
            "Use the governed group relationship routes."
        ),
        "full_page_refresh": False,
        "full_dataset_refresh": False,
        "cache_clear_required": False,
    }), 409
