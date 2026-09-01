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

    function eventTime(event) {
        return firstValue(event, ['date', 'datetime', 'timestamp', 'created_at', 'event_date', 'time']);
    }

    function eventTitle(event) {
        return firstValue(event, ['description', 'message', 'label', 'status', 'state', 'event', 'type', 'code']) || 'Provider update';
    }

    function deliveredEvent(history) {
        for (const event of history) {
            const sourceText = [firstValue(event, ['status', 'state', 'event', 'type', 'code']), firstValue(event, ['description', 'message', 'label'])].filter(Boolean).join(' ').toLowerCase();
            if (/\bdelivered\b|delivery complete|successfully delivered/.test(sourceText)) return event;
        }
        return null;
    }

    function promiseFromRow(row) {
        const lines = row ? Array.from(row.querySelectorAll('.fbm-promise-line')) : [];
        let shipBy = '';
        let deliverBy = '';
        lines.forEach(function (line) {
            const text = String(line.textContent || '').replace(/\s+/g, ' ').trim();
            if (/^ship by\b/i.test(text)) shipBy = text.replace(/^ship by\s*/i, '').trim();
            if (/^deliver by\b/i.test(text)) deliverBy = text.replace(/^deliver by\s*/i, '').trim();
        });
        if (/^pending$/i.test(shipBy)) shipBy = '';
        if (/^pending$/i.test(deliverBy)) deliverBy = '';
        return {shipBy, deliverBy};
    }

    function promiseHtml(promise) {
        if (!promise.shipBy && !promise.deliverBy) {
            return '<div class="text-muted small">Marketplace delivery promise unavailable.</div>';
        }
        const ship = promise.shipBy ? `<div class="small"><strong>Ship by:</strong> ${esc(promise.shipBy)}</div>` : '';
        const deliver = promise.deliverBy ? `<div class="small"><strong>Deliver by:</strong> ${esc(promise.deliverBy)}</div>` : '';
        return `<div class="small text-muted mb-1">Marketplace promise · persisted BT38 order</div>${ship}${deliver}`;
    }

    function promisedDeliveryDate(promise, deliveredAt) {
        if (!promise.deliverBy) return null;
        const match = String(promise.deliverBy).trim().match(/^(\d{1,2})\s+([A-Za-z]{3})(?:\s+(\d{4}))?$/);
        if (!match) return null;
        const monthMap = {jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};
        const month = monthMap[String(match[2]).toLowerCase()];
        if (month === undefined) return null;
        const reference = deliveredAt || new Date();
        const year = match[3] ? Number(match[3]) : reference.getFullYear();
        return new Date(year, month, Number(match[1]), 23, 59, 59, 999);
    }

    function performanceBlock(promise, history, providerStatus) {
        const delivered = deliveredEvent(history);
        const deliveredAt = delivered ? parseDate(eventTime(delivered)) : null;
        const promisedAt = promisedDeliveryDate(promise, deliveredAt);
        const providerSaysDelivered = /\bdelivered\b|delivery complete|successfully delivered/.test(String(providerStatus || '').toLowerCase());
        if (!promisedAt) return '<span class="badge bg-secondary">Promise timing unavailable</span>';
        if (deliveredAt) {
            if (deliveredAt.getTime() <= promisedAt.getTime()) return '<span class="badge bg-success">Delivered on time</span>';
            const deliveredDay = new Date(deliveredAt.getFullYear(), deliveredAt.getMonth(), deliveredAt.getDate());
            const promisedDay = new Date(promisedAt.getFullYear(), promisedAt.getMonth(), promisedAt.getDate());
            const days = Math.max(1, Math.round((deliveredDay.getTime() - promisedDay.getTime()) / 86400000));
            return `<span class="badge bg-danger">Delivered late · ${days} day${days === 1 ? '' : 's'}</span>`;
        }
        if (providerSaysDelivered) return '<span class="badge bg-secondary">Delivered · delivery time unavailable</span>';
        if (Date.now() > promisedAt.getTime()) return '<span class="badge bg-danger">Late · not delivered</span>';
        return '<span class="badge bg-success">On track</span>';
    }

    function historyHtml(history) {
        if (!history.length) return '<div class="alert alert-light border mb-0">No additional carrier scan events are stored yet.</div>';
        return history.map(function (event) {
            const title = eventTitle(event);
            const detailValue = firstValue(event, ['status', 'state', 'event', 'type', 'code']);
            const detail = detailValue && String(detailValue) !== String(title) ? String(detailValue) : '';
            const locationValue = firstValue(event, ['location', 'city', 'address', 'place']);
            const location = locationValue && typeof locationValue === 'object' ? (firstValue(locationValue, ['name', 'city', 'address', 'label']) || '') : (locationValue || '');
            return `<div class="border-start border-3 ps-3 py-2 mb-2"><div class="fw-semibold">${esc(title)}</div>${detail ? `<div class="small text-muted">${esc(detail)}</div>` : ''}${location ? `<div class="small text-muted">${esc(location)}</div>` : ''}<div class="small text-muted">${esc(formatDate(eventTime(event)))}</div></div>`;
        }).join('');
    }

    function marketplaceJourneyHtml(button, row, warning) {
        const tracking = button.dataset.trackingNumber || String(button.textContent || '').trim() || '—';
        const marketplace = String(row?.children?.[1]?.querySelector('strong')?.textContent || button.dataset.platform || 'Marketplace').trim();
        const shipmentCell = row?.children?.[7] || null;
        const carrier = button.dataset.carrier || String(shipmentCell?.querySelector('strong')?.textContent || marketplace).trim();
        const journeyCell = row?.children?.[8] || null;
        const badges = journeyCell ? Array.from(journeyCell.querySelectorAll('.badge')) : [];
        const milestoneHtml = badges.slice(0, 4).map(function (badge) {
            const text = String(badge.textContent || '').replace(/^\d+\s*·\s*/, '').trim();
            const isRed = badge.classList.contains('bg-danger');
            const isConfirmed = badge.classList.contains('bg-success') || badge.classList.contains('bg-primary');
            const borderClass = isRed ? 'border-danger' : (isConfirmed ? 'border-success' : 'border-secondary');
            const statusClass = isRed ? 'bg-danger' : (isConfirmed ? 'bg-success' : 'bg-light text-muted border');
            let statusText = 'Pending';
            if (isRed) {
                statusText = /picked up/i.test(text) ? 'Waiting collection' : (/delivered/i.test(text) ? 'Late' : 'Attention');
            } else if (isConfirmed) {
                statusText = 'Confirmed';
            }
            return `<div class="border-start border-3 ${borderClass} ps-3 py-2 mb-2"><div class="d-flex align-items-center justify-content-between gap-3"><div class="fw-semibold">${esc(text)}</div><span class="badge ${statusClass}">${esc(statusText)}</span></div></div>`;
        }).join('');
        const warningHtml = warning ? `<div class="alert alert-warning py-2 mb-3"><strong>Live carrier history unavailable.</strong><div class="small">${esc(warning)} BT38 is showing persisted shipment state instead.</div></div>` : '';
        const promise = promiseFromRow(row);
        const promiseBlock = promise.shipBy || promise.deliverBy ? `<div class="border rounded p-3 mb-3">${promiseHtml(promise)}</div>` : '';
        return `${warningHtml}<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3"><div><div class="fw-semibold">${esc(carrier)}</div><div class="small">Tracking: <code>${esc(tracking)}</code></div><div class="small text-muted">Journey source: ${esc(marketplace)} / persisted BT38 state</div></div></div>${promiseBlock}<div class="fw-semibold mb-2">Shipment journey</div>${milestoneHtml || '<div class="alert alert-light border mb-0">Tracking received. Carrier milestones have not been confirmed yet.</div>'}`;
    }

    async function openAlignedJourney(button) {
        const row = button.closest('.fbm-order-row');
        const modalElement = document.getElementById('fbmTrackingJourneyModal');
        const body = document.getElementById('fbmTrackingJourneyBody');
        const subtitle = document.getElementById('fbmTrackingJourneySubtitle');
        if (!row || !modalElement || !body) return;
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
        if (subtitle) subtitle.textContent = button.dataset.trackingNumber || String(button.textContent || '').trim() || 'Tracking journey';

        if (button.dataset.journeySource === 'marketplace' || !button.dataset.shipmentId) {
            body.innerHTML = marketplaceJourneyHtml(button, row);
            return;
        }

        body.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Reading Packlink/carrier journey…</div>';
        try {
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(button.dataset.shipmentId)}/packlink/status`, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
            const history = Array.isArray(payload.tracking_history) ? payload.tracking_history : [];
            const tracking = payload.tracking_number || payload.tracking || button.dataset.trackingNumber || '—';
            const promise = promiseFromRow(row);
            const performance = performanceBlock(promise, history, payload.provider_status);
            body.innerHTML = `<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3"><div><div class="fw-semibold">${esc(payload.carrier || 'Packlink/carrier')} · ${esc(payload.service || '')}</div><div class="small">Tracking: <code>${esc(tracking)}</code></div><div class="small text-muted">Journey source: Packlink / carrier platform</div></div><div>${performance}</div></div><div class="border rounded p-3 mb-3">${promiseHtml(promise)}</div><div class="fw-semibold mb-2">Platform journey</div>${historyHtml(history)}`;
        } catch (error) {
            body.innerHTML = marketplaceJourneyHtml(button, row, error.message);
        }
    }

    function install() {
        if (document.documentElement.dataset.bt38PromiseJourneyAligned === '1') return;
        document.documentElement.dataset.bt38PromiseJourneyAligned = '1';
        document.addEventListener('click', function (event) {
            const button = event.target.closest('.fbm-tracking-journey');
            if (!button) return;
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            openAlignedJourney(button);
        }, true);
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            const button = event.target.closest('.fbm-tracking-journey');
            if (!button) return;
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            openAlignedJourney(button);
        }, true);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once: true});
    else install();
})();