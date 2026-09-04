from pathlib import Path


def test_event_projection_remains_zero_query_but_is_not_installed_as_bell_authority():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "_PRESENTATION_SCOPE_KEYS" in source
    assert "db.session" not in source
    assert ".query(" not in source
    assert "requests." not in source
    assert "setInterval" not in source

    main = Path("main.py").read_text()
    assert "install_governed_bell_event_projection_alignment" not in main


def test_persisted_notification_reader_remains_the_installed_bell_authority():
    main = Path("main.py").read_text()
    notifications = Path("services/governed_notification_read_alignment.py").read_text()
    small = Path("services/governed_fbm_small_alignment.py").read_text()

    assert "install_governed_notification_read_alignment(app)" in main
    assert "install_governed_fbm_small_alignment(app)" in main
    assert "install_governed_exact_record_event_alignment(app)" in main
    assert main.index("install_governed_notification_read_alignment(app)") < main.index("install_governed_fbm_small_alignment(app)")
    assert main.index("install_governed_fbm_small_alignment(app)") < main.index("install_governed_exact_record_event_alignment(app)")

    assert "MarketplaceOrder" in notifications
    assert "FBMShipment" in notifications
    assert "marketplace_order_id" in notifications
    assert "product_title" in notifications
    assert "sku" in notifications
    assert "quantity" in notifications
    assert "carrier" in notifications
    assert "tracking_number" in notifications
    assert "lifecycle._wrap_notification_bell(app)" in small


def test_restored_bell_keeps_commercial_identity_deduplication():
    small = Path("services/governed_fbm_small_alignment.py").read_text()
    assert 'key = f"sale:{platform}:{order_id}:{sku}:{quantity}:{lifecycle_status}"' in small
    assert 'key = f"webhook:{platform}:{order_id}:{lifecycle_status}"' in small
    assert "Lifecycle changes" in small


def test_exact_transport_remains_for_targeted_page_refresh_without_owning_bell():
    main = Path("main.py").read_text()
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    small = main.index("install_governed_fbm_small_alignment(app)")
    ready = main.index("install_governed_fbm_ready_landing_alignment(app)")
    assert small < ready < exact
    assert "install_governed_bell_event_projection_alignment" not in main
