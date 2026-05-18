from __future__ import annotations

from flask import Blueprint, jsonify, request

governed_bp = Blueprint("governed", __name__)


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({
        "success": True,
        "ok": True,
        "shutdown_mode": True,
        "old_marketplace_routes_present": False,
    })


@governed_bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    """Manual governed SKU dry-run trigger; never performs live execution."""
    from governed_execution import submit_governed_marketplace_action

    governed_payload = dict(request.get_json(silent=True) or {})
    governed_payload.setdefault("action", "push_inventory")

    result = submit_governed_marketplace_action(
        governed_payload,
        actor=request.headers.get("X-Actor", "manual-governed-dry-run"),
        approval={"approved": True, "source": "manual_sku_dry_run_route"},
        dry_run=True,
    )
    return jsonify(result), 200
