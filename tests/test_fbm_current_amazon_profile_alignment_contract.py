from pathlib import Path


def test_current_fbm_profile_hydration_is_targeted_and_not_recovery():
    source = Path("services/governed_fbm_current_amazon_profile_alignment.py").read_text()

    assert '(request.path.rstrip("/") or "/") != "/fbm"' in source
    assert 'FBMOrderProfile.id.is_(None)' in source
    assert 'MarketplaceOrder.shipped_at.is_(None)' in source
    assert 'MarketplaceOrder.tracking_number.is_(None)' in source
    assert '~MarketplaceOrder.fulfillment_type.in_' in source
    assert 'get_or_refresh_amazon_profile(row, force=True)' in source
    assert '.limit(max(1, min(int(limit), 20)))' in source
    assert 'publish_governed_ui_event' not in source
    assert 'publish_webhook_ui_event' not in source
    assert 'threading' not in source
    assert '90 days' not in source
    assert 'INTERVAL' not in source
    assert 'after_request' not in source


def test_current_hydration_is_installed_before_bell_projection():
    main = Path("main.py").read_text()

    assert 'install_governed_fbm_current_amazon_profile_alignment' in main
    current = main.index('install_governed_fbm_current_amazon_profile_alignment(app)')
    exact = main.index('install_governed_exact_record_event_alignment(app)')
    bell = main.index('install_governed_bell_event_projection_alignment(app)')
    assert current < exact < bell


def test_profile_reader_persists_prime_service_and_delivery_window():
    source = Path("services/fbm_amazon_order_profile.py").read_text()

    assert 'payload.get("IsPrime")' in source
    assert 'ShipmentServiceLevelCategory' in source
    assert 'payload.get("LatestShipDate")' in source
    assert 'payload.get("EarliestDeliveryDate")' in source
    assert 'payload.get("LatestDeliveryDate")' in source
    assert 'INSERT INTO fbm_order_operational_state' in source
    assert 'profile.is_prime = is_prime' in source
    assert 'profile.shipment_service_level = service_level' in source


def test_no_broad_amazon_profile_repair_remains():
    source = Path("services/governed_amazon_fbm_profile_event_alignment.py").read_text()

    assert '_hydrate_missing_recent_profiles' not in source
    assert '_start_missing_profile_repair_once' not in source
    assert "NOW() - INTERVAL '90 days'" not in source
    assert 'get_or_refresh_amazon_profile(order, force=True)' not in source
