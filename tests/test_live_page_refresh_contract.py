from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_base_wires_one_shared_page_refresh_controller():
    base = _read("templates/base.html")
    controller = _read("static/js/bt38-live-page-refresh.js")

    assert "bt38-live-page-refresh.js" in base
    assert "bt38-marketplace-event" in controller
    assert "new EventSource(" not in controller
    assert "setInterval(" not in controller
    assert "fetch(" not in controller


def test_shared_refresh_is_event_driven_and_coalesced():
    controller = _read("static/js/bt38-live-page-refresh.js")

    assert "lastSequence" in controller
    assert "scheduled" in controller
    assert "window.location.reload()" in controller
    assert "document.visibilityState === 'hidden'" in controller


def test_mcf_keeps_its_single_narrow_refresh_without_global_duplicate():
    controller = _read("static/js/bt38-live-page-refresh.js")
    mcf = _read("templates/mcf_orders.html")

    assert "mcf-orders-body" in controller
    assert "pageOwnsCommittedRefresh" in controller
    assert "bt38-marketplace-event" in mcf
    assert "new EventSource(" not in mcf
