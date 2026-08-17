"""Governed contract for FBM shipping providers.

Provider adapters may quote services, buy labels on the seller's own account,
read tracking/acceptance state and open support cases where the provider exposes
that capability. They must not import marketplace orders, mutate inventory,
change Product Linking relationships or bypass marketplace-specific shipping
rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    quotes: bool = False
    label_purchase: bool = False
    tracking_status: bool = False
    case_opening: bool = False
    return_labels: bool = False


@dataclass(frozen=True)
class ProviderTrackingResult:
    provider: str
    provider_shipment_id: str | None
    carrier: str | None
    service: str | None
    tracking_number: str | None
    raw_status: str | None
    canonical_event: str | None
    occurred_at: Any = None
    delivered: bool = False


class FBMProviderAdapter(Protocol):
    """Interface each marketplace/carrier/provider adapter must satisfy."""

    capabilities: ProviderCapabilities

    def get_rates(self, *, order: Any, parcel: dict) -> list[dict]:
        """Read eligible services/rates. Must not purchase anything."""
        ...

    def purchase_label(self, *, order: Any, service: dict, parcel: dict) -> dict:
        """Purchase postage on the seller's own provider/marketplace account."""
        ...

    def get_tracking_status(self, *, shipment: Any) -> ProviderTrackingResult:
        """Read provider truth for carrier acceptance/movement."""
        ...

    def open_case(self, *, shipment: Any, case_type: str, reason: str) -> dict:
        """Open provider support case if the provider offers a supported API."""
        ...

    def create_return_label(self, *, order: Any, parcel: dict, service: dict | None = None) -> dict:
        """Create a return label on the seller's own provider account."""
        ...


def provider_case_mode(capabilities: ProviderCapabilities) -> str:
    """Tell the UI how an eligible provider case can be handled."""
    if capabilities.case_opening:
        return "api"
    return "provider_portal"
