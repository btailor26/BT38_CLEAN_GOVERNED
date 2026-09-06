from pathlib import Path


LEGACY = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")
GUARD = Path("static/js/fbm_dispatch_authority_truth_guard.js").read_text(encoding="utf-8")
DB_AUTHORITY = Path("services/governed_fbm_db_authority_alignment.py").read_text(encoding="utf-8")


def test_legacy_dispatch_authority_fabrication_is_neutralized():
    # Keep the legacy function visible to the contract until it can be removed
    # from the large journey asset, but do not allow its synthetic claim to
    # survive in the rendered FBM workspace.
    assert "function alignDispatchedShippingAuthority(row, status)" in LEGACY
    assert "label.textContent = 'Dispatch authority';" in LEGACY
    assert "note.textContent = 'Persisted shipment evidence';" in LEGACY
    assert "Persisted shipment evidence" in GUARD
    assert "Dispatch authority" in GUARD
    assert "shippingCell.replaceChildren();" in GUARD
    assert "Physical carrier authority is shown only from persisted shipment evidence" in GUARD


def test_truth_guard_is_installed_on_fbm_page_without_provider_work():
    assert "_install_dispatch_authority_truth_guard" in DB_AUTHORITY
    assert "fbm_dispatch_authority_truth_guard.js" in DB_AUTHORITY
    assert 'request.path.rstrip("/") != "/fbm"' in DB_AUTHORITY
    assert "PacklinkAdapter" not in GUARD
    assert "AmazonShippingAdapter" not in GUARD
    assert "fetch(" not in GUARD
