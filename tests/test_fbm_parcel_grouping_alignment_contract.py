from pathlib import Path
from types import SimpleNamespace

import services.fbm_parcel_grouping as grouping


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_fbm_parcel_grouping_alignment.py"
MODELS = ROOT / "fbm_parcel_models.py"
TEMPLATE = ROOT / "templates" / "fbm.html"


def _order(order_id, *, address="1 Test Road"):
    return SimpleNamespace(
        id=order_id,
        store_id=1,
        marketplace_order_id=f"ORDER-{order_id}",
        ship_to_name="Test Customer",
        ship_to_address=address,
        ship_to_city="Leicester",
        ship_to_postcode="LE1 1AA",
        ship_to_country="GB",
    )


def test_combination_identity_is_quantity_aware_and_order_independent(monkeypatch):
    one = _order(1)
    two = _order(2)
    lines = {
        "ORDER-1": [SimpleNamespace(sku="SKU-B", quantity=1)],
        "ORDER-2": [SimpleNamespace(sku="SKU-A", quantity=2)],
    }
    monkeypatch.setattr(grouping, "order_lines", lambda order: lines[order.marketplace_order_id])

    assert grouping.canonical_items([one, two]) == [
        {"sku": "SKU-A", "quantity": 2},
        {"sku": "SKU-B", "quantity": 1},
    ]
    assert grouping.combination_key([one, two]) == grouping.combination_key([two, one])


def test_same_address_requires_persisted_exact_recipient_identity():
    one = _order(1)
    two = _order(2)
    three = _order(3, address="2 Other Road")
    assert grouping.persisted_address_key(one) == grouping.persisted_address_key(two)
    assert grouping.same_persisted_address([one, two]) is True
    assert grouping.same_persisted_address([one, three]) is False


def test_grouping_alignment_keeps_shipping_options_db_only():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert "get_or_refresh_amazon_profile" not in source
    assert "hydrate_exact_ebay_order" not in source
    assert "PacklinkAdapter" not in source
    assert "AmazonShippingAdapter" not in source
    assert '"provider_call_made": False' in source
    assert "final label purchase/print" in source


def test_grouping_uses_one_existing_physical_shipment_authority():
    source = MODELS.read_text(encoding="utf-8")
    assert "class FBMShipmentOrderLink" in source
    assert 'db.ForeignKey("fbm_shipments.id"' in source
    assert "does not create another shipment" in source
    assert "class FBMParcelCombinationMapping" in source
    assert "marketplace_confirmed_at" in source
    assert "marketplace_confirmation_status" in source
    assert "marketplace_confirmation_error" in source


def test_explicit_pack_together_link_is_confirmation_only_and_provider_free():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert '/fbm/packing/link-shipment' in source
    assert 'confirm_pack_together' in source
    assert 'PACK_TOGETHER' in source
    assert 'link_orders_to_existing_shipment' in source
    assert 'shipment_identity not in identities' in source
    assert 'Marketplace orders remain separate' in source
    assert 'provider_call_made": False' in source


def test_ready_to_ship_ui_offers_one_box_and_reuses_mapping_review():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "Pack together" in source
    assert "Check 1 parcel" in source
    assert "/fbm/packing/preview" in source
    assert "/fbm/packing/mapping" in source
    assert "SAVE_PACK_MAPPING" in source
    assert "Mapping review: confirm the packed dimensions once" in source
    assert "Pack these orders together in 1 box / 1 shipment" in source


def test_grouped_label_flow_links_only_after_physical_shipment_exists():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "/fbm/packing/link-shipment" in source
    assert "confirm_pack_together:'PACK_TOGETHER'" in source
    assert "shipment_id:p.shipment_id" in source
    assert "Buy one label from the primary order only" in source
    assert "Confirming live service immediately before label purchase" in source


def test_secondary_linked_order_cannot_buy_second_original_label():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert "_install_secondary_purchase_guards" in source
    assert "governed_fbm.amazon_purchase" in source
    assert "governed_fbm.packlink_create_draft" in source
    assert "governed_fbm.manual_dispatch" in source
    assert "duplicate_postage_blocked" in source
    assert "already packed inside an existing shared physical shipment" in source
    assert "shipment_purpose" in source
    assert '"return", "replacement"' in source


def test_late_manual_link_can_release_secondary_confirmation_without_repurchase():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert "_release_already_confirmed_shared_shipment" in source
    assert "confirm_linked_external_orders" in source
    assert "shipment.marketplace_confirmed_at" in source
    assert "amazon_buy_shipping" in source


def test_amazon_native_label_cannot_be_linked_to_multiple_orders_server_side():
    source = ALIGNMENT.read_text(encoding="utf-8")
    assert 'str(shipment.provider or "").strip().lower() == "amazon_buy_shipping"' in source
    assert "amazon_native_shared_parcel_blocked" in source
    assert "cannot be shared across packed-together orders" in source
    assert "Use an eligible external/manual shipment" in source
