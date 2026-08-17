from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_product_linking_uses_server_rendered_state_without_retired_loader_request():
    controller = _read("static/js/bt38-page-controller.js")

    assert 'root.dataset.bt38Page === "productLinking"' in controller
    assert 'loading.classList.add("d-none")' in controller
    assert 'data.classList.remove("d-none")' in controller
    assert 'network_request_started: false' in controller
    assert 'fetch(' not in controller.split('root.dataset.bt38Page === "productLinking"', 1)[1].split('const allowedPageSizes', 1)[0]


def test_warehouse_sync_releases_ui_and_does_not_force_reload():
    source = _read("static/js/warehouse-governed.js")

    assert "new AbortController()" in source
    assert "controller.abort()" in source
    assert "15000" in source
    assert "btn.disabled = false" in source
    assert "btn.textContent = originalText" in source
    assert "window.location.reload()" not in source
