from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ROOT / "services" / "governed_ebay_order_identity_alignment.py"
SCOPES = ROOT / "services" / "governed_ebay_oauth_scopes.py"
MAIN = ROOT / "main.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ebay_order_confirmation_prefers_explicit_order_line_item_identity():
    source = _text(IDENTITY)
    assert '"orderLineItemId"' in source
    assert 'aligned_payload["marketplace_order_item_id"]' in source
    assert "listingId" in source
    assert "requests." not in source


def test_identity_alignment_wraps_existing_governed_executor_only():
    source = _text(IDENTITY)
    assert "_execution.process_marketplace_notification" in source
    assert "return _ORIGINAL(" in source
    assert "db.session" not in source


def test_governed_reauthorization_includes_shipping_finances_and_returns():
    source = _text(SCOPES)
    assert 'EBAY_COMMERCE_SHIPPING_SCOPE' in source
    assert 'EBAY_FINANCES_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.finances"' in source
    assert 'EBAY_RETURN_READ_SCOPE' in source
    assert 'EBAY_RETURN_WRITE_SCOPE' in source
    default_block = source.split("DEFAULT_EBAY_OAUTH_SCOPES = (", 1)[1].split(")", 1)[0]
    assert "EBAY_COMMERCE_SHIPPING_SCOPE" in default_block
    assert "EBAY_FINANCES_SCOPE" in default_block
    assert "EBAY_RETURN_READ_SCOPE" in default_block
    assert "EBAY_RETURN_WRITE_SCOPE" in default_block


def test_existing_legacy_refresh_token_is_not_forced_to_ungranted_scopes():
    source = _text(SCOPES)
    assert 'granted = str(credentials.get("oauth_granted_scope") or "").strip()' in source
    assert "if granted:\n        return granted" in source


def test_runtime_imports_ebay_order_identity_alignment():
    source = _text(MAIN)
    assert "import services.governed_ebay_order_identity_alignment" in source
