from pathlib import Path


def test_main_installs_existing_persisted_delivery_promise_alignment():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "from services.fbm_db_delivery_promise_alignment import install_fbm_db_delivery_promise_alignment" in source
    assert "install_fbm_db_delivery_promise_alignment(app)" in source


def test_fbm_promise_reader_remains_db_backed_without_marketplace_call():
    source = Path("services/fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")
    assert "FBMOrderProfile" in source
    assert "fbm_order_operational_state" in source
    assert "before_render_template" in source
    assert "get_order(" not in source
    assert "requests.get" not in source


def test_template_uses_injected_ship_and_delivery_dates():
    source = Path("templates/fbm.html").read_text(encoding="utf-8")
    assert "promise.ship_by_at" in source
    assert "promise.latest_delivery_at" in source
    assert "Ship by" in source
    assert "Deliver by" in source
