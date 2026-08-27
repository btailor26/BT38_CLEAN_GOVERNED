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
        # but previously only copied them into additional_data. Put those exact
        # Packlink-owned selector identities onto the sender address before the
        # existing shipment PUT/save is sent.
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

        # Read back the exact Packlink record after the existing save. Do not
        # translate or invent Ready/Draft. Keep Packlink's own state and the
        # concrete fields still incomplete on the persisted provider snapshot.
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


def install_packlink_draft_response_alignment() -> None:
    """Expose Packlink's real post-save state on the existing BT38 draft route."""
    from app import app
    from services.fbm_packlink_adapter import (
        PacklinkAdapter,
        PacklinkConfigurationError,
        PacklinkRequestError,
    )

    endpoint = "governed_fbm.packlink_create_draft"
    original_view = app.view_functions.get(endpoint)
    if original_view is None:
        return
    if getattr(original_view, "_bt38_packlink_response_aligned", False):
        return

    @wraps(original_view)
    def aligned_view(*args, **kwargs):
        response = app.make_response(original_view(*args, **kwargs))
        if response.status_code >= 400:
            return response

        payload = response.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return response

        reference = str(payload.get("provider_reference") or "").strip()
        if not reference:
            return response

        try:
            adapter = PacklinkAdapter()
            snapshot = adapter.get_shipment(reference)
            state = _provider_state(snapshot)
            missing = adapter._draft_required_fields_missing(snapshot)
        except (PacklinkConfigurationError, PacklinkRequestError) as exc:
            payload["provider_state_read_error"] = str(exc)
            payload["message"] = (
                f"Packlink returned shipment {reference}, but BT38 could not read "
                f"the provider's saved state: {exc}"
            )
            return app.response_class(
                response=app.json.dumps(payload),
                status=response.status_code,
                mimetype="application/json",
            )

        payload["provider_state"] = state
        payload["provider_missing_fields"] = missing
        payload["provider_saved_complete"] = not bool(missing)

        if missing:
            payload["message"] = (
                f"Packlink returned shipment {reference}. Provider still reports "
                f"incomplete fields: {', '.join(missing)}."
                + (f" Provider status: {state}." if state else "")
            )
        elif state:
            payload["message"] = (
                f"Packlink returned shipment {reference}. Provider status: {state}."
            )
        else:
            payload["message"] = (
                f"Packlink returned shipment {reference}. Packlink did not return "
                "a provider status in the saved shipment response."
            )

        return app.response_class(
            response=app.json.dumps(payload),
            status=response.status_code,
            mimetype="application/json",
        )

    aligned_view._bt38_packlink_response_aligned = True
    app.view_functions[endpoint] = aligned_view


# main.py already imports services.governed_mcf_compat on every process boot.
# Install once at import time without adding a second Packlink execution path.
install_packlink_draft_alignment()
install_packlink_draft_response_alignment()
