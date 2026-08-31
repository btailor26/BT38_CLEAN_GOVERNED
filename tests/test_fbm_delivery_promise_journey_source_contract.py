from pathlib import Path


def test_fbm_journey_uses_rendered_db_promise_not_provider_payload():
    source = Path("static/js/fbm_delivery_promise_journey_alignment.js").read_text(encoding="utf-8")

    assert "promiseFromRow(row)" in source
    assert "Marketplace promise · persisted BT38 order" in source
    assert "payload.marketplace_promise" not in source
    assert "performanceBlock(promise, history, payload.provider_status)" in source


def test_fbm_journey_alignment_is_loaded_for_the_single_fbm_page():
    source = Path("services/governed_order_clarity_alignment.py").read_text(encoding="utf-8")

    assert 'src="/static/js/fbm_delivery_promise_journey_alignment.js"' in source
    assert "_align_fbm_promise_journey_html" in source
    assert 'path == "/fbm"' in source


def test_fbm_journey_alignment_does_not_add_db_or_marketplace_reads():
    source = Path("static/js/fbm_delivery_promise_journey_alignment.js").read_text(encoding="utf-8")

    # The only network read retained is the existing, click-scoped Packlink status route.
    assert "/packlink/status" in source
    assert "/api/amazon" not in source
    assert "/api/ebay" not in source
    assert "marketplace_promise" not in source
    assert "setInterval" not in source
