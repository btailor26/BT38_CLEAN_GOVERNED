from pathlib import Path

SESSION = Path(
    "static/js/product-linking-session.js"
).read_text(encoding="utf-8")

TEMPLATE = Path(
    "templates/product_linking.html"
).read_text(encoding="utf-8")


def test_unlink_requires_dedicated_confirmation_modal():
    assert 'id="unlinkListingConfirmModal"' in TEMPLATE
    assert 'id="confirmExplicitUnlinkButton"' in TEMPLATE
    assert "Confirm Unlink" in TEMPLATE
    assert "Keep Linked" in TEMPLATE


def test_row_unlink_does_not_post_directly():
    start = SESSION.index("window.unlinkListing = function")
    end = SESSION.index(
        "async function confirmExplicitUnlink()",
        start,
    )

    block = SESSION[start:end]

    assert "fetch(" not in block
    assert "pendingExplicitUnlink = {" in block
    assert ".getOrCreateInstance(modalElement)" in block
    assert ".show();" in block


def test_only_explicit_confirmation_posts_unlink_and_merges_exact_delta():
    start = SESSION.index(
        "async function confirmExplicitUnlink()"
    )
    end = SESSION.index(
        "function wire()",
        start,
    )

    block = SESSION[start:end]

    assert "/unlink" in block
    assert "user_confirmed: true" in block
    assert "explicitUnlinkInFlight" in block
    assert "await applyMutationContract(data, {" in block
    assert "await clearSnapshot();" not in block
    assert "window.location.reload();" not in block


def test_no_timer_can_unlink_relationship():
    block = SESSION[
        SESSION.index("window.unlinkListing"):
        SESSION.index("function wire()")
    ]

    assert "setTimeout" not in block
    assert "setInterval" not in block


def test_idle_product_linking_boot_does_not_poll_or_refresh():
    boot = SESSION[
        SESSION.index("function boot()"):
    ]

    assert "visibilitychange" not in boot
    assert 'addEventListener("focus"' not in boot
    assert "refreshVisibleProductLinkingOnce" not in boot
    assert "confirmExplicitUnlink" not in boot
    assert "/unlink" not in boot
