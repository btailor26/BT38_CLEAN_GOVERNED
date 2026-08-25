from pathlib import Path


IMPORTER = Path("services/governed_ebay_inventory_import.py")


def _source() -> str:
    return IMPORTER.read_text(encoding="utf-8")


def test_missing_sku_recovery_is_aligned_into_existing_importer():
    source = _source()

    assert "from services.governed_ebay_sku_recovery import (" in source
    assert "ensure_single_listing_sku" in source
    assert "ensure_variation_sku" in source
    assert "sku, variation, _verified_item, _recovered = ensure_variation_sku(" in source
    assert "parent_sku, detail, _recovered = ensure_single_listing_sku(" in source

    # Missing eBay SKUs must no longer disappear or masquerade as ItemIDs.
    assert "if not sku:\n                continue" not in source
    assert 'parent_sku = _xml_text(detail, "{*}SKU") or item_id' not in source


def test_existing_seller_sku_stays_on_existing_import_path():
    source = _source()

    # Recovery is conditional only. Existing SKUs still flow through the same
    # stock/listing upsert path and are never replaced by recovery.
    assert 'sku = _xml_text(variation, "{*}SKU")' in source
    assert "if not sku:" in source
    assert "stock = _find_or_create_stock(sku, title)" in source
    assert "listing = _upsert_listing(" in source


def test_legacy_collapsed_parent_is_retired_not_deleted_or_relinked():
    source = _source()

    assert "def _retire_collapsed_sku_less_parent" in source
    assert "row.is_active = False" in source
    assert "row.warehouse_stock_id =" not in source.split(
        "def _retire_collapsed_sku_less_parent", 1
    )[1].split("def _import_item", 1)[0]
    assert "row.master_product_group_id =" not in source.split(
        "def _retire_collapsed_sku_less_parent", 1
    )[1].split("def _import_item", 1)[0]
