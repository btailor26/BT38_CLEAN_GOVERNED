from pathlib import Path


JOURNEY = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")
DB_AUTHORITY = Path("services/governed_fbm_db_authority_alignment.py").read_text(encoding="utf-8")


def test_browser_does_not_fabricate_dispatch_authority_from_carrier_text():
    assert "function alignDispatchedShippingAuthority(row, status)" not in JOURNEY
    assert "label.textContent = 'Dispatch authority';" not in JOURNEY
    assert "note.textContent = 'Persisted shipment evidence';" not in JOURNEY
    assert "shippingCell.replaceChildren();" not in JOURNEY


def test_db_authority_does_not_install_a_second_browser_truth_guard():
    assert "_canonical_persisted_shipment_map" in DB_AUTHORITY
    assert "_canonical_rank" in DB_AUTHORITY
    assert "_install_dispatch_authority_truth_guard" not in DB_AUTHORITY
    assert "fbm_dispatch_authority_truth_guard.js" not in DB_AUTHORITY
    assert "PacklinkAdapter" not in DB_AUTHORITY
    assert "AmazonShippingAdapter" not in DB_AUTHORITY
