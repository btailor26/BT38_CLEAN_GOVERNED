from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GROUPING = ROOT / "services" / "fbm_parcel_grouping.py"
SHARED = ROOT / "services" / "fbm_shared_shipment_confirmation.py"
ALIGNMENT = ROOT / "services" / "governed_fbm_shared_shipment_confirmation_alignment.py"


def test_consolidation_fails_closed_across_marketplaces():
    source = GROUPING.read_text(encoding="utf-8")
    assert "mixed_marketplaces_require_separate_shipments" in source
    assert "prime_sfp_cannot_share_external_parcel" in source


def test_shared_confirmation_reuses_one_physical_tracking_authority():
    source = SHARED.read_text(encoding="utf-8")
    assert "FBMShipmentOrderLink" in source
    assert "shipment.tracking_number" in source
    assert "client.confirm_shipment" in source
    assert "_confirm_ebay_external" in source
    assert "marketplace_confirmed_at" in source
    assert "purchase" not in source.lower().replace("postage", "")


def test_existing_confirmation_path_is_extended_not_replaced():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert "original = confirmation.confirm_external_shipment" in source
    assert "result = original(shipment=shipment, mapping=mapping)" in source
    assert "confirm_linked_external_orders" in source
    assert "post_purchase.confirm_external_shipment = confirm_with_linked_orders" in source
    assert "amazon_buy_shipping" in source
