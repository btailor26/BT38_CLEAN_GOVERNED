from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAPPER = ROOT / "services" / "fbm_order_mapper.py"


def test_fbm_parcel_preparation_is_db_only():
    source = MAPPER.read_text(encoding="utf-8")
    assert "hydrate_marketplace_destination" not in source
    assert "get_or_refresh_amazon_profile" not in source
    assert "hydrate_exact_ebay_order" not in source
    assert "Return only delivery facts already persisted on MarketplaceOrder" in source


def test_single_sku_quantity_uses_exact_existing_pack_mapping():
    source = MAPPER.read_text(encoding="utf-8")
    assert "filter_by(single_sku=sku, units_per_carton=quantity, is_active=True)" in source
    assert "units_per_carton=quantity" in source
    assert "mapping_review_required" in source


def test_weight_is_quantity_aware_without_guessing_dimensions():
    source = MAPPER.read_text(encoding="utf-8")
    assert "total_weight += unit_weight * quantity" in source
    assert "Dimensions are never multiplied" in source
    assert 'sources.extend(("multi_item_dimensions_required", "mapping_review_required"))' in source
