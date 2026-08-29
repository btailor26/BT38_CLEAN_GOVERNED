(function () {
    'use strict';

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, function (char) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char];
        });
    }

    function firstValue(object, keys) {
        if (!object || typeof object !== 'object') return null;
        for (const key of keys) {
            const value = object[key];
            if (value !== undefined && value !== null && String(value).trim() !== '') return value;
        }
        return null;
    }

    function eventTime(event) {
        return firstValue(event, ['date', 'datetime', 'timestamp', 'created_at', 'event_date', 'time']);
    }

    function eventTitle(event) {
        return firstValue(event, ['description', 'message', 'label', 'status', 'state', 'event', 'type', 'code']) || 'Provider update';
    }

    function eventDetail(event) {
        const title = String(eventTitle(event));
        const value = firstValue(event, ['status', 'state', 'event', 'type', 'code']);
        return value && String(value) !== title ? String(value) : '';
    }

    function eventLocation(event) {
        const value = firstValue(event, ['location', 'city', 'address', 'place']);
        if (value && typeof value === 'object') {
            return firstValue(value, ['name', 'city', 'address', 'label']) || '';
        }
        return value || '';
    }

    function parseDate(value) {
        if (value === undefined || value === null || value === '') return null;
        let normalized = value;
        const text = String(value).trim();
        if (/^-?\d+(?:\.\d+)?$/.test(text)) {
            const numeric = Number(text);
            if (Number.isFinite(numeric)) normalized = Math.abs(numeric) < 1000000000000 ? numeric * 1000 : numeric;
        }
        const parsed = new Date(normalized);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function formatDate(value) {
        const parsed = parseDate(value);
        if (!parsed) return value ? String(value) : 'Time not supplied';
        return parsed.toLocaleString('en-GB', {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
    }

    function deliveredEvent(history) {
        for (const event of history) {
            const sourceText = [firstValue(event, ['status', 'state', 'event', 'type', 'code']), firstValue(event, ['description', 'message', 'label'])].filter(Boolean).join(' ').toLowerCase();
            if (/\bdelivered\b|delivery complete|successfully delivered/.test(sourceText)) return event;
        }
        return null;
    }

    function performanceBlock(payload, history) {
        const promise = payload.marketplace_promise || null;
        if (!promise || !promise.latest_delivery_at) return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';
        const latest = parseDate(promise.latest_delivery_at);
        if (!latest) return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';
        const delivered = deliveredEvent(history);
        const deliveredAt = delivered ? parseDate(eventTime(delivered)) : null;
        const providerStatus = String(payload.provider_status || '').toLowerCase();
        const providerSaysDelivered = /\bdelivered\b|delivery complete|successfully delivered/.test(providerStatus);
        if (deliveredAt) {
            if (deliveredAt.getTime() <= latest.getTime()) return '<span class="badge bg-success">Delivered on time</span>';
            const days = Math.max(1, Math.ceil((deliveredAt.getTime() - latest.getTime()) / 86400000));
            return `<span class="badge bg-danger">Delivered late · ${days} day${days === 1 ? '' : 's'}</span>`;
        }
        if (providerSaysDelivered) return '<span class="badge bg-success">Delivered · provider delivery time unavailable</span>';
        if (Date.now() > latest.getTime()) return '<span class="badge bg-danger">Late · not delivered</span>';
        return '<span class="badge bg-success">On track</span>';
    }

    function promiseHtml(promise) {
        if (!promise) return '<div class="text-muted small">Marketplace delivery promise is not available for this marketplace.</div>';
        if (!promise.earliest_delivery_at && !promise.latest_delivery_at) return `<div class="text-muted small">${esc(promise.unavailable_reason || 'Marketplace delivery promise unavailable.')}</div>`;
        const start = promise.earliest_delivery_at ? formatDate(promise.earliest_delivery_at) : '—';
        const end = promise.latest_delivery_at ? formatDate(promise.latest_delivery_at) : '—';
        return `<div class="small"><strong>Marketplace promise:</strong> ${esc(start)} → ${esc(end)}</div>`;
    }

    function historyHtml(history) {
        if (!history.length) return '<div class="alert alert-light border mb-0">No additional carrier scan events are stored yet.</div>';
        return history.map(function (event) {
            const detail = eventDetail(event), location = eventLocation(event);
            return `<div class="border-start border-3 ps-3 py-2 mb-2"><div class="fw-semibold">${esc(eventTitle(event))}</div>${detail ? `<div class="small text-muted">${esc(detail)}</div>` : ''}${location ? `<div class="small text-muted">${esc(location)}</div>` : ''}<div class="small text-muted">${esc(formatDate(eventTime(event)))}</div></div>`;
        }).join('');
    }

    function marketplaceOrderIdFromRow(row) {
        if (!row || !row.children[2]) return '';
        const orderCell = row.children[2];
        const firstNode = Array.from(orderCell.childNodes).find(function (node) {
            return node.nodeType === Node.TEXT_NODE && String(node.textContent || '').trim();
        });
        return String(firstNode ? firstNode.textContent : orderCell.textContent || '').trim().split(/\s+/)[0];
    }

    function marketplaceTrackingLink(platform, orderId) {
        const normalized = String(platform || '').toLowerCase();
        if (!orderId) return '';
        if (normalized.includes('ebay')) {
            return `https://www.ebay.co.uk/mesh/ord/details?orderid=${encodeURIComponent(orderId)}`;
        }
        if (normalized.includes('amazon')) {
            return `https://sellercentral.amazon.co.uk/orders-v3/order/${encodeURIComponent(orderId)}`;
        }
        return '';
    }

    function marketplaceJourneyHtml(button, warning) {
        const row = button.closest('.fbm-order-row');
        const tracking = button.dataset.trackingNumber || String(button.textContent || '').trim() || '—';
        const platform = button.dataset.platform || (row ? String(row.children[1]?.querySelector('strong')?.textContent || 'Marketplace').trim() : 'Marketplace');
        const shipmentCell = row ? row.children[7] : null;
        const carrier = button.dataset.carrier || (shipmentCell ? String(shipmentCell.querySelector('strong')?.textContent || platform).trim() : platform);
        const journeyCell = row ? row.children[8] : null;
        const badges = journeyCell ? Array.from(journeyCell.querySelectorAll('.badge')) : [];
        const milestoneHtml = badges.slice(0, 4).map(function (badge) {
            const text = String(badge.textContent || '').replace(/^\d+\s*·\s*/, '').trim();
            const active = badge.classList.contains('bg-success') || badge.classList.contains('bg-danger') || badge.classList.contains('bg-primary');
            const statusClass = badge.classList.contains('bg-danger') ? 'bg-danger' : (active ? 'bg-success' : 'bg-light text-muted border');
            return `<div class="d-flex align-items-center justify-content-between border rounded px-3 py-2 mb-2"><span class="fw-semibold">${esc(text)}</span><span class="badge ${statusClass}">${active ? 'Confirmed' : 'Pending'}</span></div>`;
        }).join('');
        const promiseText = journeyCell ? Array.from(journeyCell.querySelectorAll('.small')).map(function (node) { return String(node.textContent || '').trim(); }).find(function (text) { return text.startsWith('Deliver by:') || text.startsWith('Delivery promise'); }) : '';
        const source = /ebay/i.test(platform) ? 'eBay' : (/amazon/i.test(platform) ? 'Amazon' : platform);
        const orderId = marketplaceOrderIdFromRow(row);
        const trackingUrl = marketplaceTrackingLink(platform, orderId);
        const marketplaceButton = trackingUrl ? `<a class="btn btn-sm btn-outline-primary mt-2" href="${esc(trackingUrl)}" target="_blank" rel="noopener noreferrer">Open ${esc(source)} tracking</a>` : '';
        const warningHtml = warning ? `<div class="alert alert-warning py-2 mb-3"><strong>Live carrier history unavailable.</strong><div class="small">${esc(warning)} BT38 is showing the persisted tracking and journey state instead.</div></div>` : '';
        return `${warningHtml}<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3"><div><div class="fw-semibold">${esc(carrier)}</div><div class="small">Tracking: <code>${esc(tracking)}</code></div><div class="small text-muted">Journey source: ${esc(source)} / persisted BT38 state</div>${marketplaceButton}</div></div>${promiseText ? `<div class="border rounded p-3 mb-3"><div class="small"><strong>${esc(promiseText)}</strong></div></div>` : ''}<div class="fw-semibold mb-2">Shipment journey</div>${milestoneHtml || '<div class="alert alert-light border mb-0">Tracking received. Carrier milestones have not been confirmed yet.</div>'}`;
    }

    async function openJourney(button) {
        const modalElement = document.getElementById('fbmTrackingJourneyModal');
        const body = document.getElementById('fbmTrackingJourneyBody');
        const subtitle = document.getElementById('fbmTrackingJourneySubtitle');
        if (!modalElement || !body) return;
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
        if (subtitle) subtitle.textContent = button.dataset.trackingNumber || 'Tracking journey';

        if (button.dataset.journeySource === 'marketplace' || !button.dataset.shipmentId) {
            body.innerHTML = marketplaceJourneyHtml(button);
            return;
        }

        body.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Reading Packlink/carrier journey…</div>';
        try {
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(button.dataset.shipmentId)}/packlink/status`, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
            const history = Array.isArray(payload.tracking_history) ? payload.tracking_history : [];
            const tracking = payload.tracking_number || payload.tracking || button.dataset.trackingNumber || '—';
            body.innerHTML = `<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3"><div><div class="fw-semibold">${esc(payload.carrier || 'Packlink/carrier')} · ${esc(payload.service || '')}</div><div class="small">Tracking: <code>${esc(tracking)}</code></div><div class="small text-muted">Journey source: Packlink / carrier platform</div></div><div>${performanceBlock(payload, history)}</div></div><div class="border rounded p-3 mb-3">${promiseHtml(payload.marketplace_promise)}</div><div class="fw-semibold mb-2">Platform journey</div>${historyHtml(history)}`;
        } catch (error) {
            body.innerHTML = marketplaceJourneyHtml(button, error.message);
        }
    }

    function installMarketplaceJourneyLinks() {
        document.querySelectorAll('.fbm-order-row').forEach(function (row) {
            const marketplace = String(row.children[1]?.querySelector('strong')?.textContent || '').trim();
            const shipmentCell = row.children[7];
            if (!shipmentCell) return;
            const carrier = String(shipmentCell.querySelector('strong')?.textContent || marketplace).trim();

            shipmentCell.querySelectorAll('a[href*="ebay.co.uk/mesh/ord/details"], a[href*="sellercentral.amazon.co.uk/orders-v3/order/"]').forEach(function (link) {
                const tracking = String(link.textContent || '').trim();
                link.removeAttribute('href');
                link.removeAttribute('target');
                link.removeAttribute('rel');
                link.setAttribute('role', 'button');
                link.setAttribute('tabindex', '0');
                link.classList.add('fbm-tracking-journey');
                link.dataset.journeySource = 'marketplace';
                link.dataset.trackingNumber = tracking;
                link.dataset.platform = marketplace;
                link.dataset.carrier = carrier;
            });

            shipmentCell.querySelectorAll('.bt38-db-tracking code').forEach(function (code) {
                if (code.closest('.fbm-tracking-journey')) return;
                const tracking = String(code.textContent || '').trim();
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-link btn-sm p-0 align-baseline fbm-tracking-journey';
                button.dataset.journeySource = 'marketplace';
                button.dataset.trackingNumber = tracking;
                button.dataset.platform = marketplace;
                button.dataset.carrier = carrier;
                code.replaceWith(button);
                button.appendChild(code);
            });
        });
    }

    function installEbayShippingHandoff() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.ebayShippingHandoffInstalled === '1') return;
        root.dataset.ebayShippingHandoffInstalled = '1';
        const align = function () {
            root.querySelectorAll('.provider-action[data-provider="ebay_shipping"]').forEach(function (button) {
                button.disabled = false;
                button.textContent = 'Open eBay shipping';
                button.title = 'Open this order in eBay to check or buy marketplace postage.';
            });
        };
        new MutationObserver(align).observe(root, {childList: true, subtree: true});
        align();
    }

    function openEbayShipping(button) {
        const id = String(button.dataset.orderId || '');
        const sourceRow = document.querySelector(`.fbm-order-row[data-order-id="${CSS.escape(id)}"]`);
        const orderId = marketplaceOrderIdFromRow(sourceRow);
        if (!orderId) {
            window.alert('BT38 could not resolve the eBay order ID for this row.');
            return;
        }
        window.open(`https://www.ebay.co.uk/mesh/ord/details?orderid=${encodeURIComponent(orderId)}`, '_blank', 'noopener,noreferrer');
    }

    function installManualShippingButton() {
        const readyButton = document.getElementById('readyToShipSelected');
        if (!readyButton || document.getElementById('manualShippingButton')) return;
        const manualButton = document.createElement('a');
        manualButton.id = 'manualShippingButton'; manualButton.href = '/fbm/manual'; manualButton.className = 'btn btn-sm btn-outline-primary';
        manualButton.innerHTML = '<i data-feather="plus-circle" class="me-1"></i>Manual Shipping';
        readyButton.parentNode.insertBefore(manualButton, readyButton);
        if (window.feather) window.feather.replace();
    }

    function installBulkActionBar() {
        const checkboxes = Array.from(document.querySelectorAll('.fbm-order-checkbox'));
        if (!checkboxes.length || document.getElementById('fbmBulkActionBar')) return;
        const style = document.createElement('style');
        style.id = 'fbmBulkActionBarStyle';
        style.textContent = `.fbm-bulk-action-bar{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;display:flex;align-items:center;gap:10px;padding:10px 12px;background:#fff;border:1px solid #d1d5db;border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.22)}.fbm-bulk-action-bar[hidden]{display:none!important}.fbm-bulk-selected-pill{background:#0ea5e9;color:#fff;border-radius:999px;padding:6px 14px;font-weight:700;font-size:12px;white-space:nowrap}.fbm-bulk-cancel{border:1px solid #d1d5db;background:#fff;color:#111827;border-radius:6px;padding:8px 18px;font-weight:600}.fbm-bulk-select{min-width:180px;border:1px solid #d1d5db;border-radius:6px;background:#fff;padding:8px 34px 8px 12px;font-weight:600;color:#111827}@media(max-width:640px){.fbm-bulk-action-bar{width:calc(100% - 24px);justify-content:space-between}.fbm-bulk-cancel{padding:8px 12px}.fbm-bulk-select{min-width:0;flex:1}}`;
        document.head.appendChild(style);
        const bar = document.createElement('div');
        bar.id = 'fbmBulkActionBar'; bar.className = 'fbm-bulk-action-bar'; bar.hidden = true;
        bar.innerHTML = `<div class="fbm-bulk-selected-pill"><span id="fbmBulkSelectedCount">0</span> order(s) selected</div><button type="button" class="fbm-bulk-cancel" id="fbmBulkCancel">Cancel</button><select class="fbm-bulk-select" id="fbmBulkActionSelect" aria-label="Bulk action"><option value="">Select action</option><option value="ready_to_ship">Ready to Ship</option><option value="print_labels">Print Labels</option><option value="check_shipments">Check Shipments</option><option value="delete">Delete</option></select>`;
        document.body.appendChild(bar);
        const count = document.getElementById('fbmBulkSelectedCount'), selectAll = document.getElementById('selectAllOrders'), actionSelect = document.getElementById('fbmBulkActionSelect');
        const selectedIds = function () { return checkboxes.filter(function (box) { return box.checked; }).map(function (box) { return Number(box.value); }).filter(Boolean); };
        const updateBar = function () { const selected = selectedIds().length; if (count) count.textContent = String(selected); bar.hidden = selected === 0; };
        checkboxes.forEach(function (box) { box.addEventListener('change', updateBar); });
        if (selectAll) selectAll.addEventListener('change', function () { setTimeout(updateBar, 0); });
        document.getElementById('fbmBulkCancel').addEventListener('click', function () {
            checkboxes.forEach(function (box) { if (!box.checked) return; box.checked = false; box.dispatchEvent(new Event('change', {bubbles: true})); });
            if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
            if (actionSelect) actionSelect.value = ''; updateBar();
        });
        actionSelect.addEventListener('change', async function () {
            const action = actionSelect.value;
            if (action !== 'delete') { actionSelect.value = ''; return; }
            const ids = selectedIds();
            if (!ids.length) { actionSelect.value = ''; return; }
            if (!window.confirm(`Delete ${ids.length} selected FBM record${ids.length === 1 ? '' : 's'} from BT38? This does not cancel the order on the marketplace or cancel/refund any carrier label.`)) { actionSelect.value = ''; return; }
            actionSelect.disabled = true;
            try {
                const response = await fetch('/governed/fbm/orders/delete', {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify({order_ids: ids, confirm_delete: 'DELETE_SELECTED_FBM_RECORDS'})});
                const payload = await response.json().catch(function () { return {}; });
                if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
                window.location.reload();
            } catch (error) { window.alert(error.message); actionSelect.value = ''; actionSelect.disabled = false; }
        });
        updateBar();
    }

    function packlinkStatusKind(payload) {
        if (payload && payload.label_ready) return 'label';
        if (payload && payload.ready_to_ship === true) return 'ready';
        const status = String(payload && payload.provider_status || '').trim().toUpperCase().replace(/[\s-]+/g, '_');
        if ((payload && payload.blocking_reason) || (Array.isArray(payload && payload.blockers) && payload.blockers.length)) return 'blocked';
        if (!status) return 'draft';
        if (status.includes('AWAITING_COMPLETION')) return 'blocked';
        if (/(CANCEL|ERROR|FAIL|REJECT)/.test(status)) return 'issue';
        if (status.includes('READY_TO_SHIP') || status.includes('READY_FOR_PAYMENT') || status.includes('READY_TO_PAY') || status.includes('PAYMENT_PENDING')) return 'ready';
        return 'draft';
    }

    function blockerText(payload) {
        if (payload && payload.blocking_reason) return String(payload.blocking_reason);
        const blockers = Array.isArray(payload && payload.blockers) ? payload.blockers : [];
        if (blockers.length && blockers[0]) return String(blockers[0].label || blockers[0].message || blockers[0].code || 'Packlink requires confirmation');
        return 'Packlink requires confirmation';
    }

    function packlinkHandoffButton() {
        return '<a class="btn btn-sm btn-primary" href="https://pro.packlink.com/" target="_blank" rel="noopener">Open Packlink PRO</a>';
    }

    function renderPacklinkStatus(box, payload, shipmentId) {
        const kind = packlinkStatusKind(payload);
        box.dataset.packlinkShipmentId = String(shipmentId || '');
        if (kind === 'label') {
            box.innerHTML = `<div class="alert alert-success mb-2"><strong>✓ Packlink label ready.</strong></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Open label details</button>`;
            return;
        }
        if (kind === 'blocked') {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Action required</strong><div class="small">${esc(blockerText(payload))}</div></div>${packlinkHandoffButton()}`;
            return;
        }
        if (kind === 'ready') {
            box.innerHTML = `<div class="alert alert-success mb-2"><strong>✓ Ready to Ship</strong></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check label</button>`;
            return;
        }
        if (kind === 'issue') {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Packlink needs attention</strong><div class="small">${esc(blockerText(payload))}</div></div>${packlinkHandoffButton()}`;
            return;
        }
        box.innerHTML = `<div class="alert alert-info mb-2"><strong>✓ Packlink draft created.</strong><div class="small">Continue in Packlink to complete the handoff.</div></div>${packlinkHandoffButton()}`;
    }

    async function refreshPacklinkBox(box, shipmentId) {
        if (!box || !shipmentId || box.dataset.packlinkStatusLoading === '1') return;
        box.dataset.packlinkStatusLoading = '1';
        box.dataset.packlinkShipmentId = String(shipmentId);
        try {
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(shipmentId)}/packlink/status`, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
            renderPacklinkStatus(box, payload, shipmentId);
        } catch (error) {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Packlink status unavailable</strong><div class="small">${esc(error.message)}</div></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check again</button>`;
        } finally {
            delete box.dataset.packlinkStatusLoading;
        }
    }

    function installPacklinkHandoff() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.packlinkHandoffInstalled === '1') return;
        root.dataset.packlinkHandoffInstalled = '1';
        const addHandoff = function () {
            root.querySelectorAll('.rate-results').forEach(function (box) {
                const text = String(box.textContent || '');
                if (!text.includes('Packlink shipment prepared.') || !text.includes('Reference:')) return;
                const statusButton = box.querySelector('.packlink-status[data-shipment-id]');
                const shipmentId = statusButton ? statusButton.dataset.shipmentId : '';
                if (shipmentId) refreshPacklinkBox(box, shipmentId);
            });
        };
        new MutationObserver(addHandoff).observe(root, {childList: true, subtree: true}); addHandoff();
    }

    function installExistingPacklinkDraftAlignment() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.packlinkDraftAlignmentInstalled === '1') return;
        root.dataset.packlinkDraftAlignmentInstalled = '1';
        const align = function () {
            root.querySelectorAll('.card[data-order-id]').forEach(function (card) {
                const orderId = card.dataset.orderId;
                const sourceRow = document.querySelector(`.fbm-order-row[data-order-id="${CSS.escape(String(orderId))}"]`);
                const existing = sourceRow ? sourceRow.querySelector('.packlink-existing-status[data-shipment-id]') : null;
                const box = card.querySelector(`.rate-results[data-order-id="${CSS.escape(String(orderId))}"]`);
                if (!existing || !box || box.dataset.existingPacklinkAligned === '1' || String(box.textContent || '').trim()) return;
                box.dataset.existingPacklinkAligned = '1';
                const shipmentId = existing.dataset.shipmentId;
                box.dataset.packlinkShipmentId = shipmentId;
                box.innerHTML = '<div class="text-muted">Checking Packlink status…</div>';
                refreshPacklinkBox(box, shipmentId);
            });
        };
        new MutationObserver(align).observe(root, {childList: true, subtree: true}); align();
    }

    function installFbmShippingModeSetting() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.fbmShippingModeInstalled === '1') return;
        root.dataset.fbmShippingModeInstalled = '1';
        const align = function () {
            root.querySelectorAll('.card[data-order-id]').forEach(function (card) {
                if (card.querySelector('.fbm-shipping-mode-setting')) return;
                const heading = Array.from(card.querySelectorAll('.fw-semibold')).find(function (element) {
                    return String(element.textContent || '').trim() === 'Choose shipping route';
                });
                if (!heading) return;
                const setting = document.createElement('div');
                setting.className = 'fbm-shipping-mode-setting d-flex align-items-center gap-2 mb-2 small';
                setting.innerHTML = '<span class="text-muted">Fulfilment</span><span class="badge bg-secondary">FBM</span>';
                heading.insertAdjacentElement('afterend', setting);
            });
        };
        new MutationObserver(align).observe(root, {childList: true, subtree: true});
        align();
    }

    document.addEventListener('click', function (event) {
        const ebayShippingButton = event.target.closest('.provider-action[data-provider="ebay_shipping"]');
        if (ebayShippingButton) {
            event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
            openEbayShipping(ebayShippingButton);
            return;
        }
        const statusButton = event.target.closest('.packlink-status[data-shipment-id]');
        if (statusButton) {
            const box = statusButton.closest('.rate-results');
            if (!box) return;
            event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
            refreshPacklinkBox(box, statusButton.dataset.shipmentId);
            return;
        }
        const journeyButton = event.target.closest('.fbm-tracking-journey');
        if (journeyButton) {
            event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); openJourney(journeyButton);
        }
    }, true);

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const journeyButton = event.target.closest('.fbm-tracking-journey[data-journey-source="marketplace"]');
        if (!journeyButton) return;
        event.preventDefault(); openJourney(journeyButton);
    });

    installMarketplaceJourneyLinks();
    installEbayShippingHandoff();
    installManualShippingButton();
    installBulkActionBar();
    installPacklinkHandoff();
    installExistingPacklinkDraftAlignment();
    installFbmShippingModeSetting();
})();