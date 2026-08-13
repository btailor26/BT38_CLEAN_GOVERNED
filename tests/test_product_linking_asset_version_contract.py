from pathlib import Path


APP = Path("app.py").read_text(encoding="utf-8")
GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(encoding="utf-8")
TEMPLATE = Path("templates/product_linking.html").read_text(encoding="utf-8")


def test_product_linking_assets_use_the_runtime_release_version():
    assert 'app.config["BT38_ASSET_VERSION"] = APP_VERSION' in APP
    assert TEMPLATE.count("config['BT38_ASSET_VERSION']") == 2
    assert "?v=bt38-global-state" not in TEMPLATE
    assert "?v=bt38-page-controller" not in TEMPLATE


def test_dynamic_product_linking_controller_inherits_loader_version():
    assert "new URL(document.currentScript.src" in GLOBAL_STATE
    assert 'loaderUrl.searchParams.get("v")' in GLOBAL_STATE
    assert "encodeURIComponent(assetVersion)" in GLOBAL_STATE
    assert "product-linking-session-verified-link" not in GLOBAL_STATE
