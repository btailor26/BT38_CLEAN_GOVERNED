"""Single post-purchase path for externally purchased FBM labels.

Provider payment must already have succeeded before this function is called.
The function never purchases postage. The provider label is persisted first and
remains printable even when a new carrier/service mapping is still under review.
A shipment is complete only when a tracking number exists. Marketplace
confirmation is released only after tracking exists and the mapping is verified.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.fbm_carrier_mapping import ensure_mapping_review, mapping_payload
from services.fbm_marketplace_confirmation import confirm_external_shipment


STRONGER_PROVIDER_STATES = {"accepted", "in_transit", "delivered"}
_AMAZON_TRACKING_SEPARATORS = "-‐‑‒–—―"


def _normalized_provider_state(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .upper()
        .replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("/", "_")
    )


def reconcile_provider_lifecycle_state(
    shipment: FBMShipment,
    *,
    observed_at: datetime | None = None,
) -> str:
    """Project persisted provider truth onto the canonical three-step journey.

    This deliberately has no order-age cutoff. Historical, current and future
    shipments use the same persisted provider status. A terminal delivery proves
    the prior journey milestones even when an earlier callback was missed.
    """
    provider_state = _normalized_provider_state(shipment.last_provider_status)
    observed_at = observed_at or shipment.last_provider_checked_at or datetime.utcnow()

    delivered_states = {
        "DELIVERED",
        "DELIVERY_COMPLETE",
        "DELIVERY_COMPLETED",
        "SUCCESSFULLY_DELIVERED",
        "COMPLETED_DELIVERY",
    }
    in_transit_states = {
        "IN_TRANSIT",
        "OUT_FOR_DELIVERY",
        "IN_DELIVERY",
        "ON_ROUTE",
    }
    accepted_states = {
        "ACCEPTED",
        "CARRIER_ACCEPTED",
        "PICKED_UP",
        "PICKEDUP",
        "COLLECTED",
    }

    if provider_state in delivered_states or provider_state.endswith("_DELIVERED"):
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or observed_at
        shipment.first_movement_at = shipment.first_movement_at or observed_at
        shipment.delivered_at = shipment.delivered_at or observed_at
        shipment.status = "delivered"
    elif provider_state in in_transit_states or "IN_TRANSIT" in provider_state:
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or observed_at
        shipment.first_movement_at = shipment.first_movement_at or observed_at
        if shipment.delivered_at is None:
            shipment.status = "in_transit"
    elif provider_state in accepted_states:
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or observed_at
        if shipment.delivered_at is None and shipment.first_movement_at is None:
            shipment.status = "accepted"

    return shipment.status


def _amazon_tracking_number(value: Any) -> str | None:
    """Return the courier tracking value in Amazon's compact form.

    Packlink/carriers can display tracking with spaces or dash variants for
    readability. Amazon should receive the courier tracking itself without
    those presentation separators.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    compact = "".join(raw.split())
    for separator in _AMAZON_TRACKING_SEPARATORS:
        compact = compact.replace(separator, "")
    return compact or None


