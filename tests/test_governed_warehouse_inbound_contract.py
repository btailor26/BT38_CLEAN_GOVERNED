from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_warehouse_inbound_alignment.py"
APP = ROOT / "app.py"


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


def test_alignment_not_silently_claimed_installed_before_app_registration():
    source = APP.read_text(encoding="utf-8")

    # Until app.py explicitly installs this module, deployment must not claim
    # the new Warehouse endpoints are live. This contract makes the remaining
    # registration boundary visible instead of falling back to legacy mobile
    # or retired PO routes.
    assert "install_governed_warehouse_inbound_alignment(app)" not in source
