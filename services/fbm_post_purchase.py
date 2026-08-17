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
    shipment.carrier = str(carrier or "").strip() or None
    shipment.service = str(service or "").strip() or None
    shipment.tracking_number = str(tracking_number or "").strip() or None

    shipment.label_format = str(label.get("format") or "").strip().upper() or None
    shipment.label_document_type = str(label.get("type") or "LABEL").strip() or "LABEL"
    shipment.label_url = str(label.get("url") or "").strip() or None
    shipment.label_storage_ref = str(label.get("storage_ref") or label.get("reference") or "").strip() or None
    shipment.label_source = provider
    shipment.label_width = _float_or_none(label.get("width"))
    shipment.label_length = _float_or_none(label.get("height") or label.get("length"))
    shipment.label_size_unit = str(label.get("units") or label.get("size_unit") or "").strip() or None
    shipment.label_dpi = _int_or_none(label.get("dpi"))
    shipment.label_page_layout = str(label.get("page_layout") or "").strip() or None

    shipment.purchase_status = "purchased"
    shipment.purchase_error = None
    shipment.label_purchased_at = shipment.label_purchased_at or now
    shipment.status = "awaiting_carrier_acceptance"

    mapping, review, mapping_ready = ensure_mapping_review(
        shipment=shipment,
        marketplace=marketplace,
        provider=provider,
        carrier=shipment.carrier,
        service=shipment.service,
    )

    if mapping_ready:
        shipment.marketplace_confirmation_status = "mapping_verified_ready"
        shipment.marketplace_confirmation_error = None
    else:
        shipment.marketplace_confirmation_status = "mapping_under_review"
        shipment.marketplace_confirmation_error = review.review_reason

    # Provider success and label/mapping state are committed together before any
    # caller attempts browser/QZ printing.
    db.session.commit()

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
        "print_allowed": True,
        "marketplace_confirmation_allowed": mapping_ready,
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
