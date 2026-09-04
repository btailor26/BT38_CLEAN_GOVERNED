from pathlib import Path


def test_delivery_promise_reader_installs_existing_amazon_persistence_repair():
    source = Path("services/fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")

    assert "install_governed_amazon_fbm_profile_event_alignment" in source
    assert "install_governed_amazon_fbm_profile_event_alignment(app)" in source
    assert "before_render_template.connect_via(app)" in source


def test_delivery_promise_render_path_remains_persisted_db_only():
    source = Path("services/fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")

    assert "fbm_order_operational_state" in source
    assert "latest_delivery_at" in source
    assert "requests.get" not in source
    assert "get_order(" not in source
