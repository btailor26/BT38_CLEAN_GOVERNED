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


def test_provider_delivery_truth_repairs_canonical_journey_for_every_order_age():
    source = Path("services/fbm_post_purchase.py").read_text(encoding="utf-8")
    reconcile = source.split('def reconcile_provider_lifecycle_state(', 1)[1].split('def _amazon_tracking_number(', 1)[0]

    assert 'shipment.last_provider_status' in reconcile
    assert 'shipment.last_provider_checked_at' in reconcile
    assert '"DELIVERED"' in reconcile
    assert 'shipment.carrier_accepted_at = shipment.carrier_accepted_at or observed_at' in reconcile
    assert 'shipment.first_movement_at = shipment.first_movement_at or observed_at' in reconcile
    assert 'shipment.delivered_at = shipment.delivered_at or observed_at' in reconcile
    assert 'shipment.status = "delivered"' in reconcile
    assert "created_at" not in reconcile
    assert "marketplace_order_id" not in reconcile
    assert "is_prime" not in reconcile


def test_packlink_status_read_reconciles_provider_truth_before_persisting_label():
    routes = Path("governed_fbm_routes.py").read_text(encoding="utf-8")
    block = routes.split('def packlink_shipment_status(shipment_id: int):', 1)[1].split('@governed_fbm_bp.post("/fbm/shipments/', 1)[0]

    provider_index = block.index('shipment.last_provider_status = provider_state or shipment.last_provider_status')
    persist_index = block.index('persist_external_label(')
    assert provider_index < persist_index

    post_purchase = Path("services/fbm_post_purchase.py").read_text(encoding="utf-8")
    persist_block = post_purchase.split('def persist_external_label(', 1)[1].split('def _float_or_none(', 1)[0]
    assert 'reconcile_provider_lifecycle_state(shipment, observed_at=shipment.last_provider_checked_at or now)' in persist_block


def test_delivery_reconciliation_does_not_change_prime_routing_or_mcf_execution():
    source = Path("services/fbm_post_purchase.py").read_text(encoding="utf-8")
    reconcile = source.split('def reconcile_provider_lifecycle_state(', 1)[1].split('def _amazon_tracking_number(', 1)[0]

    assert "AmazonShippingAdapter" not in reconcile
    assert "PacklinkAdapter" not in reconcile
    assert "confirm_external_shipment" not in reconcile
    assert "MCF" not in reconcile
    assert "db.session" not in reconcile
