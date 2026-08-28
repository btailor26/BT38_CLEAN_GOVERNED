"""Live FBM feed alignment guards.

MarketplaceOrder remains the source of truth. This module only makes provider
handoffs refresh the exact live marketplace order first; it never creates an
order, buys postage, dispatches, or mutates inventory.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _gb_postcode(value: Any) -> str:
    """Return Packlink-friendly UK postcode formatting without changing DB truth."""
    compact = re.sub(r"\s+", "", str(value or "").strip().upper())
    if len(compact) >= 5 and re.fullmatch(r"[A-Z0-9]+", compact):
        return compact[:-3] + " " + compact[-3:]
    return str(value or "").strip().upper()


def _provider_error_message(raw: str, status_code: int | None) -> str:
    """Expose Packlink validation detail while keeping secrets/request data out."""
    fallback = "Packlink request failed."
    try:
        payload = json.loads(raw) if raw else None
    except Exception:
        payload = None

    messages: list[str] = []

    def collect(value: Any, key: str = "") -> None:
        if value in (None, "", [], {}):
            return
        if isinstance(value, str):
            text = " ".join(value.split()).strip()
            if text and text.lower() not in {"error", "errors"}:
                messages.append(f"{key}: {text}" if key and key not in {"message", "error", "detail"} else text)
            return
        if isinstance(value, (int, float, bool)):
            if key:
                messages.append(f"{key}: {value}")
            return
        if isinstance(value, list):
            for item in value:
                collect(item, key)
            return
        if isinstance(value, dict):
            priority = ("message", "detail", "error", "errors", "non_field_errors")
            seen = set()
            for name in priority:
                if name in value:
                    seen.add(name)
                    collect(value.get(name), name)
            for name, item in value.items():
                if name not in seen:
                    collect(item, str(name))

    collect(payload)
    unique: list[str] = []
    for message in messages:
        if message not in unique:
            unique.append(message)
    detail = " · ".join(unique[:5])
    if detail:
        prefix = f"Packlink HTTP {status_code}" if status_code else "Packlink"
        return f"{prefix}: {detail}"[:1000]
    return f"Packlink HTTP {status_code}: request failed." if status_code else fallback


def _install_packlink_request_alignment(packlink) -> None:
    """Use body-appropriate headers and preserve Packlink validation responses."""
    current = packlink.PacklinkAdapter._request_json
    if getattr(current, "_bt38_provider_error_alignment", False):
        return

    def aligned_request_json(self, method: str, endpoint: str, *, query=None, body=None):
        url = packlink.PACKLINK_BASE_URL + endpoint.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(self._headers())
        if data is None:
            # Packlink's GET services contract is a query-string request. Do not
            # advertise a JSON request body when none exists.
            headers.pop("Content-Type", None)
        request = Request(url=url, method=method.upper(), headers=headers, data=data)
        try:
            with urlopen(request, timeout=packlink.PACKLINK_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200) or 200)
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise packlink.PacklinkRequestError(
                _provider_error_message(raw, exc.code),
                status_code=exc.code,
            ) from exc
        except URLError as exc:
            raise packlink.PacklinkRequestError("Packlink could not be reached.") from exc
        if status < 200 or status >= 300:
            raise packlink.PacklinkRequestError(
                _provider_error_message(raw, status),
                status_code=status,
            )
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise packlink.PacklinkRequestError(
                "Packlink returned an invalid JSON response.",
                status_code=status,
            ) from exc

    aligned_request_json._bt38_provider_error_alignment = True
    packlink.PacklinkAdapter._request_json = aligned_request_json


def install_live_packlink_alignment() -> None:
    """Force exact eBay hydration before every Packlink quote request.

    This is deliberately at the provider boundary, not the page render path.
    It means old and future eBay orders use the canonical Fulfillment API line
    identity and current buyer destination before Packlink sees the order.
    Safe legacy aliases are collapsed by hydrate_exact_ebay_order, preventing
    duplicate quantities from leaking into the shipment handoff.
    """
    from services import fbm_packlink_adapter as packlink

    _install_packlink_request_alignment(packlink)

    current = packlink.PacklinkAdapter.get_rates
    if getattr(current, "_bt38_live_ebay_alignment", False):
        return

    def aligned_get_rates(self, *, order: Any, parcel: dict):
        store = getattr(order, "store", None)
        platform = str(getattr(store, "platform", "") or "").strip().lower()
        order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
        if store is not None and order_id and "ebay" in platform:
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

            result = hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=order_id,
                source="packlink_live_handoff",
            )
            if not result.get("success"):
                reason = str(result.get("reason") or "exact_ebay_order_hydration_failed")
                detail = str(result.get("error") or "").strip()
                message = f"eBay live order refresh failed: {reason}"
                if detail:
                    message += f" · {detail[:500]}"
                raise packlink.PacklinkRequestError(message)

            # Hydration may have removed a stale alias row. Re-resolve the
            # surviving canonical row so Packlink never continues with a
            # deleted SQLAlchemy object selected by an older UI request.
            from models import MarketplaceOrder

            canonical = (
                MarketplaceOrder.query
                .filter_by(store_id=store.id, marketplace_order_id=order_id)
                .order_by(MarketplaceOrder.id.asc())
                .first()
            )
            if canonical is None:
                raise packlink.PacklinkRequestError("eBay live order disappeared during exact refresh.")
            order = canonical

            # Rebuild the parcel after canonicalisation. This is important for
            # order-level weight/quantity calculations and keeps the provider
            # handoff aligned with the surviving live order rows.
            from services.fbm_order_mapper import provider_parcel

            entered = {
                key: parcel.get(key)
                for key in ("weight_kg", "length_cm", "width_cm", "height_cm")
                if parcel.get(key) not in (None, "")
            }
            parcel = provider_parcel(order, entered)

        # Packlink accepts UK postcodes in canonical spaced form. Normalize only
        # the provider-bound copy; persisted marketplace/customer data is unchanged.
        parcel = dict(parcel or {})
        if str(parcel.get("from_country") or "").upper() == "GB":
            parcel["from_zip"] = _gb_postcode(parcel.get("from_zip"))
        if str(parcel.get("to_country") or "").upper() == "GB":
            parcel["to_zip"] = _gb_postcode(parcel.get("to_zip"))

        return current(self, order=order, parcel=parcel)

    aligned_get_rates._bt38_live_ebay_alignment = True
    aligned_get_rates.__name__ = current.__name__
    aligned_get_rates.__doc__ = current.__doc__
    packlink.PacklinkAdapter.get_rates = aligned_get_rates


install_live_packlink_alignment()
