from pathlib import Path


def test_bell_uses_exact_committed_event_projection_without_db_read():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "_PRESENTATION_SCOPE_KEYS" in source
    assert '"status"' in source
    assert '"quantity"' in source
    assert '"carrier"' in source
    assert '"tracking_number"' in source
    assert "ready._event_to_bell_record = _event_to_bell_record" in source
    assert "ready._event_only_bell_reader" in source
    assert "db.session" not in source
    assert ".query(" not in source
    assert "requests." not in source
    assert "setInterval" not in source


def test_bell_browser_keeps_only_observed_exact_event_history():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "bt38.notifications.exactEventRecords.v1" in source
    assert "bt38-marketplace-event" in source
    assert "/governed/ui/notifications" in source
    assert "rows.slice(0,50)" in source
    assert "new EventSource" not in source
    assert "window.location.reload" not in source
    assert "fetch(window.location.href" not in source


def test_final_install_order_keeps_zero_query_bell_and_exact_transport_last():
    main = Path("main.py").read_text()
    small = main.index("install_governed_fbm_small_alignment(app)")
    ready = main.index("install_governed_fbm_ready_landing_alignment(app)")
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    bell = main.index("install_governed_bell_event_projection_alignment(app)")
    assert small < ready < exact < bell


def test_bell_labels_follow_order_lifecycle_not_generic_commit_name():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "Delivered" in source
    assert "In transit" in source
    assert "Picked up" in source
    assert "Dispatched" in source
    assert "Sale" in source
    assert "Return requested" in source
    assert "Refunded" in source
