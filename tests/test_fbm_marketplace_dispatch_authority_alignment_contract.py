from pathlib import Path


SOURCE = Path("services/governed_fbm_marketplace_dispatch_authority_alignment.py").read_text(encoding="utf-8")
INSTALLER = Path("services/governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_marketplace_dispatch_supersedes_pre_dispatch_route_presentation():
    assert '"marketplace_buy_shipping": False' in SOURCE
    assert '"external_provider": False' in SOURCE
    assert '"manual": False' in SOURCE
    assert 'Marketplace dispatch is persisted on this order and now owns shipment truth' in SOURCE


def test_dispatched_without_tracking_stays_marketplace_authoritative_and_neutral():
    assert 'carrier_display = carrier or f"{platform} marketplace shipment"' in SOURCE
    assert 'service_display = "Marketplace dispatch" if tracking else "Tracking pending"' in SOURCE
    assert 'tracking_number=tracking' in SOURCE
    assert 'carrier_accepted_at=changed_at if status in _TERMINAL_DELIVERY_STATES else None' in SOURCE


def test_processed_unshipped_remains_awaiting_dispatch():
    assert '"processed", "confirmed", "unshipped", "order", "ready"' in SOURCE
    assert 'return "Awaiting dispatch"' in SOURCE


def test_alignment_preserves_marketplace_promise_and_is_read_only():
    assert "delivery_promise" not in SOURCE
    assert "FBMOrderProfile" not in SOURCE
    assert "db.session" not in SOURCE
    assert "requests." not in SOURCE
    assert "fetch(" not in SOURCE


def test_alignment_is_installed_after_existing_lifecycle_authority():
    lifecycle = INSTALLER.index("install_governed_fbm_lifecycle_alignment(app)")
    dispatch = INSTALLER.index("install_governed_fbm_marketplace_dispatch_authority_alignment()")
    assert lifecycle < dispatch
