"""Read-only Packlink PRO adapter for BT38 FBM.

This module deliberately enables only authenticated reads at this stage.
It does not create shipments, purchase labels, dispatch marketplace orders,
write tracking, alter inventory, or import marketplace orders.

Packlink's own ecommerce integration core uses:
- https://api.packlink.com/v1/
- Authorization: <Packlink PRO API key>
- GET /clients for account data
- GET /services for service/rate discovery
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.fbm_provider_contract import ProviderCapabilities


PACKLINK_BASE_URL = "https://api.packlink.com/v1/"
PACKLINK_TIMEOUT_SECONDS = 12


class PacklinkConfigurationError(RuntimeError):
    pass


class PacklinkRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PacklinkConnectionResult:
    ok: bool
    configured: bool
    status_code: int | None
    account_country: str | None = None
    account_email: str | None = None
    message: str | None = None


class PacklinkAdapter:
    """Packlink PRO provider adapter with writes hard-disabled for now."""

    capabilities = ProviderCapabilities(
        provider="packlink",
        quotes=True,
        label_purchase=False,
        tracking_status=False,
        case_opening=False,
        return_labels=False,
    )

    def __init__(self, api_key: str | None = None):
        self._api_key = (api_key or os.environ.get("PACKLINK_API_KEY") or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise PacklinkConfigurationError("PACKLINK_API_KEY is not configured.")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._api_key,
            "User-Agent": "BT38-FBM/1.0",
        }

    def _get_json(self, endpoint: str, *, query: list[tuple[str, str]] | None = None) -> Any:
        url = PACKLINK_BASE_URL + endpoint.lstrip("/")
        if query:
            url += "?" + urlencode(query)

        request = Request(url=url, method="GET", headers=self._headers())
        try:
            with urlopen(request, timeout=PACKLINK_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200) or 200)
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            message = "Packlink request failed."
            try:
                payload = json.loads(body) if body else {}
                if isinstance(payload, dict):
                    message = str(payload.get("message") or message)
            except Exception:
                pass
            raise PacklinkRequestError(message, status_code=exc.code) from exc
        except URLError as exc:
            raise PacklinkRequestError("Packlink could not be reached.") from exc

        if status < 200 or status >= 300:
            raise PacklinkRequestError("Packlink request failed.", status_code=status)

        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PacklinkRequestError("Packlink returned an invalid JSON response.", status_code=status) from exc

    def connection_check(self) -> PacklinkConnectionResult:
        """Validate the Fly secret using Packlink's read-only client endpoint."""
        if not self.configured:
            return PacklinkConnectionResult(
                ok=False,
                configured=False,
                status_code=None,
                message="PACKLINK_API_KEY is not configured.",
            )

        try:
            payload = self._get_json("clients")
        except PacklinkRequestError as exc:
            return PacklinkConnectionResult(
                ok=False,
                configured=True,
                status_code=exc.status_code,
                message=str(exc),
            )

        account_country = None
        account_email = None
        if isinstance(payload, dict):
            account_country = str(
                payload.get("country")
                or payload.get("platform_country")
                or ""
            ).strip() or None
            account_email = str(payload.get("email") or "").strip() or None

        return PacklinkConnectionResult(
            ok=True,
            configured=True,
            status_code=200,
            account_country=account_country,
            account_email=account_email,
            message="Packlink PRO authentication succeeded.",
        )

    def get_rates(self, *, order: Any, parcel: dict) -> list[dict]:
        """Read Packlink services without creating or purchasing a shipment.

        The caller must supply explicit routing data in parcel. This avoids
        guessing order/address field names while the FBM order mapper is still
        being audited.
        """
        required = (
            "from_country",
            "from_zip",
            "to_country",
            "to_zip",
            "width_cm",
            "height_cm",
            "length_cm",
            "weight_kg",
        )
        missing = [name for name in required if parcel.get(name) in (None, "")]
        if missing:
            raise PacklinkConfigurationError(
                "Missing Packlink rate fields: " + ", ".join(missing)
            )

        query = [
            ("from[country]", str(parcel["from_country"])),
            ("from[zip]", str(parcel["from_zip"])),
            ("to[country]", str(parcel["to_country"])),
            ("to[zip]", str(parcel["to_zip"])),
            ("packages[0][width]", str(parcel["width_cm"])),
            ("packages[0][height]", str(parcel["height_cm"])),
            ("packages[0][length]", str(parcel["length_cm"])),
            ("packages[0][weight]", str(parcel["weight_kg"])),
        ]
        payload = self._get_json("services", query=query)
        return payload if isinstance(payload, list) else []

    def purchase_label(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink label purchase is not enabled yet.")

    def get_tracking_status(self, **_: Any):
        raise NotImplementedError("Packlink tracking reads are not enabled yet.")

    def open_case(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink case opening is not enabled yet.")

    def create_return_label(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink return labels are not enabled yet.")
