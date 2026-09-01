from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_scans_are_persisted_events_with_unique_identity():
    model = (ROOT / "sds_models.py").read_text(encoding="utf-8")
    assert '__tablename__ = "sds_scan_events"' in model
    assert "event_key" in model
    assert "unique=True" in model
    assert 'default="seller_scan"' in model


def test_sds_scan_lifecycle_uses_explicit_real_events_only():
    source = (ROOT / "services" / "governed_sds_scan_alignment.py").read_text(encoding="utf-8")
    assert '"handover"' in source
    assert '"in_transit"' in source
    assert '"delivered"' in source
    assert 'body.get("confirm_scan") != f"SCAN_{event_type.upper()}"' in source
    assert '"awaiting_seller_handover"' in source
    assert '"seller_handover_confirmed"' in source
    assert '"in_transit"' in source
    assert '"delivered"' in source


def test_sds_scans_update_existing_fbm_timestamps_without_marketplace_write():
    source = (ROOT / "services" / "governed_sds_scan_alignment.py").read_text(encoding="utf-8")
    assert '"timestamp_field": "carrier_accepted_at"' in source
    assert '"timestamp_field": "first_movement_at"' in source
    assert '"timestamp_field": "delivered_at"' in source
    assert 'shipment.last_provider_status = f"sds_{event_type}"' in source
    assert '"marketplace_written": False' in source
    assert "guard_marketplace_write" not in source


def test_sds_scan_lifecycle_is_installed_after_dispatch_authority():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_governed_sds_dispatch_alignment(app)" in source
    assert "install_governed_sds_scan_alignment(app)" in source
    assert source.index("install_governed_sds_dispatch_alignment(app)") < source.index("install_governed_sds_scan_alignment(app)")
