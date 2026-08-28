from pathlib import Path


AMAZON = Path("services/fbm_amazon_order_profile.py").read_text(encoding="utf-8")
EBAY = Path("services/governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")
MAPPER = Path("services/fbm_order_mapper.py").read_text(encoding="utf-8")


def test_amazon_exact_order_persists_customer_delivery_promise():
    assert "EarliestDeliveryDate" in AMAZON
    assert "LatestDeliveryDate" in AMAZON
    assert "update_marketplace_facts" in AMAZON
    assert "earliest_delivery_at" in AMAZON
    assert "latest_delivery_at" in AMAZON


def test_ebay_exact_order_persists_shipping_service_and_delivery_window():
    assert "update_marketplace_facts" in EBAY
    assert "shipping_service" in EBAY
    assert "earliest_delivery_at" in EBAY
    assert "latest_delivery_at" in EBAY
    assert "fulfillmentStartInstructions" in EBAY


def test_mapper_reads_and_saves_order_level_parcel_values():
    assert "saved_order_parcel" in MAPPER
    assert "save_order_parcel" in MAPPER
    assert "weight_kg" in MAPPER
    assert "length_cm" in MAPPER
    assert "width_cm" in MAPPER
    assert "height_cm" in MAPPER
