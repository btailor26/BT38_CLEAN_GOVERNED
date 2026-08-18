"""Persistent carrier/service mapping for BT38 FBM.

A postage purchase and label print must not be blocked merely because BT38 has
never seen a provider's carrier/service string before. Marketplace confirmation
*is* blocked until that mapping has been verified once.

Mappings are marketplace-specific because Amazon/eBay carrier/service codes can
differ even when the provider label says the same thing (for example UPS).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_models import FBMCarrierServiceMapping, FBMShipmentMappingReview


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def mapping_key(*, marketplace: str, provider: str, carrier: str, service: str) -> dict[str, str]:
    return {
        "marketplace": _norm(marketplace),
        "provider": _norm(provider),
        "provider_carrier": _norm(carrier),
        "provider_service": _norm(service),
    }


def find_mapping(*, marketplace: str, provider: str, carrier: str, service: str) -> FBMCarrierServiceMapping | None:
    key = mapping_key(marketplace=marketplace, provider=provider, carrier=carrier, service=service)
    return FBMCarrierServiceMapping.query.filter_by(**key).first()


def ensure_mapping_review(
    *,
    shipment,
    marketplace: str,
    provider: str,
    carrier: str | None,
    service: str | None,
) -> tuple[FBMCarrierServiceMapping, FBMShipmentMappingReview, bool]:
    """Return mapping + shipment review + whether marketplace confirmation is safe.

    The identity is marketplace + provider + carrier + service. A new identity is
    saved once as ``pending_review``. The purchased label is still printable.
    After that identity is verified, every future matching shipment reuses the
    saved marketplace mapping automatically and does not ask for another review.
    """
    carrier_text = str(carrier or "").strip()
    service_text = str(service or "").strip()
    mapping = find_mapping(
        marketplace=marketplace,
        provider=provider,
        carrier=carrier_text,
        service=service_text,
    )
    if mapping is None:
        key = mapping_key(
            marketplace=marketplace,
            provider=provider,
            carrier=carrier_text,
            service=service_text,
        )
        mapping = FBMCarrierServiceMapping(
            **key,
            provider_carrier_display=carrier_text or "Unknown carrier",
            provider_service_display=service_text or "Unknown service",
            verification_status="pending_review",
            usage_count=0,
        )
        db.session.add(mapping)
        db.session.flush()

    review = FBMShipmentMappingReview.query.filter_by(shipment_id=shipment.id).first()
    first_use_of_mapping_for_shipment = review is None or review.mapping_id != mapping.id

    if review is None:
        review = FBMShipmentMappingReview(
            shipment_id=shipment.id,
            mapping_id=mapping.id,
            status="verified" if mapping.verification_status == "verified" else "under_review",
        )
        db.session.add(review)
    else:
        review.mapping_id = mapping.id
        review.status = "verified" if mapping.verification_status == "verified" else "under_review"

    if first_use_of_mapping_for_shipment:
        mapping.usage_count = int(mapping.usage_count or 0) + 1
    mapping.last_used_at = datetime.utcnow()

    if mapping.verification_status == "verified":
        review.resolved_at = review.resolved_at or datetime.utcnow()
        review.review_reason = None
        return mapping, review, True

    review.resolved_at = None
    review.review_reason = (
        f"{mapping.provider_carrier_display} · {mapping.provider_service_display} has not yet been verified "
        f"for {marketplace}. Label printing and physical dispatch are allowed; marketplace tracking confirmation is held."
    )
    return mapping, review, False


def _validate_amazon_packlink_mapping(
    mapping: FBMCarrierServiceMapping,
    *,
    carrier_code: str,
    carrier_name: str | None,
    service_code: str | None,
    service_name: str | None,
) -> None:
    """Fail closed before saving an Amazon+Packlink VTR mapping."""
    if _norm(mapping.marketplace) != "amazon" or _norm(mapping.provider) != "packlink":
        return

    values = [
        carrier_code,
        carrier_name or "",
        service_code or "",
        service_name or "",
    ]
    if any(_norm(value) == "other" for value in values if str(value or "").strip()):
        raise ValueError("Amazon Packlink mappings cannot use Other for carrier or shipping service.")

    resolved_carrier = str(carrier_name or carrier_code or "").strip()
    resolved_service = str(service_name or service_code or "").strip()
    if not resolved_carrier:
        raise ValueError("Amazon Packlink mapping requires the exact Amazon carrier.")
    if not resolved_service:
        raise ValueError("Amazon Packlink mapping requires the exact Amazon shipping service.")

    provider_carrier = _norm(mapping.provider_carrier_display)
    carrier_key = _norm(resolved_carrier)
    service_key = _norm(resolved_service)

    # Proven account-specific rule: Yodel is confirmed to Amazon as Yodel with
    # service Xpert. Do not allow a generic Yodel/Other-style mapping to be saved.
    if "yodel" in provider_carrier:
        if "yodel" not in carrier_key:
            raise ValueError("Yodel Packlink shipments must map to Amazon carrier Yodel.")
        if service_key != "xpert":
            raise ValueError("Yodel Packlink shipments must map to Amazon shipping service Xpert.")

    # Packlink may return either Evri or Hermes naming. The verified Amazon
    # mapping must remain in the Hermes family and identify the exact service.
    if "evri" in provider_carrier or "hermes" in provider_carrier:
        if "hermes" not in carrier_key:
            raise ValueError("Evri/Hermes Packlink shipments must map to Amazon's Hermes carrier identity.")
        if not (
            ("two day" in service_key or "2nd day" in service_key or "2 day" in service_key)
            and ("drop off" in service_key or "drop-off" in service_key or "dropoff" in service_key)
        ):
            raise ValueError("Evri/Hermes Packlink shipments must map to the exact Amazon two-day drop-off service.")


def verify_mapping(
    mapping: FBMCarrierServiceMapping,
    *,
    marketplace_carrier_code: str,
    marketplace_carrier_name: str | None = None,
    marketplace_service_code: str | None = None,
    marketplace_service_name: str | None = None,
    verified_by: str | None = None,
) -> FBMCarrierServiceMapping:
    """Verify one mapping identity and release all shipments waiting on it.

    Saving the mapping is the one-time user action. The verified mapping is
    committed before any marketplace call. Release results are attached to the
    returned model for the route/UI to report truthfully; a failed marketplace
    confirmation never rolls back the saved mapping or an already-paid label.
    """
    carrier_code = str(marketplace_carrier_code or "").strip()
    if not carrier_code:
        raise ValueError("Marketplace carrier code is required.")

    carrier_name = str(marketplace_carrier_name or "").strip() or None
    service_code = str(marketplace_service_code or "").strip() or None
    service_name = str(marketplace_service_name or "").strip() or None

    _validate_amazon_packlink_mapping(
        mapping,
        carrier_code=carrier_code,
        carrier_name=carrier_name,
        service_code=service_code,
        service_name=service_name,
    )

    waiting_reviews = FBMShipmentMappingReview.query.filter_by(
        mapping_id=mapping.id,
        status="under_review",
    ).all()
    waiting_shipments = [review.shipment for review in waiting_reviews if review.shipment is not None]

    mapping.marketplace_carrier_code = carrier_code
    mapping.marketplace_carrier_name = carrier_name
    mapping.marketplace_service_code = service_code
    mapping.marketplace_service_name = service_name
    mapping.verification_status = "verified"
    mapping.verified_at = datetime.utcnow()
    mapping.verified_by = str(verified_by or "user").strip() or "user"
    mapping.last_error = None

    now = datetime.utcnow()
    for review in waiting_reviews:
        review.status = "verified"
        review.resolved_at = now
        review.review_reason = None

    db.session.commit()

    release_results: list[dict[str, Any]] = []
    if waiting_shipments:
        from services.fbm_marketplace_confirmation import confirm_external_shipment
        for shipment in waiting_shipments:
            result = confirm_external_shipment(shipment=shipment, mapping=mapping)
            release_results.append({
                "shipment_id": shipment.id,
                "marketplace_order_id": shipment.marketplace_order_id,
                **(result or {}),
            })

    failed = [item for item in release_results if not item.get("success")]
    mapping._release_summary = {
        "attempted": len(release_results),
        "confirmed": sum(1 for item in release_results if item.get("success")),
        "failed": len(failed),
        "results": release_results,
    }
    if failed:
        mapping.last_error = (
            f"Mapping saved, but {len(failed)} waiting shipment"
            f"{'s' if len(failed) != 1 else ''} could not be confirmed to the marketplace."
        )
        db.session.commit()

    return mapping


def mapping_payload(mapping: FBMCarrierServiceMapping | None) -> dict[str, Any] | None:
    if mapping is None:
        return None
    payload = {
        "id": mapping.id,
        "marketplace": mapping.marketplace,
        "provider": mapping.provider,
        "provider_carrier": mapping.provider_carrier_display,
        "provider_service": mapping.provider_service_display,
        "verification_status": mapping.verification_status,
        "marketplace_carrier_code": mapping.marketplace_carrier_code,
        "marketplace_carrier_name": mapping.marketplace_carrier_name,
        "marketplace_service_code": mapping.marketplace_service_code,
        "marketplace_service_name": mapping.marketplace_service_name,
        "verified_at": mapping.verified_at.isoformat() if mapping.verified_at else None,
        "usage_count": int(mapping.usage_count or 0),
        "last_error": mapping.last_error,
    }
    release_summary = getattr(mapping, "_release_summary", None)
    if release_summary is not None:
        payload["release_summary"] = release_summary
    return payload
