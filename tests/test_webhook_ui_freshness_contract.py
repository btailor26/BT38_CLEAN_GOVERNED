from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webhook_revision_endpoint_is_read_only_completed_truth():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert '@app.get("/governed/ui/webhook-revision")' in source
    assert "webhooks.amazon_notifications" in source
    assert "webhooks.ebay_notifications" in source
    assert source.count("WHERE completed_at IS NOT NULL") == 2
    assert "MAX(completed_at)" in source


def test_open_governed_pages_follow_completed_webhook_revision():
    source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert 'id="bt38WebhookUiFreshness"' in source
    assert 'fetch("/governed/ui/webhook-revision"' in source
    assert 'window.setInterval(checkWebhookRevision, 2000)' in source
    assert 'window.location.reload()' in source

    for path in (
        '"/warehouse"',
        '"/product-linking"',
        '"/amazon-fba-stock"',
        '"/listings"',
    ):
        assert path in source


def test_ui_freshness_does_not_start_marketplace_or_stock_work():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    route = main_source.split(
        '@app.get("/governed/ui/webhook-revision")', 1
    )[1].split("@app.teardown_request", 1)[0]

    assert "SELECT" in route
    assert "INSERT" not in route
    assert "UPDATE " not in route
    assert "DELETE " not in route
    assert "push_marketplace" not in route
    assert "sync" not in route.lower()
