from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "templates" / "public_landing.html"
PRIVACY = ROOT / "templates" / "privacy.html"
TERMS = ROOT / "templates" / "terms.html"
SUPPORT = ROOT / "templates" / "support.html"
APPLY = ROOT / "templates" / "early_access_apply.html"
RECEIVED = ROOT / "templates" / "early_access_received.html"
PUBLIC_ROUTES = ROOT / "services" / "public_early_access.py"
CANONICAL_LOGO = ROOT / "static" / "img" / "marketplaces" / "bt38-inventory-amazon-developer.svg"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_app_name_is_bt38_inventory_everywhere():
    for path in (LANDING, PRIVACY, TERMS, SUPPORT, APPLY, RECEIVED):
        text = _text(path)
        assert "BT38 Inventory" in text, f"BT38 Inventory identity missing from {path}"
        assert "BT38 Inventory Solutions" not in text, f"stale product identity in {path}"


def test_landing_explains_selected_ecommerce_solution_connectors_category():
    text = _text(LANDING)
    assert "Ecommerce Solution Connectors" in text
    assert "Amazon" in text
    assert "eBay" in text
    assert "Current supported marketplace connections include Amazon and eBay" in text


def test_public_policy_and_support_destinations_are_real_routes():
    landing = _text(LANDING)
    routes = _text(PUBLIC_ROUTES)
    for path in ("/privacy", "/terms", "/support"):
        assert f'href="{path}"' in landing, f"landing does not link to {path}"
        assert f'@app.get("{path}")' in routes, f"public route {path} is not registered"


def test_canonical_amazon_appstore_brand_is_github_source_and_used_by_landing():
    assert CANONICAL_LOGO.is_file(), "canonical BT38 Inventory Appstore logo missing from governed GitHub source"
    logo = _text(CANONICAL_LOGO)
    assert 'viewBox="0 0 1000 1000"' in logo, "canonical Appstore logo must remain square"
    assert "BT38" in logo and "Inventory" in logo
    landing = _text(LANDING)
    assert "img/marketplaces/bt38-inventory-amazon-developer.svg" in landing


def test_public_claims_are_limited_to_current_supported_connections():
    text = _text(LANDING)
    assert "Current supported marketplace connections include Amazon and eBay" in text
    for future_channel in ("TikTok Shop connection", "Shopify connection", "Etsy connection", "Facebook connection"):
        assert future_channel not in text
