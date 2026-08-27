"""Packlink draft/save alignment for BT38 FBM.

Keep one governed Packlink execution path while matching Packlink's own draft
contract: address objects carry address fields only; Packlink selector identities
stay in additional_data. After POST /shipments, BT38 always saves that newly
created Packlink record once through the same PUT /shipments/{reference} endpoint
used by Packlink PRO's working Save action, then re-reads and verifies provider state.
"""
from __future__ import annotations

from functools import wraps
from typing import Any


def _provider_state(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    source = payload.get("shipment") if isinstance(payload.get("shipment"), dict) else payload
    for key in ("state", "status", "shipment_status", "inbox"):
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _strip_non_contract_address_selectors(body: dict[str, Any]) -> dict[str, Any]:
    """Keep Packlink selector IDs in additional_data, not address DTOs."""
    if not isinstance(body, dict):
        return body
    for side in ("from", "to"):
        address = body.get(side)
        if not isinstance(address, dict):
            continue
        for key in (
            "country_code",
            "countryCode",
            "postal_zone_id",
            "postalZoneId",
            "zip_code_id",
            "zipCodeId",
        ):
            address.pop(key, None)
    return body


def install_packlink_draft_alignment() -> None:
    from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError

    if getattr(PacklinkAdapter, "_bt38_draft_save_aligned", False):
        return

    original_create_draft = PacklinkAdapter.create_shipment_draft

    @wraps(original_create_draft)
    def aligned_create_draft(self, *, order, parcel, rate):
        # Keep the initial POST aligned to Packlink's Address DTO. Selector IDs
        # already resolved by the adapter remain in additional_data.
        original_post_json = self._post_json
        had_instance_override = "_post_json" in self.__dict__
        prior_instance_override = self.__dict__.get("_post_json")

        def post_with_contract_address(endpoint, body):
            normalized_endpoint = str(endpoint or "").strip("/")
            if normalized_endpoint == "shipments" and isinstance(body, dict):
                _strip_non_contract_address_selectors(body)
            return original_post_json(endpoint, body)

        self._post_json = post_with_contract_address
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

        # IMPORTANT: do not trust BT38's pre-save field detector here. Packlink can
        # return country='GB' while its UI still says 'Country is mandatory'. The
        # working Packlink PRO action captured in-browser is an unconditional PUT
        # to /v1/shipments/{reference}. Mirror that save transition once for every
        # newly created shipment, then verify the provider's post-save record.
        saved = self.save_shipment_draft(reference)
        snapshot = (
            saved.get("raw")
            if isinstance(saved, dict) and isinstance(saved.get("raw"), dict)
            else self.get_shipment(reference)
        )
        state = _provider_state(snapshot)
        if not state and isinstance(saved, dict):
            state = saved.get("provider_status")
        blockers = self.draft_blockers(snapshot)
        ready = self._provider_ready_to_ship(snapshot)

        if not ready:
            labels = [
                str(item.get("label") or item.get("code") or "Packlink draft")
                for item in blockers
                if isinstance(item, dict)
            ]
            detail = ", ".join(labels) if labels else (state or "provider still reports draft/incomplete")
            raise PacklinkRequestError(
                f"Packlink shipment {reference} was created and PUT-saved but did not reach a payment-ready state: {detail}."
            )

        result["provider_state"] = state
        result["provider_missing_fields"] = []
        result["provider_saved_complete"] = True
        result["provider_auto_saved"] = True
        result["verified"] = True
        return result

    PacklinkAdapter.create_shipment_draft = aligned_create_draft
    PacklinkAdapter._bt38_draft_save_aligned = True


def install_packlink_draft_response_alignment() -> None:
    """Expose Packlink's final provider state on the existing BT38 route."""
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
            blockers = adapter.draft_blockers(snapshot)
            ready = adapter._provider_ready_to_ship(snapshot)
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
        payload["provider_missing_fields"] = [
            item.get("label") or item.get("code")
            for item in blockers
            if isinstance(item, dict)
        ]
        payload["provider_saved_complete"] = ready

        if ready:
            payload["message"] = (
                f"Packlink returned shipment {reference}. Provider draft is saved "
                f"and ready for payment" + (f" ({state})." if state else ".")
            )
        else:
            detail = ", ".join(
                str(item.get("label") or item.get("code"))
                for item in blockers
                if isinstance(item, dict)
            )
            payload["message"] = (
                f"Packlink returned shipment {reference}, but provider state is not "
                f"payment-ready" + (f": {detail}." if detail else (f" ({state})." if state else "."))
            )

        return app.response_class(
            response=app.json.dumps(payload),
            status=response.status_code,
            mimetype="application/json",
        )

    aligned_view._bt38_packlink_response_aligned = True
    app.view_functions[endpoint] = aligned_view


# main.py imports this alignment module on process boot through the governed
# compatibility path. Install once without adding a second shipment-create path.
install_packlink_draft_alignment()
install_packlink_draft_response_alignment()
