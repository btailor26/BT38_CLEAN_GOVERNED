from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QZ_SOURCE = ROOT / "static" / "js" / "fbm_qz_print.js"


def _source() -> str:
    return QZ_SOURCE.read_text(encoding="utf-8")


def test_packlink_payment_remains_provider_side_and_status_reads_existing_shipment():
    source = _source()

    assert "Pay in Packlink" in source
    assert "Check payment / get label" in source
    assert "/fbm/shipments/${encodeURIComponent(shipmentId)}/packlink/status" in source
    assert "method: 'GET'" in source

    # The paid-label alignment must never create or repurchase postage.
    assert "/packlink/draft" not in source
    assert "BUY_POSTAGE" not in source


def test_bulk_packlink_action_reuses_selected_existing_shipments_only():
    source = _source()

    assert "bulkPacklinkLabels" in source
    assert "selectedPacklinkShipments" in source
    assert ".fbm-order-checkbox:checked" in source
    assert ".packlink-existing-status[data-shipment-id]" in source
    assert "for (const item of rows)" in source
    assert "await packlinkStatus(item.shipmentId)" in source


def test_paid_labels_print_separately_and_keep_download_fallback():
    source = _source()

    assert "await printLabel(label)" in source
    assert "ensureRowLabelFallback(item.row, label)" in source
    assert "Download label" in source
    assert "label.url || label.base64 || label.data" in source
    assert "downloadBase64Label" in source
