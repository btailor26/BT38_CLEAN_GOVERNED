from pathlib import Path
from types import SimpleNamespace

from services.fbm_shipping_state import shipment_confirmation_state


TEMPLATE = Path("templates/fbm.html").read_text(encoding="utf-8")


def test_fbm_page_shows_three_step_journey_for_marketplace_shipments_without_numbers():
    assert ">Picked up</span>" in TEMPLATE
    assert ">In transit</span>" in TEMPLATE
    assert ">Delivered</span>" in TEMPLATE
    assert "1 · Picked up" not in TEMPLATE
    assert "2 · In transit" not in TEMPLATE
    assert "3 · Delivered" not in TEMPLATE
    assert "sellercentral.amazon.co.uk/orders-v3/order/" in TEMPLATE
    assert "ebay.co.uk/mesh/ord/details?orderid=" in TEMPLATE


def test_mapping_verified_is_not_a_customer_facing_fbm_badge():
    assert "Mapping verified" not in TEMPLATE
    assert "Mapping under review" in TEMPLATE


def test_pickup_and_delivery_require_real_provider_milestones():
    label_only = SimpleNamespace(
        delivered_at=None,
        first_movement_at=None,
        carrier_accepted_at=None,
        handover_due_at=None,
        label_purchased_at=object(),
    )
    accepted = SimpleNamespace(
        delivered_at=None,
        first_movement_at=None,
        carrier_accepted_at=object(),
        handover_due_at=None,
        label_purchased_at=object(),
    )
    moving = SimpleNamespace(
        delivered_at=None,
        first_movement_at=object(),
        carrier_accepted_at=object(),
        handover_due_at=None,
        label_purchased_at=object(),
    )
    delivered = SimpleNamespace(
        delivered_at=object(),
        first_movement_at=object(),
        carrier_accepted_at=object(),
        handover_due_at=None,
        label_purchased_at=object(),
    )

    assert shipment_confirmation_state(label_only) == "awaiting_carrier_acceptance"
    assert shipment_confirmation_state(accepted) == "accepted"
    assert shipment_confirmation_state(moving) == "in_transit"
    assert shipment_confirmation_state(delivered) == "delivered"
