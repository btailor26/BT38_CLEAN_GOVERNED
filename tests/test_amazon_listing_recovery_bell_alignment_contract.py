from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME = (
    ROOT / "services" / "governed_runtime_engine.py"
).read_text(encoding="utf-8")

ROUTES = (
    ROOT / "governed_routes.py"
).read_text(encoding="utf-8")

BASE = (
    ROOT / "templates" / "base.html"
).read_text(encoding="utf-8")


def test_amazon_listing_recovery_is_not_blocked_by_fba_fuse():
    amazon_start = RUNTIME.index('if "amazon" in platform:')
    ebay_start = RUNTIME.index('if "ebay" in platform:', amazon_start)
    block = RUNTIME[amazon_start:ebay_start]

    assert "run_governed_amazon_listing_fulfillment_refresh" in block
    assert "fba_import_enabled" in block

    listing_call = block.index(
        "run_governed_amazon_listing_fulfillment_refresh"
    )
    fba_gate = block.index("fba_import_enabled")

    # Listing recovery is reached before deciding whether FBA inventory
    # hydration itself is allowed.
    assert listing_call < fba_gate


def test_existing_8_hour_recovery_cycle_is_retained():
    assert "FULL_SYNC_SECONDS = 8 * 60 * 60" in RUNTIME
    assert 'source="full_sync_8h_recovery"' in RUNTIME


def test_bell_uses_canonical_marketplace_listing_as_listing_event():
    assert "MarketplaceListing.created_at.desc()" in ROUTES
    assert '"event_key": f"listing:' in ROUTES
    assert '"log_type": "marketplace_listing"' in ROUTES
    assert '"title": title' in ROUTES
    assert '"sku": sku' in ROUTES
    assert '"listing_id": external_listing_id' in ROUTES


def test_bell_does_not_create_duplicate_listing_notification_records():
    start = ROUTES.index("def governed_ui_notifications():")
    end = ROUTES.find("\\n@governed_bp.", start + 10)
    block = ROUTES[start:end if end != -1 else None]

    assert "db.session.add(" not in block
    assert "db.session.commit(" not in block
    assert "SystemLog(" not in block


def test_listing_bell_displays_title_sku_and_marketplace_identity():
    assert 'record.log_type === "marketplace_listing"' in BASE
    assert '`SKU ${record.sku}`' in BASE
    assert '"ASIN"' in BASE
    assert '"Item"' in BASE


def test_alignment_adds_no_browser_polling():
    start = BASE.index('<script id="bt38NotificationPanelScript">')
    end = BASE.index("</script>", start)
    bell = BASE[start:end]

    assert "setInterval(" not in bell
    assert "setTimeout(" not in bell
