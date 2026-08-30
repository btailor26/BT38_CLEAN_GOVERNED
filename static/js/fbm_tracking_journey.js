/* BT38 FBM asset alignment bootstrap.
 * Load native eBay shipping before the preserved tracking journey so the
 * capture-phase native handler owns eBay Shipping and the legacy Seller Hub
 * handler cannot run first.
 *
 * Journey colour rule:
 * - label/tracking stage reached but carrier pickup is not confirmed => RED
 * - carrier pickup/acceptance is persisted from the journey => GREEN
 * - no label/tracking stage yet => neutral
 * A printed/ready label never proves carrier pickup by itself.
 */
(function (document) {
    'use strict';

    const pickupStates = new Set([
        'accepted',
        'carrier_accepted',
        'collected',
        'picked_up',
        'in_transit',
        'out_for_delivery',
        'delivered'
    ]);
    const movementStates = new Set(['in_transit', 'out_for_delivery', 'delivered']);

    function lifecycleLabel(status) {
        const labels = {
            pending: 'Pending',
            unshipped: 'Confirmed',
            order: 'Confirmed',
            confirmed: 'Confirmed',
            partially_shipped: 'Partially dispatched',
            shipped: 'Dispatched',
            accepted: 'Picked up',
            carrier_accepted: 'Picked up',
            collected: 'Picked up',
            picked_up: 'Picked up',
            in_transit: 'In transit',
            out_for_delivery: 'Out for delivery',
            delivered: 'Delivered',
            return_requested: 'Return requested',
            returned: 'Returned',
            refund_requested: 'Refund requested',
            refunded: 'Refunded',
            replacement_requested: 'Replacement requested',
            replacement: 'Replacement',
            case_open: 'Issue / case',
            dispute: 'Dispute',
            chargeback: 'Chargeback',
            cancel_requested: 'Cancellation requested',
            cancelled: 'Cancelled'
        };
        return labels[status] || String(status || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    function lifecycleClass(status) {
        if (['delivered', 'picked_up', 'accepted', 'carrier_accepted', 'collected', 'in_transit', 'out_for_delivery'].includes(status)) return 'bg-success';
        if (['return_requested', 'returned', 'refund_requested', 'refunded', 'case_open', 'dispute', 'chargeback', 'cancel_requested', 'cancelled'].includes(status)) return 'bg-danger';
        if (['replacement_requested', 'replacement'].includes(status)) return 'bg-info text-dark';
        if (status === 'pending') return 'bg-warning text-dark';
        if (['shipped', 'partially_shipped'].includes(status)) return 'bg-primary';
        return 'bg-light text-dark border';
    }

    function labelOrTrackingStageReached(row) {
        if (String(row && row.dataset ? row.dataset.labelReady || '' : '') === '1') return true;
        const shipmentCell = row && row.children ? row.children[7] : null;
        if (!shipmentCell) return false;
        return Array.from(shipmentCell.querySelectorAll('code')).some(code => {
            const value = String(code.textContent || '').trim();
            return value && value !== 'Parcel ID pending';
        });
    }

    function setBadgeState(badge, stateClass) {
        if (!badge) return;
        badge.classList.remove('bg-success', 'bg-danger', 'bg-primary', 'bg-light', 'text-muted', 'text-dark', 'border');
        String(stateClass || '').split(/\s+/).filter(Boolean).forEach(name => badge.classList.add(name));
    }

    function alignPersistedLifecycle() {
        document.querySelectorAll('.fbm-order-row').forEach(row => {
            const status = String(row.dataset.lifecycleStatus || '').trim().toLowerCase();
            const orderCell = row.children && row.children[2];
            if (status && orderCell && !orderCell.querySelector('.bt38-order-lifecycle')) {
                const wrap = document.createElement('div');
                wrap.className = 'small mt-1 bt38-order-lifecycle';
                const badge = document.createElement('span');
                badge.className = `badge ${lifecycleClass(status)}`;
                badge.textContent = lifecycleLabel(status);
                wrap.appendChild(badge);
                orderCell.appendChild(wrap);
            }

            const journeyCell = row.children && row.children[8];
            if (!journeyCell) return;
            const badges = Array.from(journeyCell.querySelectorAll('.badge'));
            const pickedUp = badges.find(badge => /picked up/i.test(String(badge.textContent || '')));
            const inTransit = badges.find(badge => /in transit/i.test(String(badge.textContent || '')));
            const delivered = badges.find(badge => /delivered/i.test(String(badge.textContent || '')));

            const pickupAlreadyConfirmed = Boolean(pickedUp && pickedUp.classList.contains('bg-success'));
            if (pickupAlreadyConfirmed || pickupStates.has(status)) {
                setBadgeState(pickedUp, 'bg-success');
                if (pickedUp) pickedUp.title = 'Carrier pickup confirmed by persisted journey state';
            } else if (labelOrTrackingStageReached(row)) {
                setBadgeState(pickedUp, 'bg-danger');
                if (pickedUp) pickedUp.title = 'Label/tracking ready · waiting for actual carrier pickup';
            }

            if (movementStates.has(status)) setBadgeState(inTransit, 'bg-success');
            if (status === 'delivered') setBadgeState(delivered, 'bg-success');
        });
    }

    function loadLegacy() {
        if (document.querySelector('script[data-bt38-fbm-tracking-legacy="1"]')) {
            alignPersistedLifecycle();
            return;
        }
        const legacy = document.createElement('script');
        legacy.src = '/static/js/fbm_tracking_journey_legacy.js';
        legacy.dataset.bt38FbmTrackingLegacy = '1';
        legacy.onload = alignPersistedLifecycle;
        legacy.onerror = alignPersistedLifecycle;
        document.head.appendChild(legacy);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', alignPersistedLifecycle, {once: true});
    } else {
        alignPersistedLifecycle();
    }

    if (document.querySelector('script[data-bt38-ebay-native-bootstrap="1"]')) {
        loadLegacy();
        return;
    }

    const nativeScript = document.createElement('script');
    nativeScript.src = '/static/js/fbm_ebay_shipping_alignment.js';
    nativeScript.dataset.bt38EbayNativeBootstrap = '1';
    nativeScript.onload = loadLegacy;
    nativeScript.onerror = loadLegacy;
    document.head.appendChild(nativeScript);
})(document);
