"""Narrow Packlink draft-save alignment for BT38 FBM.

Keep the existing Packlink contract intact. The only behavioural change is that
sender location selector identities resolved from Packlink are written back onto
the sender address before the existing shipment POST/PUT save runs. After the
existing save, BT38 exposes the exact provider state and provider-reported
missing fields instead of inventing a Ready/Draft status.
"""
from __future__ import annotations

from functools import wraps
from typing import Any


def _provider_state(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("state", "status", "shipment_status", "inbox"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def install_packlink_draft_alignment() -> None:
    from services.fbm_packlink_adapter import PacklinkAdapter

    if getattr(PacklinkAdapter, "_bt38_draft_save_aligned", False):
        return

    original_location_ids = PacklinkAdapter._best_effort_location_ids
    original_create_draft = PacklinkAdapter.create_shipment_draft

    @wraps(original_location_ids)
    def aligned_location_ids(self, from_address, to_address):
        result = original_location_ids(self, from_address, to_address)
        result = result if isinstance(result, dict) else {}

        # The existing adapter already resolves Packlink's sender selector IDs,
        # but previously only copied them into additional_data. Packlink's UI can
        # therefore still show sender country/area as incomplete until Save is
        # pressed manually. Put the exact resolved identities on the sender
        # address before the existing PUT save executes.
        sender_country = self._clean_country(
            result.get("country_code_from") or from_address.get("country")
        )
        sender_zone = self._selector_id(result.get("postal_zone_id_from"))
        sender_postcode = self._selector_id(result.get("zip_code_id_from"))

        if sender_country:
            from_address["country"] = sender_country
            from_address["country_code"] = sender_country
        if sender_zone:
            from_address["postal_zone_id"] = sender_zone
        if sender_postcode:
            from_address["zip_code_id"] = sender_postcode

        return result

    @wraps(original_create_draft)
    def aligned_create_draft(self, *, order, parcel, rate):
        result = original_create_draft(
            self,
            order=order,
            parcel=parcel,
            rate=rate,
        )
        if not isinstance(result, dict):
            return result

        reference = str(result.get("reference") or "").strip()
        if not reference:
            return result

        # Read back the exact provider record after BT38's existing save. Do not
        # guess Ready/Draft. Return Packlink's own state and the concrete fields
        # still missing according to the same persisted provider snapshot.
        snapshot = self.get_shipment(reference)
        missing = self._draft_required_fields_missing(snapshot)
        state = _provider_state(snapshot)

        result["provider_state"] = state
        result["provider_missing_fields"] = missing
        result["provider_saved_complete"] = not bool(missing)
        return result

    PacklinkAdapter._best_effort_location_ids = aligned_location_ids
    PacklinkAdapter.create_shipment_draft = aligned_create_draft
    PacklinkAdapter._bt38_draft_save_aligned = True


# main.py already imports services.governed_mcf_compat on every process boot.
# Install once at import time without adding a second Packlink execution path.
install_packlink_draft_alignment()
