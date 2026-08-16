from pathlib import Path


VISIBILITY = Path("services/governed_amazon_subscription_visibility.py").read_text(
    encoding="utf-8"
)
ALIGNMENT = Path("services/governed_amazon_eventbridge_alignment.py").read_text(
    encoding="utf-8"
)


def test_subscription_visibility_aligns_upstream_then_reuses_existing_reconciler():
    assert "align_governed_amazon_eventbridge_to_existing_sqs" in VISIBILITY
    assert "ensure_governed_amazon_listing_notification_subscriptions" in VISIBILITY
    assert "MarketplaceListing" not in VISIBILITY
    assert "WarehouseStock" not in VISIBILITY
    assert "refresh_governed_listing_from_snapshot" not in VISIBILITY
    assert "push_group_listings" not in VISIBILITY


def test_eventbridge_alignment_reuses_existing_amazon_sqs_only():
    assert "_amazon_sqs_connection" in ALIGNMENT
    assert '"InputPath": "$.detail"' in ALIGNMENT
    assert "create_destination(" in ALIGNMENT
    assert "create_event_bus(" in ALIGNMENT
    assert "put_rule(" in ALIGNMENT
    assert "put_targets(" in ALIGNMENT
    assert '"new_queue_created": False' in ALIGNMENT
    assert '"new_consumer_created": False' in ALIGNMENT
    assert '"new_importer_created": False' in ALIGNMENT
    assert "create_queue(" not in ALIGNMENT
    assert "MarketplaceListing" not in ALIGNMENT
    assert "WarehouseStock" not in ALIGNMENT
    assert "refresh_governed_listing_from_snapshot" not in ALIGNMENT
    assert "push_group_listings" not in ALIGNMENT


def test_subscription_visibility_persists_diagnostic_sync_log():
    assert "SyncLog(" in VISIBILITY
    assert 'event_type=amazon_listing_subscription_reconcile' in VISIBILITY
    assert "db.session.commit()" in VISIBILITY
