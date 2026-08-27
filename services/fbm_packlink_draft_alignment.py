"""Narrow Packlink draft-save alignment for BT38 FBM.

Keep the existing Packlink contract intact. Packlink location selector identities
resolved by the canonical adapter are bound to the outgoing address objects at
the existing shipment POST boundary. This preserves one Packlink execution path
while making the recipient country/postcode selection persist exactly as a
completed Packlink form expects. BT38 then exposes Packlink's exact provider
state and provider-reported missing fields instead of inventing Ready/Draft.
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


def _bind_recipient_selectors(adapter: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Bind Packlink-owned recipient selector IDs onto the visible address.

    The canonical adapter already resolves postal_zone_id_to and zip_code_id_to
    from Packlink before creating a shipment. Packlink PRO can display the ISO
    country text when those IDs exist only in additional_data, while its edit
    form still treats Country as mandatory. Bind the same resolved IDs onto the
    recipient address immediately before the existing POST /shipments call.
    """
    if not isinstance(body, dict):
        return body
    recipient = body.get("to")
    additional = body.get("additional_data")
    if not isinstance(recipient, dict) or not isinstance(additional, dict):
        return body

    country = adapter._clean_country(
        recipient.get("country_code") or recipient.get("country")
    )
    postal_zone_id = adapter._selector_id(additional.get("postal_zone_id_to"))
    zip_code_id = adapter._selector_id(additional.get("zip_code_id_to"))

    if country:
        recipient["country"] = country
        recipient["country_code"] = country
    if postal_zone_id:
        recipient["postal_zone_id"] = postal_zone_id
    if zip_code_id:
        recipient["zip_code_id"] = zip_code_id
    return body


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

        # The canonical adapter resolves Packlink's sender selector IDs but only
        # exposes them through additional_data. Keep the same Packlink-owned
        # identities on the sender address as well.
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
        # The canonical adapter currently strips recipient selector IDs from the
        # address after resolving them. Intercept only this adapter instance's
        # existing shipment POST, bind those same IDs back onto body['to'], then
        # immediately restore the original method. No second request/path is
        # introduced and the adapter remains POST-once + provider readback.
        original_post_json = self._post_json
        had_instance_override = "_post_json" in self.__dict__
        prior_instance_override = self.__dict__.get("_post_json")

        def post_with_bound_recipient(endpoint, body):
            normalized_endpoint = str(endpoint or "").strip("/")
            if normalized_endpoint == "shipments" and isinstance(body, dict):
                _bind_recipient_selectors(self, body)
            return original_post_json(endpoint, body)

        self._post_json = post_with_bound_recipient
        try:
            result = original_create_draft(
                self,
                order=order,
                parcel=parcel,
                rate=rate,
            )
        finally:
            if had_instance_override:
                self._post_json = prior_instance_override
            else:
                self.__dict__.pop("_post_json", None)

        if not isinstance(result, dict):
            return result

        reference = str(result.get("reference") or "").strip()
        if not reference:
            return result

        # Read back the exact Packlink record after the provider create. Do not
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
    """Expose Packlink's real post-create state on the existing BT38 route."""
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
