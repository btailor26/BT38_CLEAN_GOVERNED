from pathlib import Path


def test_event_projection_is_final_zero_query_bell_authority():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "_PRESENTATION_SCOPE_KEYS" in source
    assert "db.session" not in source
    assert ".query(" not in source
    assert "requests." not in source
    assert "setInterval" not in source
    assert "_event_only_bell_reader" in source
    assert "zero DB/API bell reads" in source

    main = Path("main.py").read_text()
    assert "install_governed_bell_event_projection_alignment" in main
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    bell = main.index("install_governed_bell_event_projection_alignment(app)")
    assert exact < bell


def test_older_db_backed_bell_wrappers_cannot_remain_final_authority():
    main = Path("main.py").read_text()
    small = main.index("install_governed_fbm_small_alignment(app)")
    ready = main.index("install_governed_fbm_ready_landing_alignment(app)")
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    bell = main.index("install_governed_bell_event_projection_alignment(app)")

    assert small < ready < exact < bell

    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert 'app.view_functions[endpoint] = login_required(ready._event_only_bell_reader)' in projection


def test_bell_keeps_bounded_browser_observed_history_without_db_hydration():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    ready = Path("services/governed_fbm_ready_landing_alignment.py").read_text()

    assert "bt38.notifications.exactEventRecords.v1" in projection
    assert "rows.slice(0,50)" in projection
    assert "window.addEventListener('bt38-marketplace-event'" in projection
    assert 'html = html.replace("hydrateBellAfterWake();", "stale = true;")' in ready


def test_exact_transport_and_bell_remain_db_blind():
    exact = Path("services/governed_exact_record_event_alignment.py").read_text()
    ready = Path("services/governed_fbm_ready_landing_alignment.py").read_text()

    assert "zero polling / zero idle DB work" in exact
    assert "ui._condition.wait(timeout=25.0)" in exact
    assert "db.session" not in ready.split("def _event_only_bell_reader():", 1)[1].split("def _restore_pending_fbm_visibility", 1)[0]
    assert '"db_query": False' in ready
