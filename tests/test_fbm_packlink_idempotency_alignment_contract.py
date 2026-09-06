from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_packlink_idempotency_alignment.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "governed_fbm_routes.py").read_text(encoding="utf-8")


def test_original_packlink_draft_uses_existing_deterministic_purchase_key():
    assert 'purchase_key = f"packlink_draft:{order.store_id}:{order.marketplace_order_id}"' in ROUTES
    assert 'FBMShipment.query.filter_by(purchase_key=purchase_key).first()' in ROUTES


def test_uncertain_or_created_original_draft_cannot_auto_repost_to_provider():
    assert 'endpoint = "governed_fbm.packlink_create_draft"' in ALIGNMENT
    assert '"draft_creating"' in ALIGNMENT
    assert '"draft_verification_required"' in ALIGNMENT
    assert '"pending_provider_payment"' in ALIGNMENT
    assert 'provider_reference or tracking or state in _UNCERTAIN_OR_CREATED_STATES' in ALIGNMENT
    assert 'duplicate_provider_shipment_blocked' in ALIGNMENT
    assert '"automatic_retry_allowed": False' in ALIGNMENT
    assert "PacklinkAdapter" not in ALIGNMENT


def test_core_retry_hint_is_rewritten_fail_closed_after_ambiguous_provider_exception():
    assert 'payload.get("retry_allowed") is True' in ALIGNMENT
    assert 'payload["retry_allowed"] = False' in ALIGNMENT
    assert "provider may already have created the shipment" in ALIGNMENT


def test_return_and_replacement_keep_their_existing_explicit_additional_shipment_path():
    assert 'if purpose in {"return", "replacement"}' in ALIGNMENT
    assert 'return current(*args, **kwargs)' in ALIGNMENT
    assert "CONFIRM_REPLACEMENT" in ROUTES
    assert "CONFIRM_RETURN" not in ALIGNMENT


def test_packlink_idempotency_alignment_installed_in_runtime():
    assert "install_governed_fbm_packlink_idempotency_alignment" in MAIN
    assert "fail" in MAIN.lower() and "second provider draft" in MAIN
