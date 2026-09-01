from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_label_reads_only_persisted_order_and_shipment():
    source = (ROOT / "services" / "governed_sds_label_alignment.py").read_text(encoding="utf-8")
    assert "db.session.get(FBMShipment, shipment_id)" in source
    assert "MarketplaceOrder.query" in source
    assert "hydrate_marketplace_destination" not in source
    assert "ship_to(" not in source
    assert "db.session.commit" not in source
    assert "guard_marketplace_write" not in source


def test_sds_label_qr_encodes_only_exact_persisted_sds_reference():
    source = (ROOT / "services" / "governed_sds_label_alignment.py").read_text(encoding="utf-8")
    assert 'reference.upper() != f"SDS-{shipment.id:010d}"' in source
    assert "cv2.QRCodeEncoder_create()" in source
    assert "encoder.encode(reference)" in source
    assert '"scan_value": shipment.provider_shipment_id' in source


def test_sds_qr_uses_existing_locked_opencv_dependency():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    source = (ROOT / "services" / "governed_sds_label_alignment.py").read_text(encoding="utf-8")
    assert '"opencv-python-headless>=4.11.0.86"' in project
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "import cv2" in source
    assert "qrcode" not in project.lower()


def test_sds_label_is_installed_between_dispatch_and_scan_authority():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    dispatch = source.index("install_governed_sds_dispatch_alignment(app)")
    label = source.index("install_governed_sds_label_alignment(app)")
    scan = source.index("install_governed_sds_scan_alignment(app)")
    assert dispatch < label < scan


def test_printable_label_uses_same_reference_for_visible_and_qr_identity():
    template = (ROOT / "templates" / "fbm_sds_label.html").read_text(encoding="utf-8")
    assert "{{ label.sds_reference }}" in template
    assert "{{ label.scan_value }}" in template
    assert "bt38_sds_label_qr" in template
    assert "does not itself mark the parcel dispatched or delivered" in template
