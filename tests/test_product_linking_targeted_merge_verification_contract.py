from pathlib import Path

SOURCE = Path("static/js/product-linking-session.js").read_text(encoding="utf-8")


def _block(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    end = SOURCE.index(end_marker, start)
    return SOURCE[start:end]


def test_targeted_merge_removes_listing_from_stale_rows_before_fresh_rows_are_inserted():
    block = _block("function mergeTargetedData", "function mutationSearchKeys")

    stale_filter = block.index(".filter((product) => !changedProductIds.has(productIdentity(product)))")
    stale_map = block.index(".map((product) =>", stale_filter)
    fresh_insert = block.index(".concat(changedProducts)", stale_map)

    assert stale_filter < stale_map < fresh_insert
    assert "Fresh rows" in block or "Fresh rows" in SOURCE


def test_fresh_targeted_rows_are_not_filtered_by_affected_listing_id():
    block = _block("function mergeTargetedData", "function mutationSearchKeys")

    fresh_insert = block.index(".concat(changedProducts)")
    affected_filter = block.index("!listingIds.has(listingIdentity(listing))")

    # The affected-listing filter belongs to the stale cached rows and must run
    # before changedProducts are appended. Otherwise the newly linked row is
    # immediately removed from the browser session.
    assert affected_filter < fresh_insert


def test_link_still_verifies_browser_relationship_after_targeted_merge():
    block = _block("window.linkListingToWarehouse = async function", "window.unlinkListing = function")

    merge_pos = block.index("await applyMutationContract(data, {")
    verify_pos = block.index("mappingExists(listingId, warehouseId, data.group_id)")
    assert merge_pos < verify_pos
    assert "window.location.reload" not in block
    assert "clearSnapshot" not in block
