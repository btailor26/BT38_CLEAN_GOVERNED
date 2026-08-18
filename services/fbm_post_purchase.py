"""Single post-purchase path for externally purchased FBM labels.

Provider payment must already have succeeded before this function is called.
The function never purchases postage. It persists the provider result first,
then evaluates carrier/service mapping. Printing is never blocked by an unknown
mapping; marketplace confirmation is held until mapping is verified.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from services.fbm_carrier_mapping import ensure_mapping_review, mapping_payload
from services.fbm_marketplace_confirmation import confirm_external_shipment


STRONGER_PROVIDER_STATES = {"accepted", "in_transit", "delivered"}


def persist_external_label(
    *,
    shipment: FBMShipment,
    marketplace: str,
    provider: str,
    provider_shipment_id: str | None,
    carrier: str | None,
    service: str | None,
    tracking_number: str | None,
    label: dict[str, Any] | None,
    provider_carrier_id: str | None = None,
    provider_service_id: str | None = None,
) -> dict[str, Any]:
    """Persist a confirmed provider label and evaluate marketplace mapping."""
    now = datetime.utcnow()
    label = label or {}

    shipment.provider = provider
    shipment.provider_shipment_id = str(provider_shipment_id or "").strip() or shipment.provider_shipment_id
    shipment.provider_carrier_id = str(provider_carrier_id or "").strip() or shipment.provider_carrier_id
    shipment.provider_service_id = str(provider_service_id or "").strip() or shipment.provider_service_id
    shipment.carrier = str(carrier or "").strip() or shipment.carrier
    shipment.service = str(service or "").strip() or shipment.service
    shipment.tracking_number = str(tracking_number or "").strip() or shipment.tracking_number

    shipment.label_format = str(label.get("format") or "").strip().upper() or shipment.label_format
    shipment.label_document_type = str(label.get("type") or "LABEL").strip() or shipment.label_document_type or "LABEL"
    shipment.label_url = str(label.get("url") or "").strip() or shipment.label_url
    shipment.label_storage_ref = str(label.get("storage_ref") or label.get("reference") or "").strip() or shipment.label_storage_ref
    shipment.label_source = provider
    shipment.label_width = _float_or_none(label.get("width")) or shipment.label_width
    shipment.label_length = _float_or_none(label.get("height") or label.get("length")) or shipment.label_length
    shipment.label_size_unit = str(label.get("units") or label.get("size_unit") or "").strip() or shipment.label_size_unit
    shipment.label_dpi = _int_or_none(label.get("dpi")) or shipment.label_dpi
    shipment.label_page_layout = str(label.get("page_layout") or "").strip() or shipment.label_page_layout

    shipment.purchase_status = "purchased"
    shipment.purchase_error = None
    shipment.label_purchased_at = shipment.label_purchased_at or now

    current_status = str(shipment.status or "").strip().lower()
    if current_status not in STRONGER_PROVIDER_STATES:
        shipment.status = "awaiting_carrier_acceptance"

    mapping, review, mapping_ready = ensure_mapping_review(
        shipment=shipment,
        marketplace=marketplace,
        provider=provider,
        carrier=shipment.carrier,
        service=shipment.service,
    )

    if mapping_ready:
        # A repeated provider status/label read must preserve a completed
        # marketplace confirmation rather than moving it back to a ready state.
        shipment.marketplace_confirmation_status = (
            "confirmed"
            if shipment.marketplace_confirmed_at
            else "mapping_verified_ready"
        )
        shipment.marketplace_confirmation_error = None
    else:
        shipment.marketplace_confirmation_status = "mapping_under_review"
        shipment.marketplace_confirmation_error = review.review_reason

    db.session.commit()

    confirmation = None
    if mapping_ready:
        confirmation = confirm_external_shipment(shipment=shipment, mapping=mapping)

    has_printable_label = bool(
        shipment.label_url
        or label.get("base64")
        or label.get("data")
        or label.get("contents")
    )

    return {
        "shipment_id": shipment.id,
        "provider_shipment_id": shipment.provider_shipment_id,
        "carrier": shipment.carrier,
        "service": shipment.service,
        "tracking_number": shipment.tracking_number,
        "mapping_ready": mapping_ready,
        "mapping": mapping_payload(mapping),
        "mapping_status": "verified" if mapping_ready else "under_review",
        "mapping_message": None if mapping_ready else "Under review for correct marketplace mapping. Label printing is available now.",
        "print_allowed": has_printable_label,
        "marketplace_confirmation_allowed": mapping_ready,
        "marketplace_confirmation": confirmation,
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
