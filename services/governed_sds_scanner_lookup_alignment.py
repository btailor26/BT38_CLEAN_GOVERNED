"""Read-only SDS parcel resolver for the existing BT38 mobile scanner."""
from __future__ import annotations

import re

from flask import jsonify
from flask_login import login_required

from fbm_models import FBMShipment


SDS_REFERENCE = re.compile(r"^SDS-(\d{10})$")


def _next_event(status: str | None) -> str | None:
    status = str(status or "").strip().lower()
    if status == "awaiting_seller_handover":
        return "handover"
    if status == "seller_handover_confirmed":
        return "in_transit"
    if status == "in_transit":
        return "delivered"
    return None


def install_governed_sds_scanner_lookup_alignment(app) -> None:
    if getattr(app, "_bt38_sds_scanner_lookup_alignment_installed", False):
        return

    @app.get("/api/mobile/sds/<reference>", endpoint="bt38_mobile_sds_lookup")
    @login_required
    def mobile_sds_lookup(reference: str):
        normalised = str(reference or "").strip().upper()
        match = SDS_REFERENCE.fullmatch(normalised)
        if match is None:
            return jsonify({"success": False, "message": "Invalid SDS parcel reference."}), 404

        shipment_id = int(match.group(1))
        shipment = FBMShipment.query.filter_by(
            id=shipment_id,
            provider="sds",
            provider_shipment_id=normalised,
        ).first()
        if shipment is None:
            return jsonify({"success": False, "message": "SDS parcel not found."}), 404

        next_event = _next_event(shipment.status)
        return jsonify({
            "success": True,
            "shipment_id": shipment.id,
            "sds_reference": shipment.provider_shipment_id,
            "status": shipment.status,
            "next_event": next_event,
            "can_advance": next_event is not None,
            "delivered": str(shipment.status or "").strip().lower() == "delivered",
            "read_only": True,
        })

    app._bt38_sds_scanner_lookup_alignment_installed = True
