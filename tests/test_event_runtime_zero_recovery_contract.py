from pathlib import Path


def test_event_runtime_has_no_automatic_recovery_reads():
    source = Path('services/governed_event_runtime.py').read_text(encoding='utf-8')

    assert 'recover_missed_ebay_listings' not in source
    assert '_recover_mcf_auto_release_events' not in source
    assert 'MISSED_EBAY_LISTING_RECOVERY_SECONDS' not in source
    assert 'time.monotonic' not in source
    assert 'automatic startup/periodic recovery disabled' in source


def test_event_runtime_still_processes_exact_events_and_sleeps():
    source = Path('services/governed_event_runtime.py').read_text(encoding='utf-8')

    assert '_poll_amazon_sqs_once' in source
    assert '_pending_notification_event.wait' in source
    assert '_pop_due_events' in source
    assert '_run_light_reconcile_cycle' in source
    assert 'No timed recovery work. No event means return directly to sleep.' in source
