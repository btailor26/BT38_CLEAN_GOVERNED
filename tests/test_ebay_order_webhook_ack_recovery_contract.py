from pathlib import Path
import ast


SOURCE_PATH = Path("services/governed_webhook_rejection_recovery.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name: str) -> str:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_ebay_order_confirmation_ack_requires_exact_recovery_handoff():
    handler = _function_source("recover_when_marketplace_webhook_is_rejected")

    assert "scheduled = request_rejected_webhook_recovery(" in handler
    assert 'platform == "ebay"' in handler
    assert '_ebay_request_topic() == "ORDER_CONFIRMATION"' in handler
    assert "response.status_code = 200" in handler
    assert 'response.headers["X-BT38-Exact-Recovery"] = "scheduled"' in handler

    scheduled_pos = handler.index("scheduled = request_rejected_webhook_recovery(")
    ack_pos = handler.index("response.status_code = 200")
    assert scheduled_pos < ack_pos


def test_ebay_listing_and_other_topics_are_not_acknowledged_by_order_fix():
    handler = _function_source("recover_when_marketplace_webhook_is_rejected")
    topic_reader = _function_source("_ebay_request_topic")

    assert '== "ORDER_CONFIRMATION"' in handler
    assert '== "LISTING"' not in handler
    assert "payload.get(\"topic\")" in topic_reader
    assert "notification.get(\"topic\")" in topic_reader
    assert "metadata.get(\"topic\")" in topic_reader


def test_capture_or_recovery_failure_preserves_original_http_failure():
    handler = _function_source("recover_when_marketplace_webhook_is_rejected")

    # HTTP 200 is permitted only inside the `scheduled` guard. A capture failure
    # has no durable notification ID, therefore exact recovery cannot schedule
    # and the original error response remains available for eBay retry.
    condition = (
        'scheduled\n        and platform == "ebay"\n'
        '        and _ebay_request_topic() == "ORDER_CONFIRMATION"'
    )
    assert condition in handler


def test_existing_exact_recovery_remains_the_only_failure_processor():
    runner = _function_source("_run_pending_recoveries")
    requester = _function_source("request_rejected_webhook_recovery")

    assert "recover_exact_failed_webhook" in runner
    assert "_pending_notifications" in requester
    assert "run_governed_warehouse_sync" not in runner
    assert "run_governed_marketplace_order_import" not in runner
    assert "LISTING" not in runner
