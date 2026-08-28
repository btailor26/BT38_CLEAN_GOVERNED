"""Packlink draft/save alignment for BT38 FBM.

Keep one governed Packlink execution path while matching Packlink PRO's own
browser Save contract. After POST /shipments, BT38 reads the created provider
record, builds the same writable shipment shape used by Packlink PRO's successful
PUT /shipments/{reference} action, saves it once, then re-reads provider state.
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


def _clean_address(value: Any) -> dict[str, Any]:
    address = dict(value) if isinstance(value, dict) else {}
    _strip_non_contract_address_selectors({"to": address})
    # Packlink PRO's browser Save sends the ordinary Address DTO only.
    allowed = (
        "city", "country", "state", "zip_code", "company", "email",
        "name", "phone", "street1", "street2", "surname",
    )
    return {key: address.get(key) for key in allowed if key in address and address.get(key) not in (None, "")}


def _browser_save_body(snapshot: dict[str, Any], reference: str) -> dict[str, Any]:
    """Build the Packlink PRO browser PUT shape proven by the captured Save call."""
    source = snapshot.get("shipment") if isinstance(snapshot.get("shipment"), dict) else snapshot
    if not isinstance(source, dict):
        source = {}

    additional_data = dict(source.get("additional_data")) if isinstance(source.get("additional_data"), dict) else {}

    # Preserve provider-resolved selector IDs and names exactly where Packlink's
    # browser sends them. Do not duplicate them into from/to address objects.
    from_address = _clean_address(source.get("from"))
    to_address = _clean_address(source.get("to"))

    if from_address.get("country") and not additional_data.get("postal_zone_name_from"):
        additional_data["postal_zone_name_from"] = from_address.get("state") or "United Kingdom"
    if to_address.get("country") and not additional_data.get("postal_zone_name_to"):
        additional_data["postal_zone_name_to"] = to_address.get("state") or "United Kingdom"

    packages: list[dict[str, Any]] = []
    raw_packages = source.get("packages") if isinstance(source.get("packages"), list) else []
    for index, package in enumerate(raw_packages):
        if not isinstance(package, dict):
            continue
        row = dict(package)
        package_id = str(row.get("id") or row.get("name") or "custom-parcel-id")
        row["id"] = package_id
        row["name"] = str(row.get("name") or package_id)
        packages.append(row)

    carrier = source.get("carrier")
    if isinstance(carrier, dict):
        carrier = carrier.get("name") or carrier.get("label") or carrier.get("code")
    service = source.get("service")
    if isinstance(service, dict):
        service = service.get("name") or service.get("label") or service.get("code")

    body: dict[str, Any] = {
        "carrier": carrier or "",
        "service": service or "",
        "service_id": source.get("service_id"),
        "adult_signature": bool(source.get("adult_signature", False)),
        "additional_handling": bool(source.get("additional_handling", False)),
        "insurance": source.get("insurance") if isinstance(source.get("insurance"), dict) else {
            "amount": 0,
            "insurance_selected": False,
        },
        "print_in_store_selected": bool(source.get("print_in_store_selected", False)),
        "proof_of_delivery": bool(source.get("proof_of_delivery", False)),
        "priority": bool(source.get("priority", False)),
        "additional_data": additional_data,
        "content": source.get("content") or "Goods",
        "content_second_hand": bool(source.get("content_second_hand", False)),
        "contentvalue": source.get("contentvalue") if source.get("contentvalue") is not None else 20,
        "currency": source.get("currency") or "EUR",
        "from": from_address,
        "packages": packages,
        "packlink_reference": reference,
        "shipment_custom_reference": source.get("shipment_custom_reference") or "",
        "to": to_address,
        "voucher_name": source.get("voucher_name"),
        "has_customs": bool(source.get("has_customs", False)),
        "selected_products": source.get("selected_products") if isinstance(source.get("selected_products"), dict) else {
            "ddp": {"is_selected": None}
        },
    }

    # Remove only keys Packlink's browser omits when absent; keep explicit false/null
    # values that are part of the proven Save contract.
    if body.get("service_id") is None:
        body.pop("service_id", None)
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

        # The captured successful Packlink PRO Save uses a full browser-shaped PUT,
        # not BT38's older reduced writable-key reconstruction. Mirror that body.
        snapshot = self.get_shipment(reference)
        if not isinstance(snapshot, dict) or not snapshot:
            raise PacklinkRequestError(
                f"Packlink shipment {reference} was created but could not be read before browser-aligned save."
            )
        save_body = _browser_save_body(snapshot, reference)
        if not save_body.get("from") or not save_body.get("to") or not save_body.get("packages"):
            raise PacklinkRequestError(
                f"Packlink shipment {reference} did not expose enough provider data for browser-aligned save."
            )

        self._put_json(f"shipments/{reference}", save_body)
        snapshot = self.get_shipment(reference)
        state = _provider_state(snapshot)
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
                f"Packlink shipment {reference} was browser-PUT-saved but did not reach a payment-ready state: {detail}."
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


install_packlink_draft_alignment()
install_packlink_draft_response_alignment()
