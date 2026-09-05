from pathlib import Path


def test_production_deploy_does_not_run_automatic_recovery_or_catchup():
    workflow = Path('.github/workflows/deploy-fly.yml').read_text(encoding='utf-8')

    forbidden = (
        'Recover recent known Packlink purchases once',
        'Reconcile eBay webhook and recover missed changes once',
        'Recover stale marketplace dispatch truth once',
        'Record Amazon listing subscription reconciliation',
        'Reconcile Amazon MCF status subscription',
        'Recover missing Amazon listings once after deployment',
        'recover_packlink_shipments_for_day',
        'align_ebay_notifications_and_recover_missed_changes',
        'recover_bounded_marketplace_dispatch_truth',
        'record_governed_amazon_listing_subscription_reconciliation',
        'align_governed_amazon_mcf_notification_to_existing_sqs',
        'run_governed_amazon_listing_fulfillment_refresh',
    )

    for token in forbidden:
        assert token not in workflow

    assert 'Deploy exact GitHub commit with Fly remote builder' in workflow
    assert 'Verify deployed image DB contract against production Neon' in workflow
    assert 'Restore Packlink callback registration' in workflow
