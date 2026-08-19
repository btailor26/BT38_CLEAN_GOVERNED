from pathlib import Path


def test_packlink_original_draft_can_be_recreated_until_tracking_exists():
    source = Path("governed_fbm_routes.py").read_text(encoding="utf-8")
    block = source.split('def packlink_create_draft(order_id: int):', 1)[1].split('@governed_fbm_bp.get("/fbm/shipments/', 1)[0]

    assert "FBMShipment.tracking_number.isnot(None)" in block
    assert 'FBMShipment.tracking_number != ""' in block
    assert 'shipment.provider_shipment_id = None' in block
    assert 'retry_allowed": True' in block
    assert "A Packlink shipment draft already exists for this order. No duplicate draft was created." not in block


def test_packlink_extra_label_requires_return_or_replacement_confirmation():
    source = Path("governed_fbm_routes.py").read_text(encoding="utf-8")
    block = source.split('def packlink_create_draft(order_id: int):', 1)[1].split('@governed_fbm_bp.get("/fbm/shipments/', 1)[0]

    assert 'purpose not in {"return", "replacement"}' in block
    assert '"requires_shipment_purpose": True' in block
    assert '"options": ["return", "replacement"]' in block
    assert 'required_confirmation = f"CONFIRM_{purpose.upper()}"' in block
    assert 'confirm_additional_shipment' in block


def test_external_label_completion_waits_for_tracking():
    source = Path("services/fbm_post_purchase.py").read_text(encoding="utf-8")

    assert 'tracking_ready = bool(str(shipment.tracking_number or "").strip())' in source
    assert 'shipment.purchase_status = "purchased" if tracking_ready else "label_ready_tracking_pending"' in source
    assert 'shipment.marketplace_confirmation_status = "tracking_pending"' in source
    assert 'if tracking_ready and mapping_ready:' in source
    assert '"shipment_complete": tracking_ready' in source
    assert '"marketplace_confirmation_allowed": tracking_ready and mapping_ready' in source


def test_external_second_packlink_reference_is_held_after_tracking():
    source = Path("services/fbm_packlink_callback.py").read_text(encoding="utf-8")
    block = source.split('def _attach_by_marketplace_reference(', 1)[1].split('def process_packlink_callback(', 1)[0]

    assert 'completed_tracking = str(shipment.tracking_number or "").strip()' in block
    assert 'existing_reference != reference' in block
    assert 'additional_shipment_requires_return_or_replacement_confirmation' in block