def _marketplace_delivery_promise(shipment: FBMShipment, marketplace: str) -> dict[str, Any] | None:
    """Read the marketplace-owned delivery promise for the journey response.

    BT38 does not calculate or rebuild the customer promise. For Amazon this is
    an exact on-demand Orders API read. Unsupported marketplaces return no
    promise rather than guessing from the carrier or Packlink service.
    """
    if str(marketplace or "").strip().casefold() != "amazon":
        return None
    order = MarketplaceOrder.query.filter_by(
        store_id=shipment.store_id,
        marketplace_order_id=shipment.marketplace_order_id,
    ).order_by(MarketplaceOrder.id.asc()).first()
    if order is None:
        return None
    try:
        from services.fbm_amazon_order_profile import get_amazon_delivery_promise
        return get_amazon_delivery_promise(order)
    except Exception as exc:
        return {
            "source": "amazon",
            "earliest_delivery_at": None,
            "latest_delivery_at": None,
            "unavailable_reason": str(exc),
        }


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
    """Persist a confirmed provider label and evaluate tracking + mapping gates."""
    now = datetime.utcnow()
    label = label or {}

    shipment.provider = provider
    shipment.provider_shipment_id = str(provider_shipment_id or "").strip() or shipment.provider_shipment_id
    shipment.provider_carrier_id = str(provider_carrier_id or "").strip() or shipment.provider_carrier_id
    shipment.provider_service_id = str(provider_service_id or "").strip() or shipment.provider_service_id
    shipment.carrier = str(carrier or "").strip() or shipment.carrier
    shipment.service = str(service or "").strip() or shipment.service

    incoming_tracking = str(tracking_number or "").strip() or shipment.tracking_number
    if str(marketplace or "").strip().casefold() == "amazon":
        shipment.tracking_number = _amazon_tracking_number(incoming_tracking) or shipment.tracking_number
    else:
        shipment.tracking_number = incoming_tracking

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

    tracking_ready = bool(str(shipment.tracking_number or "").strip())
    shipment.purchase_status = "purchased" if tracking_ready else "label_ready_tracking_pending"
    shipment.purchase_error = None
    shipment.label_purchased_at = shipment.label_purchased_at or now

    current_status = str(shipment.status or "").strip().lower()
    if current_status not in STRONGER_PROVIDER_STATES:
        shipment.status = "awaiting_carrier_acceptance" if tracking_ready else "awaiting_tracking"

    # A provider status read can arrive after earlier lifecycle callbacks were
    # missed. Reconcile it before committing so every order age uses the same
    # canonical Picked up -> In transit -> Delivered milestones.
    reconcile_provider_lifecycle_state(shipment, observed_at=shipment.last_provider_checked_at or now)

    # Mapping can be learned as soon as the paid label identifies carrier/service,
    # but tracking is the completion gate. A label without tracking is printable
    # and remains open; it must never confirm the marketplace yet.
    mapping, review, mapping_ready = ensure_mapping_review(
        shipment=shipment,
        marketplace=marketplace,
        provider=provider,
        carrier=shipment.carrier,
        service=shipment.service,
    )

    if not tracking_ready:
        shipment.marketplace_confirmation_status = "tracking_pending"
        shipment.marketplace_confirmation_error = None
    elif mapping_ready:
        shipment.marketplace_confirmation_status = (
            "confirmed"
            if shipment.marketplace_confirmed_at
            else "mapping_verified_ready"
        )
        shipment.marketplace_confirmation_error = None
    else:
        shipment.marketplace_confirmation_status = "mapping_under_review"
        shipment.marketplace_confirmation_error = review.review_reason

    # Provider success, label, tracking and mapping/review state are committed
    # before any marketplace write.
    db.session.commit()

    confirmation = None
    if tracking_ready and mapping_ready:
        confirmation = confirm_external_shipment(shipment=shipment, mapping=mapping)

    has_printable_label = bool(
        shipment.label_url
        or label.get("base64")
        or label.get("data")
        or label.get("contents")
    )
    marketplace_promise = _marketplace_delivery_promise(shipment, marketplace)

    return {
        "shipment_id": shipment.id,
        "provider_shipment_id": shipment.provider_shipment_id,
        "carrier": shipment.carrier,
        "service": shipment.service,
        "tracking_number": shipment.tracking_number,
        "shipment_complete": tracking_ready,
        "completion_reason": "tracking_recorded" if tracking_ready else "tracking_pending",
        "mapping_ready": mapping_ready,
        "mapping": mapping_payload(mapping),
        "mapping_status": "verified" if mapping_ready else "under_review",
        "mapping_message": None if mapping_ready else "Under review for correct marketplace mapping. Label printing and physical dispatch are available now; marketplace transfer is held until the mapping is verified.",
        "print_allowed": has_printable_label,
        "marketplace_confirmation_allowed": tracking_ready and mapping_ready,
        "marketplace_confirmation": confirmation,
        "marketplace_promise": marketplace_promise,
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
