from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_existing_mobile_scanner_remains_stock_authority_for_non_sds_codes():
    base = (ROOT / "static" / "js" / "mobile-scanner.js").read_text(encoding="utf-8")
    alignment = (ROOT / "static" / "js" / "mobile-sds-scanner-alignment.js").read_text(encoding="utf-8")
    assert "MobileScanner.prototype.handleBarcodeScan" in alignment
    assert "return originalHandleBarcodeScan.call(this, barcode);" in alignment
    assert "/api/mobile/sku/" in base
    assert "/api/mobile/adjust" in base


def test_existing_camera_detector_is_extended_with_qr_not_replaced():
    alignment = (ROOT / "static" / "js" / "mobile-sds-scanner-alignment.js").read_text(encoding="utf-8")
    for barcode_format in ("ean_13", "ean_8", "code_128", "code_39", "upc_a", "upc_e"):
        assert barcode_format in alignment
    assert "qr_code" in alignment
    assert r"/^SDS-\d{10}$/" in alignment


def test_sds_scanner_resolves_persisted_shipment_before_explicit_event_write():
    resolver = (ROOT / "services" / "governed_sds_scanner_lookup_alignment.py").read_text(encoding="utf-8")
    alignment = (ROOT / "static" / "js" / "mobile-sds-scanner-alignment.js").read_text(encoding="utf-8")
    assert 'provider_shipment_id=normalised' in resolver
    assert '"read_only": True' in resolver
    assert "window.confirm" in alignment
    assert "confirm_scan:" in alignment
    assert "/sds/scan" in alignment


def test_mobile_scanner_page_loads_sds_extension_after_existing_scanner():
    template = (ROOT / "templates" / "mobile_scan.html").read_text(encoding="utf-8")
    base = '/static/js/mobile-scanner.js'
    sds = '/static/js/mobile-sds-scanner-alignment.js'
    assert base in template and sds in template
    assert template.index(base) < template.index(sds)


def test_scanner_service_worker_cache_is_bumped_for_sds_extension():
    worker = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")
    assert "bt38-scanner-v2" in worker
    assert "/static/js/mobile-sds-scanner-alignment.js" in worker
