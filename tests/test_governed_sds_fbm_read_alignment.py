from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_alignment_is_installed_after_config_authority():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_governed_seller_delivery_config(app)" in source
    assert "install_governed_sds_fbm_read_alignment()" in source
    assert source.index("install_governed_seller_delivery_config(app)") < source.index("install_governed_sds_fbm_read_alignment()")


def test_sds_is_attached_to_existing_fbm_read_helpers_only():
    source = (ROOT / "services" / "governed_sds_fbm_read_alignment.py").read_text(encoding="utf-8")
    assert "fbm._marketplace_shipping_mode = shipping_mode" in source
    assert "fbm._shipping_provider_options = provider_options" in source
    assert '"provider": "sds"' in source
    assert '"available": bool(sds.get("eligible"))' in source
    assert "create_shipment" not in source
    assert "guard_marketplace_write" not in source


def test_sds_auto_scan_fails_closed_before_network_lookup():
    source = (ROOT / "services" / "governed_sds_fbm_alignment.py").read_text(encoding="utf-8")
    assert '"order_already_dispatched"' in source
    assert '"prime_sfp_blocked"' in source
    assert '"destination_outside_uk"' in source
    assert '"sds_warehouse_unresolved"' in source
    assert source.index('if prime_sfp:') < source.index('coordinate_lookup(origin)')
    assert source.index('country not in') < source.index('coordinate_lookup(origin)')


def test_postcode_lookup_is_bounded_and_cached():
    source = (ROOT / "services" / "governed_sds_postcode_lookup.py").read_text(encoding="utf-8")
    assert "@lru_cache(maxsize=2048)" in source
    assert "LOOKUP_TIMEOUT_SECONDS = 3" in source
    assert "timeout=LOOKUP_TIMEOUT_SECONDS" in source
