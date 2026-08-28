"""Narrow Packlink location-selector fallback for the existing draft method.

Packlink rate quoting accepts a real GB country/city/postcode without internal
browser selector IDs.  Some valid UK postcodes do not resolve a zip_code_id via
the auxiliary locations endpoint.  That optional lookup must not block the
actual shipment POST.  When (and only when) the existing method fails before
POST for that selector reason, continue through the same provider shipment POST
and the already-proven browser-shaped PUT Save contract.

No country/state rule is changed here.  No second route or shipment lifecycle is
introduced.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Any

from services.fbm_order_mapper import order_lines, ship_from, ship_to
from services.fbm_packlink_adapter import (
    PACKLINK_ACCOUNT_COUNTRY,
    PACKLINK_DEFAULT_CONTENT_VALUE,
    PACKLINK_DRAFT_SOURCE,
    PACKLINK_PLATFORM,
    PacklinkAdapter,
    PacklinkRequestError,
)
from services.fbm_packlink_draft_alignment import _browser_save_body, _provider_state


def _optional_selector_failure(exc: Exception) -> bool:
    text = str(exc or "").casefold()
    return (
        "selector could not be resolved" in text
        and ("postcode" in text or "country" in text or "city" in text)
    )


def _create_without_optional_selector_ids(
    adapter: PacklinkAdapter,
    *,
    order: Any,
    parcel: dict[str, Any],
    rate: dict[str, Any],
) -> dict[str, Any]:
    service_id = str(rate.get("service_id") or rate.get("id") or "").strip()
    if not service_id:
        raise PacklinkRequestError("Selected Packlink service ID is missing.")

    origin = ship_from()
    stored_destination = parcel.get("_packlink_handoff_destination")
    destination = dict(stored_destination) if isinstance(stored_destination, dict) else ship_to(order)
    for field in ("name", "address1", "city", "postcode", "country", "phone"):
        if not destination.get(field):
            raise PacklinkRequestError(f"Destination {field} is missing from the BT38 order.")
    for field in ("weight_kg", "width_cm", "height_cm", "length_cm"):
        if not parcel.get(field):
            raise PacklinkRequestError(f"Parcel {field} is missing.")

    account = adapter._get_json("clients")
    platform_country = adapter._clean_country(
        (account or {}).get("country") if isinstance(account, dict) else None
        or origin.get("country")
        or PACKLINK_ACCOUNT_COUNTRY
    )
    customer_name, customer_surname = adapter._split_name(
        destination.get("name"), fallback_surname="Customer"
    )
    sender_name, sender_surname = adapter._split_name(
        origin.get("name") or "B & T Outlet", fallback_surname="Outlet"
    )

    lines = order_lines(order)
    content_parts: list[str] = []
    content_value = 0.0
    items: list[dict[str, Any]] = []
    for line in lines:
        qty = max(1, int(getattr(line, "quantity", 1) or 1))
        sku = str(getattr(line, "sku", "Item") or "Item").strip() or "Item"
        content_parts.append(f"{qty} {sku}")
        unit_price = adapter._positive_amount(getattr(line, "unit_price", None))
        if unit_price is not None:
            content_value += unit_price * qty
        items.append({
            "title": sku,
            "quantity": qty,
            "price": (unit_price or 0.0) * qty,
        })
    if content_value <= 0:
        content_value = PACKLINK_DEFAULT_CONTENT_VALUE

    from_address = {
        "name": sender_name,
        "surname": sender_surname,
        "company": origin.get("company") or "B & T Outlet",
        "street1": origin.get("address1"),
        "street2": origin.get("address2") or "",
        "zip_code": adapter._clean_postcode(origin.get("postcode")),
        "city": origin.get("city"),
        "state": origin.get("region") or None,
        "country": adapter._clean_country(origin.get("country") or "GB"),
        "phone": origin.get("phone") or "",
        "email": origin.get("email") or "",
    }
    to_address = {
        "name": customer_name,
        "surname": customer_surname,
        "company": destination.get("company") or "",
        "street1": destination.get("address1"),
        "street2": destination.get("address2") or "",
        "zip_code": adapter._clean_postcode(destination.get("postcode")),
        "city": destination.get("city"),
        "state": destination.get("region") or None,
        "country": adapter._clean_country(destination.get("country") or "GB"),
        "phone": destination.get("phone") or "",
        "email": destination.get("email") or "",
    }

    custom_reference = str(getattr(order, "marketplace_order_id", ""))[:50]
    draft_attempt_id = f"{custom_reference}:bt38:{os.urandom(6).hex()}"[:50]
    body = {
        "user_id": (account or {}).get("id") if isinstance(account, dict) else None,
        "client_id": (account or {}).get("client_id") if isinstance(account, dict) else None,
        "platform": PACKLINK_PLATFORM,
        "platform_country": platform_country,
        "source": PACKLINK_DRAFT_SOURCE,
        "from": from_address,
        "to": to_address,
        "service": rate.get("service_name") or rate.get("service") or "",
        "carrier": rate.get("carrier_name") or rate.get("carrier") or "",
        "service_id": int(service_id) if service_id.isdigit() else service_id,
        "packages": [{
            "width": int(round(float(parcel["width_cm"]))),
            "height": int(round(float(parcel["height_cm"]))),
            "length": int(round(float(parcel["length_cm"]))),
            "weight": round(float(parcel["weight_kg"]), 2),
        }],
        "content": ", ".join(content_parts)[:60] or "Goods",
        "contentvalue": round(content_value, 2),
        "content_second_hand": False,
        "shipment_custom_reference": custom_reference,
        "priority": False,
        "contentValue_currency": "GBP",
        "has_customs": False,
        "additional_data": {
            "shipping_service_name": rate.get("service_name") or rate.get("service") or None,
            "selectedWarehouseId": None,
            "parcel_Ids": [],
            "postal_zone_name_from": "United Kingdom" if from_address["country"] == "GB" else from_address.get("state"),
            "postal_zone_name_to": "United Kingdom" if to_address["country"] == "GB" else to_address.get("state"),
            "order_id": draft_attempt_id,
            "seller_user_id": None,
            "items": items,
        },
    }

    payload = adapter._post_json("shipments", body)
    reference = ""
    if isinstance(payload, dict):
        reference = str(payload.get("shipment_reference") or payload.get("reference") or "").strip()
    if not reference:
        raise PacklinkRequestError("Packlink created no shipment reference.")

    # Keep the exact browser-shaped Save sequence that was already proven.
    snapshot = adapter.get_shipment(reference)
    if not isinstance(snapshot, dict) or not snapshot:
        raise PacklinkRequestError(
            f"Packlink shipment {reference} was created but could not be read before browser-aligned save."
        )
    save_body = _browser_save_body(snapshot, reference)
    if not save_body.get("from") or not save_body.get("to") or not save_body.get("packages"):
        raise PacklinkRequestError(
            f"Packlink shipment {reference} did not expose enough provider data for browser-aligned save."
        )
    adapter._put_json(f"shipments/{reference}", save_body)
    snapshot = adapter.get_shipment(reference)
    blockers = adapter.draft_blockers(snapshot)
    ready = adapter._provider_ready_to_ship(snapshot)
    state = _provider_state(snapshot)
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

    return {
        "reference": reference,
        "payment_status": "pending_packlink_payment",
        "label_ready": False,
        "raw": payload,
        "verified": True,
        "provider_state": state,
        "provider_missing_fields": [],
        "provider_saved_complete": True,
        "provider_auto_saved": True,
        "location_selector_fallback": True,
    }


def install_packlink_optional_location_fallback() -> None:
    current = PacklinkAdapter.create_shipment_draft
    if getattr(current, "_bt38_optional_location_fallback", False):
        return

    @wraps(current)
    def aligned_create(self, *, order, parcel, rate):
        try:
            return current(self, order=order, parcel=parcel, rate=rate)
        except PacklinkRequestError as exc:
            if not _optional_selector_failure(exc):
                raise
            # The selector exception is raised before POST /shipments, so this
            # continuation cannot duplicate an already-created provider draft.
            return _create_without_optional_selector_ids(
                self,
                order=order,
                parcel=parcel,
                rate=rate,
            )

    aligned_create._bt38_optional_location_fallback = True
    PacklinkAdapter.create_shipment_draft = aligned_create


install_packlink_optional_location_fallback()
