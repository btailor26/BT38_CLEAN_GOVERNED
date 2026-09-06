from pathlib import Path
from types import SimpleNamespace

from services.fbm_parcel_grouping import (
    canonical_items,
    combination_key,
    persisted_address_key,
    same_persisted_address,
)


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_fbm_parcel_grouping_alignment.py"
MODELS = ROOT / "fbm_parcel_models.py"


def _line(sku, quantity):
    return SimpleNamespace(sku=sku, quantity=quantity, store_id=None, marketplace_order_id=None)


def _address_order(order_id, *, address="1 Test Road"):
    return SimpleNamespace(
        id=order_id,
        store_id=None,
        marketplace_order_id=None,
        ship_to_name="Test Customer",
        ship_to_address=address,
        ship_to_city="Leicester",
        ship_to_postcode="LE1 1AA",
        ship_to_country="GB",
    )


def test_combination_identity_is_quantity_aware_and_order_independent():
    first = [_line("SKU-B", 1), _line("SKU-A", 2)]
    second = [_line("SKU-A", 2), _line("SKU-B", 1)]
    assert canonical_items(first) == [
        {"sku": "SKU-A", "quantity": 2},
        {"sku": "SKU-B", "quantity": 1},
    ]
    assert combination_key(first) == combination_key(second)


def test_same_address_requires_persisted_exact_recipient_identity():
    one = _address_order(1)
    two = _address_order(2)
    three = _address_order(3, address="2 Other Road")
    assert persisted_address_key(one) == persisted_address_key(two)
    assert same_persisted_address([one, two]) is True
    assert same_persisted_address([one, three]) is False


def test_grouping_alignment_keeps_shipping_options_db_only():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert "get_or_refresh_amazon_profile" not in source
    assert "hydrate_exact_ebay_order" not in source
    assert "PacklinkAdapter" not in source
    assert "AmazonShippingAdapter" not in source
    assert "provider_call_made\": False" in source
    assert "final label purchase/print" in source


def test_grouping_uses_one_existing_physical_shipment_authority():
    source = MODELS.read_text(encoding="utf-8")
    assert "class FBMShipmentOrderLink" in source
    assert 'db.ForeignKey("fbm_shipments.id"' in source
    assert "does not create another shipment" in source
    assert "class FBMParcelCombinationMapping" in source
