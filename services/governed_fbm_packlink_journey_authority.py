"""Keep existing Packlink tracking on the existing FBM journey path.

The lifecycle alignment already uses deterministic purchase keys as BT38
shipment-source proof. Older persisted Packlink rows can predate that key while
still carrying the provider shipment id and tracking number returned by Packlink.
Those two persisted provider facts are sufficient to keep that same shipment on
the existing Packlink journey renderer.

No polling, provider read, shipment creation, marketplace write or second journey
path is introduced here.
"""
from __future__ import annotations

from services import governed_fbm_lifecycle_alignment as lifecycle


_original_bt38_owns_shipment = lifecycle.bt38_owns_shipment


def _aligned_bt38_owns_shipment(shipment) -> bool:
    if _original_bt38_owns_shipment(shipment):
        return True
    if shipment is None:
        return False
    provider = lifecycle._status(getattr(shipment, "provider", None))
    if provider != "packlink":
        return False
    provider_shipment_id = str(
        getattr(shipment, "provider_shipment_id", None) or ""
    ).strip()
    tracking_number = str(
        getattr(shipment, "tracking_number", None) or ""
    ).strip()
    return bool(provider_shipment_id and tracking_number)


lifecycle.bt38_owns_shipment = _aligned_bt38_owns_shipment
