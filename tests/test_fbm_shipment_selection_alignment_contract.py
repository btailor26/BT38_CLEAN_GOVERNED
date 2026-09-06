from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_fbm_shipment_selection_alignment.py"
MAIN = ROOT / "main.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_selector_keeps_existing_fbm_shipment_table_only():
    source = _text(ALIGNMENT)
    assert "from fbm_models import FBMShipment" in source
    assert "CREATE TABLE" not in source
    assert "requests." not in source
    assert "AmazonShippingAdapter" not in source
    assert "PacklinkAdapter" not in source


def test_original_outbound_precedes_return_or_replacement():
    source = _text(ALIGNMENT)
    assert '"packlink_return:"' in source
    assert '"packlink_replacement:"' in source
    assert "1 if not additional else 0" in source


def test_purchased_physical_authority_precedes_exact_marketplace_tracking():
    source = _text(ALIGNMENT)
    purchased_pos = source.index("1 if purchased else 0")
    tracking_pos = source.index("1 if exact_tracking_match else 0")
    assert purchased_pos < tracking_pos
    assert 'provider not in {"", "marketplace"}' in source
    assert 'purchase_status == "purchased"' in source
    assert 'getattr(shipment, "label_purchased_at", None) is not None' in source


def test_installer_rebinds_route_and_page_selector():
    source = _text(ALIGNMENT)
    assert "routes._shipment_map = _aligned_shipment_map" in source
    assert "page_alignment._shipment_map = _aligned_shipment_map" in source


def test_runtime_installs_shipment_selection_alignment():
    source = _text(MAIN)
    assert "install_governed_fbm_shipment_selection_alignment" in source
