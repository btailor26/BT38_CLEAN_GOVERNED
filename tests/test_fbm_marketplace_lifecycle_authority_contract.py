from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_lifecycle_alignment.py").read_text(encoding="utf-8")
EBAY = (ROOT / "services" / "governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")
AMAZON = (ROOT / "services" / "governed_amazon_tracking_readback.py").read_text(encoding="utf-8")
JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")


def test_existing_purchase_key_and_completed_provider_state_discriminate_ownership():
    assert 'purchase_key.startswith("packlink_")' in ALIGNMENT
    assert 'purchase_key.startswith("packlink_draft:")' in ALIGNMENT
    assert 'purchase_status in {"purchased", "label_ready_tracking_pending"}' in ALIGNMENT
    assert 'label_purchased_at is not None or tracking' in ALIGNMENT
    assert 'purchase_key.startswith("amazon_buy_shipping:")' in ALIGNMENT
    assert 'purchase_key.startswith("manual:")' in ALIGNMENT
    assert 'if bt38_owns_shipment(shipment)' in ALIGNMENT


def test_unpaid_packlink_draft_cannot_override_marketplace_tracking_truth():
    ownership = ALIGNMENT.split('def bt38_owns_shipment(shipment) -> bool:', 1)[1].split('\ndef _marketplace_proxy(order):', 1)[0]
    draft_rule = ownership.split('if purchase_key.startswith("packlink_draft:"):', 1)[1].split('\n        return bool(', 1)[0]
    assert 'pending_provider_payment' not in draft_rule
    assert 'purchase_status in {"purchased", "label_ready_tracking_pending"}' in draft_rule
    assert 'label_purchased_at is not None or tracking' in draft_rule

    shipment_map = ALIGNMENT.split('def aligned_shipment_map(rows):', 1)[1].split('\n    def aligned_shipping_mode(', 1)[0]
    assert 'if bt38_owns_shipment(shipment):' in shipment_map
    assert 'marketplace = _marketplace_proxy(row)' in shipment_map
    assert 'result[key] = marketplace' in shipment_map


def test_marketplace_owned_shipments_use_persisted_order_truth_not_provider_status():
    assert 'provider="marketplace"' in ALIGNMENT
    assert 'carrier=carrier' in ALIGNMENT
    assert 'tracking_number=tracking' in ALIGNMENT
    assert 'marketplace_confirmation_status="marketplace_authoritative"' in ALIGNMENT
    assert 'This shipment is marketplace-authoritative; BT38 will not query the Packlink provider path for it.' in ALIGNMENT


def test_amazon_pending_stays_out_of_fbm_without_consuming_bounded_slots():
    assert 'page._platform(row).strip().lower() == "amazon"' in ALIGNMENT
    assert '_status(getattr(row, "status", None)) == "pending"' in ALIGNMENT
    assert 'original_latest_rows(probe_limit)' in ALIGNMENT
    assert 'page._FBM_MAX_EXPANDED' in ALIGNMENT
    assert 'page._FBM_DISCOVERY_MULTIPLIER' in ALIGNMENT


def test_amazon_buy_shipping_purchase_is_gated_but_tracking_readback_remains():
    assert 'AMAZON_BUY_SHIPPING_APPROVED' in ALIGNMENT
    assert '"governed_fbm.amazon_rates"' in ALIGNMENT
    assert '"governed_fbm.amazon_purchase"' in ALIGNMENT
    assert 'Amazon Buy Shipping is pending production approval.' in ALIGNMENT
    assert 'hydrate_amazon_tracking_for_order' in ALIGNMENT


def test_webhook_lifecycle_extends_the_existing_order_status_path():
    for status in (
        '"PICKEDUP": "picked_up"',
        '"INTRANSIT": "in_transit"',
        '"OUTFORDELIVERY": "out_for_delivery"',
        '"RETURNREQUESTED": "return_requested"',
        '"REFUNDED": "refunded"',
        '"REPLACEMENTREQUESTED": "replacement_requested"',
        '"chargeback"',
        '"case_open"',
    ):
        assert status in ALIGNMENT
    assert 'execution._extract_order_lifecycle_values = aligned_extract' in ALIGNMENT


def test_bell_identity_changes_when_the_same_order_lifecycle_changes():
    assert 'record["status_label"] = label' in ALIGNMENT
    assert 'record["title"] = f"{label} · {product_title}"' in ALIGNMENT
    assert 'record["event_key"] = f"order:{store_id}:{order_id}:{line_identity}:{status}"' in ALIGNMENT


def test_ebay_and_amazon_readbacks_correct_stale_marketplace_fields():
    assert '_text(row.carrier) != _text(shipment["carrier"])' in EBAY
    assert '_text(row.tracking_number) != _text(shipment["tracking_number"])' in EBAY
    assert 'marketplace_status = _ebay_lifecycle_status(order)' in EBAY
    assert '_text(getattr(row, "carrier", None)) != _text(shipment["carrier"])' in AMAZON
    assert '_text(getattr(row, "tracking_number", None)) != _text(shipment["tracking_number"])' in AMAZON


def test_routine_dispatch_recovery_reuses_existing_exact_readbacks():
    assert 'hydrate_exact_ebay_order' in ALIGNMENT
    assert 'source=f"{source}:ebay_fulfillment_readback"' in ALIGNMENT
    assert 'hydrate_amazon_tracking_for_order' in ALIGNMENT
    assert 'source=f"{source}:amazon_package_readback"' in ALIGNMENT
    assert 'marketplace_write_started' not in ALIGNMENT


def test_picked_up_stays_red_after_label_stage_until_real_acceptance():
    assert 'labelOrTrackingStageReached(row)' in JOURNEY
    assert "setBadgeState(pickedUp, 'bg-danger')" in JOURNEY
    assert "setBadgeState(pickedUp, 'bg-success')" in JOURNEY
    assert 'pickupStates.has(status)' in JOURNEY
    assert 'Label printed / tracking ready · waiting for carrier collection' in JOURNEY
    assert 'label/tracking ready without carrier acceptance => Picked up RED' in JOURNEY


def test_browser_journey_assets_are_fresh_and_delivery_alignment_is_loaded_before_legacy():
    assert "bootstrapUrl.searchParams.get('v')" in JOURNEY
    assert 'String(Date.now())' in JOURNEY
    assert "assetUrl('/static/js/fbm_ebay_shipping_alignment.js')" in JOURNEY
    assert "assetUrl('/static/js/fbm_delivery_promise_journey_alignment.js')" in JOURNEY
    assert "assetUrl('/static/js/fbm_tracking_journey_legacy.js')" in JOURNEY
    assert "nativeScript.onload = loadDeliveryPromiseAlignment" in JOURNEY
    assert "nativeScript.onerror = loadDeliveryPromiseAlignment" in JOURNEY
    assert "delivery.onload = loadLegacy" in JOURNEY
    assert "delivery.onerror = loadLegacy" in JOURNEY


def test_alignment_does_not_create_parallel_runtime_or_browser_polling():
    for forbidden in ('Thread(', 'Queue(', 'setInterval('):
        assert forbidden not in ALIGNMENT
    assert 'run_governed_marketplace_order_import' not in ALIGNMENT
