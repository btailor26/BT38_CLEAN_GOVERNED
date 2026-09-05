/* BT38 FBM asset alignment bootstrap.
 * Load native eBay shipping before the preserved tracking journey so the
 * capture-phase native handler owns eBay Shipping and the legacy Seller Hub
 * handler cannot run first.
 *
 * Journey colour rule:
 * - persisted label/postage created without carrier acceptance => Picked up RED
 * - persisted carrier pickup/acceptance => Picked up GREEN
 * - persisted carrier movement => In transit GREEN
 * - delivery timing colour remains owned by the delivery-promise journey
 *
 * Asset rule:
 * - every dynamically loaded FBM journey asset carries the deployed entry
 *   asset version; when an older unversioned page is still open, a per-load
 *   revision prevents stale child scripts surviving a browser refresh.
 */
(function (document) {
    'use strict';

    const bootstrapScript = document.currentScript;
    const bootstrapUrl = bootstrapScript && bootstrapScript.src
        ? new URL(bootstrapScript.src, window.location.href)
        : null;
    const assetRevision = bootstrapUrl && bootstrapUrl.searchParams.get('v')
        ? bootstrapUrl.searchParams.get('v')
        : String(Date.now());

    function assetUrl(path) {
        const separator = String(path || '').includes('?') ? '&' : '?';
        return `${path}${separator}v=${encodeURIComponent(assetRevision)}`;
    }

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
    const fbmPageSizes = [15, 30, 50, 100];
    const fbmMaxLoaded = 300;
    const fbmPagerAnchor = 'bt38FbmPager';
    const fbmSearchSessionKey = 'bt38:fbm:search';

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
        return String(row && row.dataset ? row.dataset.labelReady || '' : '') === '1';
    }

    function setBadgeState(badge, stateClass) {
        if (!badge) return;
        badge.classList.remove('bg-success', 'bg-danger', 'bg-primary', 'bg-light', 'text-muted', 'text-dark', 'border');
        String(stateClass || '').split(/\s+/).filter(Boolean).forEach(name => badge.classList.add(name));
    }

    function alignDispatchedShippingAuthority(row, status) {
        if (!row || !row.children) return;
        const shippingCell = row.children[5];
        const shipmentCell = row.children[7];
        if (!shippingCell || !shipmentCell) return;

        const dispatchedStates = new Set([
            'partially_shipped',
            'shipped',
            'accepted',
            'carrier_accepted',
            'collected',
            'picked_up',
            'in_transit',
            'out_for_delivery',
            'delivered',
            'return_requested',
            'returned',
            'refund_requested',
            'refunded',
            'replacement_requested',
            'replacement',
            'case_open',
            'dispute',
            'chargeback'
        ]);
        const trackingNode = shipmentCell.querySelector('code');
        if (!dispatchedStates.has(status) && !trackingNode) return;

        const carrierNode = shipmentCell.querySelector('strong');
        const carrier = String(carrierNode && carrierNode.textContent || '').trim();
        if (!carrier) return;

        shippingCell.replaceChildren();
        const label = document.createElement('div');
        label.className = 'small text-muted';
        label.textContent = 'Dispatch authority';
        const authority = document.createElement('strong');
        authority.textContent = carrier;
        const note = document.createElement('div');
        note.className = 'fbm-row-note text-muted';
        note.textContent = 'Persisted shipment evidence';
        shippingCell.append(label, authority, note);
    }

    function installFbmSearch() {
        if (document.getElementById('bt38FbmSearchForm')) return;
        const table = document.querySelector('.fbm-order-row')?.closest('table');
        if (!table) return;
        const card = table.closest('.card');
        const tableWrap = table.closest('.table-responsive');
        if (!card || !tableWrap) return;

        const form = document.createElement('form');
        form.id = 'bt38FbmSearchForm';
        form.className = 'p-3 border-bottom';
        form.setAttribute('role', 'search');
        form.innerHTML = '<div class="d-flex gap-2" style="max-width:760px">' +
            '<input id="bt38FbmSearchInput" class="form-control" name="q" type="search" autocomplete="off" ' +
            'placeholder="Search by order, SKU, product, marketplace, postcode, carrier or tracking..." aria-label="Search FBM orders">' +
            '<button class="btn btn-dark px-4" type="submit">Search</button>' +
            '</div><div id="bt38FbmSearchResult" class="small text-muted mt-2 d-none"></div>';
        card.insertBefore(form, tableWrap);

        const input = form.querySelector('#bt38FbmSearchInput');
        const result = form.querySelector('#bt38FbmSearchResult');

        // Match Warehouse's local-filter model: cache server-rendered row text once,
        // then search that bounded in-memory cache. No DB, marketplace, provider,
        // shipment-status or network request is allowed from this search path.
        const rows = Array.from(table.querySelectorAll('.fbm-order-row')).map(element => ({
            el: element,
            text: String(element.textContent || '').trim().toLowerCase(),
            dataset: Object.assign({}, element.dataset)
        }));

        const params = new URLSearchParams(window.location.search);
        const requestedPageSize = Number.parseInt(params.get('page_size') || '15', 10);
        const pageSize = fbmPageSizes.includes(requestedPageSize) ? requestedPageSize : 15;
        const requestedPage = Math.max(1, Number.parseInt(params.get('page') || '1', 10) || 1);
        const requestedLimit = Math.max(pageSize, Number.parseInt(params.get('limit') || String(pageSize), 10) || pageSize);

        let footer = Array.from(card.children).find(element => element.classList && element.classList.contains('card-footer')) || null;
        const serverHasMore = Boolean(footer && footer.querySelector('#fbmExpandOrders'));
        if (!footer) {
            footer = document.createElement('div');
            footer.className = 'card-footer';
            card.appendChild(footer);
        }
        footer.id = fbmPagerAnchor;

        const loadedPages = Math.max(1, Math.ceil(rows.length / pageSize));
        const currentPage = Math.min(requestedPage, loadedPages);

        function rowMatches(row, query) {
            if (!query) return true;
            const haystack = `${row.text} ${Object.values(row.dataset).join(' ')}`.toLowerCase();
            return haystack.includes(query);
        }

        function buildUrl(targetPage, targetPageSize, targetLimit) {
            const next = new URLSearchParams(window.location.search);
            next.set('page_size', String(targetPageSize));
            next.set('page', String(targetPage));
            next.set('limit', String(Math.min(fbmMaxLoaded, Math.max(targetPageSize, targetLimit))));
            return `${window.location.pathname}?${next.toString()}#${fbmPagerAnchor}`;
        }

        function renderPager() {
            const knownPages = Math.max(1, Math.ceil(rows.length / pageSize));
            const maxPages = Math.max(1, Math.ceil(fbmMaxLoaded / pageSize));
            const canLoadAnotherPage = serverHasMore && requestedLimit < fbmMaxLoaded;
            const pageLinksThrough = Math.min(maxPages, knownPages + (canLoadAnotherPage ? 1 : 0));

            const sizeLinks = fbmPageSizes.map(size => {
                const active = size === pageSize;
                return `<a class="btn btn-sm ${active ? 'btn-dark' : 'btn-outline-secondary'}" href="${buildUrl(1, size, size)}" aria-current="${active ? 'page' : 'false'}">${size}</a>`;
            }).join('');

            const pageLinks = [];
            for (let page = 1; page <= pageLinksThrough; page += 1) {
                const active = page === currentPage;
                const targetLimit = page <= knownPages ? requestedLimit : Math.max(requestedLimit, page * pageSize);
                pageLinks.push(`<a class="btn btn-sm ${active ? 'btn-dark' : 'btn-outline-secondary'}" href="${buildUrl(page, pageSize, targetLimit)}" aria-current="${active ? 'page' : 'false'}">${page}</a>`);
            }

            const first = rows.length ? ((currentPage - 1) * pageSize) + 1 : 0;
            const last = Math.min(rows.length, currentPage * pageSize);
            const moreText = serverHasMore ? ' Older orders load only when another page is requested.' : '';
            footer.className = 'card-footer d-flex justify-content-between align-items-center flex-wrap gap-2';
            footer.innerHTML =
                `<span class="small text-muted">Showing ${first}-${last} of ${rows.length} loaded FBM orders · Page ${currentPage}.${moreText}</span>` +
                '<div class="d-flex flex-wrap gap-2 align-items-center justify-content-end">' +
                    '<span class="small text-muted me-1">Show:</span>' +
                    sizeLinks +
                    '<span class="small text-muted ms-2 me-1">Page:</span>' +
                    pageLinks.join('') +
                '</div>';
        }

        function applySearch() {
            const query = String(input.value || '').trim().toLowerCase();
            const pageStart = (currentPage - 1) * pageSize;
            const pageEnd = pageStart + pageSize;
            let visible = 0;

            rows.forEach((row, index) => {
                const match = rowMatches(row, query);
                const inPage = index >= pageStart && index < pageEnd;
                const show = query ? match : inPage;
                row.el.hidden = !show;
                if (show) visible += 1;
            });

            try {
                window.sessionStorage.setItem(fbmSearchSessionKey, input.value || '');
            } catch (error) {
                // Browser storage is optional; search remains fully local without it.
            }

            result.classList.toggle('d-none', !query);
            if (query) result.textContent = `${visible} matching order${visible === 1 ? '' : 's'} in the loaded FBM browser session`;
        }

        renderPager();

        try {
            const savedSearch = window.sessionStorage.getItem(fbmSearchSessionKey) || '';
            if (savedSearch) input.value = savedSearch;
        } catch (error) {
            // Ignore storage restrictions and keep the local search available.
        }

        applySearch();

        form.addEventListener('submit', event => {
            event.preventDefault();
            event.stopPropagation();
            applySearch();
        });
        input.addEventListener('input', applySearch);
        input.addEventListener('change', applySearch);

        if (window.location.hash === `#${fbmPagerAnchor}`) {
            window.requestAnimationFrame(() => footer.scrollIntoView({block: 'end'}));
        }
    }

    function alignPersistedLifecycle() {
        installFbmSearch();
        document.querySelectorAll('.fbm-order-row').forEach(row => {
            const status = String(row.dataset.lifecycleStatus || '').trim().toLowerCase();
            alignDispatchedShippingAuthority(row, status);
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
                if (pickedUp) pickedUp.title = 'Label / postage created · waiting for carrier collection';
            }

            if (movementStates.has(status)) setBadgeState(inTransit, 'bg-success');
            if (status === 'delivered' && delivered && !delivered.classList.contains('bg-danger')) {
                setBadgeState(delivered, 'bg-success');
            }
        });
    }

    let governedLiveRefreshPending = false;

    async function applyCommittedFbmSnapshot() {
        if (governedLiveRefreshPending) return;
        governedLiveRefreshPending = true;
        try {
            const response = await fetch(window.location.href, {
                method: 'GET',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'Accept': 'text/html'}
            });
            if (!response.ok) throw new Error(`FBM refresh failed (HTTP ${response.status})`);
            const html = await response.text();
            const parsed = new DOMParser().parseFromString(html, 'text/html');
            const dataNode = parsed.getElementById('bt38FbmLifecycleTabsData');
            const countsNode = parsed.getElementById('bt38FbmLifecycleCountsData');
            if (!dataNode || !countsNode || typeof window.BT38FBMApplyCommittedSnapshot !== 'function') return;
            const nextData = JSON.parse(dataNode.textContent || '{}');
            const nextCounts = JSON.parse(countsNode.textContent || '{}');

            document.querySelectorAll('.fbm-order-row').forEach(row => {
                const freshRow = parsed.querySelector(`.fbm-order-row[data-order-id="${CSS.escape(String(row.dataset.orderId || ''))}"]`);
                if (!freshRow) return;
                if (freshRow.dataset.lifecycleStatus) row.dataset.lifecycleStatus = freshRow.dataset.lifecycleStatus;
                row.dataset.labelReady = freshRow.dataset.labelReady || '0';
            });
            window.BT38FBMApplyCommittedSnapshot(nextData, nextCounts);
            alignPersistedLifecycle();
        } catch (error) {
            console.warn('[BT38 FBM] committed session refresh unavailable', error);
        } finally {
            governedLiveRefreshPending = false;
        }
    }

    function refreshFbmFromGovernedEvent() {
        // Reuse the application shell's single governed SSE connection. This is
        // one event-driven DB snapshot read after commit: no poller, no second
        // EventSource, no marketplace/provider read and no full-page refresh.
        const activeModal = document.querySelector('#fbmShippingModal.show, #fbmTrackingJourneyModal.show');
        if (activeModal) {
            activeModal.addEventListener('hidden.bs.modal', () => void applyCommittedFbmSnapshot(), {once: true});
            return;
        }
        void applyCommittedFbmSnapshot();
    }

    window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent);

    function loadDeliveryPromiseAlignment() {
        if (document.querySelector('script[data-bt38-fbm-delivery-promise-alignment="1"]')) return;
        const alignment = document.createElement('script');
        alignment.src = assetUrl('/static/js/fbm_delivery_promise_journey_alignment.js');
        alignment.dataset.bt38FbmDeliveryPromiseAlignment = '1';
        document.head.appendChild(alignment);
    }

    function loadLegacy() {
        if (document.querySelector('script[data-bt38-fbm-tracking-legacy="1"]')) {
            alignPersistedLifecycle();
            loadDeliveryPromiseAlignment();
            return;
        }
        const legacy = document.createElement('script');
        legacy.src = assetUrl('/static/js/fbm_tracking_journey_legacy.js');
        legacy.dataset.bt38FbmTrackingLegacy = '1';
        legacy.onload = function () {
            alignPersistedLifecycle();
            loadDeliveryPromiseAlignment();
        };
        legacy.onerror = function () {
            alignPersistedLifecycle();
            loadDeliveryPromiseAlignment();
        };
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
    nativeScript.src = assetUrl('/static/js/fbm_ebay_shipping_alignment.js');
    nativeScript.dataset.bt38EbayNativeBootstrap = '1';
    nativeScript.onload = loadLegacy;
    nativeScript.onerror = loadLegacy;
    document.head.appendChild(nativeScript);
})(document);
