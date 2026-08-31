from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_lifecycle_alignment.py").read_text(encoding="utf-8")
EBAY = (ROOT / "services" / "governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")
AMAZON = (ROOT / "services" / "governed_amazon_tracking_readback.py").read_text(encoding="utf-8")
JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")
LEGACY_JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey_legacy.js").read_text(encoding="utf-8")
FBM_TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")
BASE_TEMPLATE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")


def test_existing_purchase_key_preserves_provider_source_ownership():
    ownership = ALIGNMENT.split('def bt38_owns_shipment(shipment) -> bool:', 1)[1].split('\ndef _marketplace_proxy(order):', 1)[0]
    assert 'provider = _status(getattr(shipment, "provider", None))' in ownership
    assert 'if provider == "packlink":' in ownership
    assert 'return purchase_key.startswith("packlink_")' in ownership
    assert 'if provider == "amazon_buy_shipping":' in ownership
    assert 'return purchase_key.startswith("amazon_buy_shipping:")' in ownership
    assert 'if provider == "manual":' in ownership
    assert 'return purchase_key.startswith("manual:")' in ownership
    assert 'purchase_status' not in ownership
    assert 'label_purchased_at' not in ownership
    assert 'tracking_number' not in ownership


def test_packlink_owned_shipment_stays_source_before_marketplace_fallback():
    shipment_map = ALIGNMENT.split('def aligned_shipment_map(rows):', 1)[1].split('\n    def aligned_shipping_mode(', 1)[0]
    assert 'if bt38_owns_shipment(shipment):' in shipment_map
    assert 'result[key] = shipment' in shipment_map
    assert 'marketplace = _marketplace_proxy(row)' in shipment_map
    assert 'result[key] = marketplace' in shipment_map
    assert shipment_map.index('result[key] = shipment') < shipment_map.index('marketplace = _marketplace_proxy(row)')


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
    assert "return String(row && row.dataset ? row.dataset.labelReady || '' : '') === '1';" in JOURNEY
    assert "setBadgeState(pickedUp, 'bg-danger')" in JOURNEY
    assert "setBadgeState(pickedUp, 'bg-success')" in JOURNEY
    assert 'pickupStates.has(status)' in JOURNEY
    assert 'Label / postage created · waiting for carrier collection' in JOURNEY
    assert 'persisted label/postage created without carrier acceptance => Picked up RED' in JOURNEY
    assert "querySelectorAll('code')" not in JOURNEY.split('function labelOrTrackingStageReached(row) {', 1)[1].split('\n    }', 1)[0]


def test_marketplace_journey_uses_exact_row_identity_instead_of_generic_cell_guessing():
    assert 'function marketplaceFromRow(row)' in LEGACY_JOURNEY
    assert "marketplaceCell.querySelector('.fbm-marketplace-logo')" in LEGACY_JOURNEY
    assert "logo.getAttribute('alt') || logo.getAttribute('title')" in LEGACY_JOURNEY
    assert "orderCell.querySelector('.fw-semibold')" in LEGACY_JOURNEY
    assert "button.dataset.platform || marketplaceFromRow(row) || 'Marketplace'" in LEGACY_JOURNEY
    install = LEGACY_JOURNEY.split('function installMarketplaceJourneyLinks() {', 1)[1].split('\n    function installEbayShippingHandoff()', 1)[0]
    assert 'const marketplace = marketplaceFromRow(row);' in install
    assert "row.children[1]?.querySelector('strong')" not in install


def test_browser_journey_assets_are_fresh_and_core_journey_is_not_gated_by_delivery_alignment():
    assert "config['BT38_ASSET_VERSION']" in FBM_TEMPLATE
    assert "filename='js/fbm_tracking_journey.js', v=config['BT38_ASSET_VERSION']" in FBM_TEMPLATE
    assert "bootstrapUrl.searchParams.get('v')" in JOURNEY
    assert 'String(Date.now())' in JOURNEY
    assert "assetUrl('/static/js/fbm_ebay_shipping_alignment.js')" in JOURNEY
    assert "assetUrl('/static/js/fbm_delivery_promise_journey_alignment.js')" in JOURNEY
    assert "assetUrl('/static/js/fbm_tracking_journey_legacy.js')" in JOURNEY
    assert "nativeScript.onload = loadLegacy" in JOURNEY
    assert "nativeScript.onerror = loadLegacy" in JOURNEY
    assert "legacy.onload = function ()" in JOURNEY
    assert "loadDeliveryPromiseAlignment();" in JOURNEY
    assert "nativeScript.onload = loadDeliveryPromiseAlignment" not in JOURNEY


def test_fbm_reuses_the_existing_governed_live_event_channel_without_polling():
    assert 'const liveUrl = "/governed/ui/events/stream"' in BASE_TEMPLATE
    assert 'new EventSource(' in BASE_TEMPLATE
    assert '"bt38-marketplace-event"' in BASE_TEMPLATE
    assert "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent)" in JOURNEY
    assert 'new EventSource(' not in JOURNEY
    assert 'setInterval(' not in JOURNEY
    assert '/governed/ui/events/stream' not in JOURNEY
    assert 'window.location.reload()' in JOURNEY
    assert "hidden.bs.modal" in JOURNEY


def test_fbm_search_stays_inside_the_current_browser_session():
    search = JOURNEY.split('function installFbmSearch() {', 1)[1].split('\n    function alignPersistedLifecycle()', 1)[0]
    assert "const fbmSearchSessionKey = 'bt38:fbm:search';" in JOURNEY
    assert 'window.sessionStorage.setItem(fbmSearchSessionKey' in search
    assert 'window.sessionStorage.getItem(fbmSearchSessionKey)' in search
    assert "Array.from(table.querySelectorAll('.fbm-order-row')).map" in search
    assert 'No DB, marketplace, provider,' in search
    assert 'fetch(' not in search
    assert 'XMLHttpRequest' not in search
    assert 'new EventSource(' not in search
    assert 'setInterval(' not in search


def test_alignment_does_not_create_parallel_runtime_or_browser_polling():
    for forbidden in ('Thread(', 'Queue(', 'setInterval('):
        assert forbidden not in ALIGNMENT
    assert 'run_governed_marketplace_order_import' not in ALIGNMENT
