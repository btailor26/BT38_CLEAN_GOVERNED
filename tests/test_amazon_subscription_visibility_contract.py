from pathlib import Path


VISIBILITY = Path("services/governed_amazon_subscription_visibility.py").read_text(
    encoding="utf-8"
)


def test_subscription_visibility_reuses_existing_reconciler_only():
    assert "ensure_governed_amazon_listing_notification_subscriptions" in VISIBILITY
    assert "create_destination" not in VISIBILITY
    assert "boto3" not in VISIBILITY
    assert "eventbridge" not in VISIBILITY.lower()
    assert "sqs" not in VISIBILITY.lower()


def test_subscription_visibility_persists_only_diagnostic_sync_log():
    assert "SyncLog(" in VISIBILITY
    assert 'event_type=amazon_listing_subscription_reconcile' in VISIBILITY
    assert "db.session.commit()" in VISIBILITY
    assert "MarketplaceListing" not in VISIBILITY
    assert "WarehouseStock" not in VISIBILITY
    assert "refresh_governed_listing_from_snapshot" not in VISIBILITY
    assert "push_group_listings" not in VISIBILITY
