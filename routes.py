"""BT38 route compatibility module for shutdown proof.

Retired marketplace execution and setup surfaces are blocked centrally by
shutdown_http_guard before handlers can run. This module intentionally exposes a
minimal blueprint only; old direct marketplace route bodies are not present in
shutdown mode.
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("main", __name__)


@bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    """Non-marketplace status endpoint for shutdown proof diagnostics."""
    return jsonify(
        {
            "success": True,
            "ok": True,
            "shutdown_mode": True,
            "old_marketplace_routes_present": False,
        }
    )


@bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    """Manual governed SKU dry-run trigger.

    This backend-only route does not call marketplace services or adapters
    directly. It forwards the request to the single governed execution entry
    point with dry_run=True. Runtime gate remains force-closed, so results stay
    blocked/dry-run only.
    """
    from governed_execution import submit_governed_marketplace_action

    payload = request.get_json(silent=True) or {}
    governed_payload = dict(payload)
    governed_payload.setdefault("action", "push_inventory")

    result = submit_governed_marketplace_action(
        governed_payload,
        actor=request.headers.get("X-Actor", "manual-governed-dry-run"),
        approval={"approved": True, "source": "manual_sku_dry_run_route"},
        dry_run=True,
    )
    return jsonify(result), 200
