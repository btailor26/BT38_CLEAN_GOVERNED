from pathlib import Path


EBAY = Path("services/governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")
TEMPLATE = Path("templates/fbm.html").read_text(encoding="utf-8")
LABEL_FALLBACK = Path("static/js/fbm_label_fallback.js").read_text(encoding="utf-8")
CALLBACK = Path("services/fbm_packlink_callback.py").read_text(encoding="utf-8")
POST_PURCHASE = Path("services/fbm_post_purchase.py").read_text(encoding="utf-8")


def test_ebay_legacy_identity_alias_cannot_double_count_order_quantity():
    assert "_safe_stale_identity_alias" in EBAY
    assert "processed_at" in EBAY
    assert "canonical_line_id" in EBAY
    assert "db.session.delete(row)" in EBAY
    assert "identity_aliases_removed" in EBAY


def test_packlink_paid_label_is_extracted_and_persisted_before_printing():
    assert "adapter.get_labels(reference)" in CALLBACK
    assert "persist_external_label" in CALLBACK
    assert "shipment.label_url" in POST_PURCHASE
    assert 'shipment.label_source = provider' in POST_PURCHASE


def test_ui_has_manual_download_fallback_for_persisted_and_fresh_labels():
    assert "Download label" in TEMPLATE
    assert "shipment.label_url" in TEMPLATE
    assert "fbm_label_fallback.js" in TEMPLATE
    assert "data-label" in LABEL_FALLBACK
    assert "Download label" in LABEL_FALLBACK


def test_packlink_payment_ui_does_not_claim_exact_api_payment():
    assert "Open Packlink · Ready for payment" in LABEL_FALLBACK
    assert "https://pro.packlink.com/" in LABEL_FALLBACK
