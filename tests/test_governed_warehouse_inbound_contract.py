from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_warehouse_inbound_alignment.py"
INSTALLER = ROOT / "services" / "governed_warehouse_inbound_installer.py"
MAIN = ROOT / "main.py"


def test_warehouse_inbound_alignment_is_read_only_and_governed():
    source = ALIGNMENT.read_text(encoding="utf-8")

    assert '"/governed/warehouse/expected-inbound"' in source
    assert '"/governed/warehouse/scan/<path:identity>"' in source
    assert source.count("@login_required") >= 2
    assert '"read_only": True' in source
    assert "db.session.commit" not in source
    assert "db.session.add" not in source
    assert "/api/mobile/adjust" not in source
    assert "/api/mobile/bulk-adjust" not in source


def test_scan_resolution_keeps_identity_separate_from_quantity_mutation():
    source = ALIGNMENT.read_text(encoding="utf-8")

    assert "WarehouseStock.barcode == value" in source
    assert "MarketplaceListing.fnsku == value" in source
    assert "AmazonFBAListing.fnsku == value" in source
    assert "ProductPackMapping.master_barcode == value" in source
    assert 'identity_type = "master_carton"' in source
    assert "units_per_scan" in source
    assert "available_quantity" in source
    assert "on_order_quantity" in source
    assert "No stock has been changed" in source


def test_alignment_is_registered_through_existing_main_startup_path():
    installer = INSTALLER.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")

    assert "install_governed_warehouse_inbound_alignment(app)" in installer
    assert "from services.governed_warehouse_inbound_installer import" in main
    assert "install_governed_warehouse_inbound(app)" in main
