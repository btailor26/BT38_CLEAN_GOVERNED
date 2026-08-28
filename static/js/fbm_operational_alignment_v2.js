(function () {
    'use strict';

    const loadedOrders = new Set();
    const parcelTimers = new Map();

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, function (char) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char];
        });
    }

    async function jsonFetch(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
            ...(options || {})
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok || payload.success !== true) {
            throw new Error(payload.message || `HTTP ${response.status}`);
        }
        return payload;
    }

    function primeBadge() {
        return '<span class="bt38-prime-mark" aria-label="Prime" title="Seller Fulfilled Prime">' +
            '<span class="bt38-prime-tick">✓</span><span class="bt38-prime-word">prime</span></span>';
    }

    function installStyles() {
        if (document.getElementById('bt38FbmOperationalStyles')) return;
        const style = document.createElement('style');
        style.id = 'bt38FbmOperationalStyles';
        style.textContent = `
            .bt38-prime-mark{display:inline-flex;align-items:center;gap:1px;margin-top:4px;padding:1px 3px;background:#f5f5f5;line-height:1;border-radius:2px;white-space:nowrap}
            .bt38-prime-tick{color:#ff9900;font-size:22px;font-weight:900;line-height:.8;transform:rotate(-8deg);display:inline-block}
            .bt38-prime-word{color:#00a8e1;font-size:18px;font-weight:700;letter-spacing:-.8px;font-family:Arial,sans-serif}
            .bt38-promise{font-size:11px;margin-top:5px;font-weight:600;white-space:nowrap}
            .bt38-promise.bt38-late{color:#dc3545}
            .bt38-promise.bt38-on-time{color:#198754}
            .bt38-promise.bt38-pending{color:#6c757d}
            .bt38-parcel-save-state{font-size:11px;margin-top:5px;min-height:16px}
            .bt38-shipping-service{font-size:11px;color:#6c757d;margin-top:3px}
        `;
        document.head.appendChild(style);
    }

    function removePageClutter() {
        document.querySelectorAll('.alert.alert-info').forEach(function (node) {
            if (/source of truth:/i.test(node.textContent || '')) node.remove();
        });
        document.querySelectorAll('span.badge.bg-primary.fs-6').forEach(function (node) {
            if (/governed shipping/i.test(node.textContent || '')) node.remove();
        });
    }

    function journeyCell(row) {
        return row && row.children && row.children.length >= 9 ? row.children[8] : null;
    }

    function stripJourneyNumbers(row) {
        const cell = journeyCell(row);
        if (!cell) return;
        const badges = Array.from(cell.querySelectorAll('.badge')).slice(0, 3);
        badges.forEach(function (badge) {
            badge.textContent = (badge.textContent || '').replace(/^\s*[123]\s*·\s*/, '');
        });
    }

    function setBadgeState(badge, kind, text) {
        if (!badge) return;
        badge.className = 'badge';
        if (kind === 'success') badge.classList.add('bg-success');
        else if (kind === 'danger') badge.classList.add('bg-danger');
        else badge.classList.add('bg-light', 'text-muted', 'border');
        badge.textContent = text;
    }

    function applyPrime(row, isPrime) {
        if (!isPrime || !row || row.children.length < 2) return;
        const cell = row.children[1];
        const old = Array.from(cell.querySelectorAll('.badge')).find(function (badge) {
            return /prime|sfp/i.test(badge.textContent || '');
        });
        if (old) {
            old.outerHTML = primeBadge();
        } else if (!cell.querySelector('.bt38-prime-mark')) {
            cell.insertAdjacentHTML('beforeend', `<div>${primeBadge()}</div>`);
        }
    }

    function applyShippingService(row, service) {
        if (!service || !row || row.children.length < 7) return;
        const cell = row.children[6];
        let line = cell.querySelector('.bt38-shipping-service');
        if (!line) {
            line = document.createElement('div');
            line.className = 'bt38-shipping-service';
            cell.appendChild(line);
        }
        line.textContent = service;
    }

    function applyJourney(row, payload) {
        const cell = journeyCell(row);
        if (!cell) return;
        stripJourneyNumbers(row);
        const badges = Array.from(cell.querySelectorAll('.badge')).slice(0, 3);
        if (badges.length < 3) return;

        const state = String(payload.journey_state || 'not_started');
        const pickedUp = ['accepted', 'in_transit', 'out_for_delivery', 'delivered'].includes(state);
        const inTransit = ['in_transit', 'out_for_delivery', 'delivered'].includes(state);
        const delivered = state === 'delivered';
        const promise = payload.promise || {};

        setBadgeState(badges[0], pickedUp ? 'success' : 'neutral', 'Picked up');
        setBadgeState(badges[1], inTransit ? 'success' : 'neutral', 'In transit');

        if (promise.delivered_late) {
            setBadgeState(badges[2], 'danger', 'Delivered late');
        } else if (promise.late && !delivered) {
            setBadgeState(badges[2], 'danger', 'Delayed');
        } else if (delivered) {
            setBadgeState(badges[2], 'success', 'Delivered');
        } else {
            setBadgeState(badges[2], 'neutral', 'Delivered');
        }

        let promiseLine = cell.querySelector('.bt38-promise');
        if (!promiseLine) {
            promiseLine = document.createElement('div');
            promiseLine.className = 'bt38-promise';
            cell.appendChild(promiseLine);
        }
        promiseLine.className = 'bt38-promise ' + (
            promise.delivered_late || promise.late ? 'bt38-late' :
            promise.delivered_on_time ? 'bt38-on-time' : 'bt38-pending'
        );
        promiseLine.textContent = promise.available && promise.label
            ? `Promise: ${promise.label}`
            : 'Promise unavailable';
    }

    async function hydrateRow(row) {
        if (!row) return;
        const orderId = row.dataset.orderId;
        if (!orderId || loadedOrders.has(orderId)) return;
        loadedOrders.add(orderId);
        stripJourneyNumbers(row);
        try {
            const payload = await jsonFetch(`/governed/fbm/orders/${encodeURIComponent(orderId)}/operational`);
            applyPrime(row, payload.is_prime === true);
            applyShippingService(row, payload.shipping_service);
            applyJourney(row, payload);
        } catch (error) {
            loadedOrders.delete(orderId);
            const cell = journeyCell(row);
            if (cell && !cell.querySelector('.bt38-promise')) {
                const warning = document.createElement('div');
                warning.className = 'bt38-promise bt38-late';
                warning.textContent = 'Promise check unavailable';
                cell.appendChild(warning);
            }
        }
    }

    function installLazyOperationalHydration() {
        const rows = Array.from(document.querySelectorAll('.fbm-order-row'));
        rows.forEach(stripJourneyNumbers);
        if (!('IntersectionObserver' in window)) {
            rows.slice(0, 50).forEach(hydrateRow);
            return;
        }
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                hydrateRow(entry.target);
            });
        }, {rootMargin: '300px 0px'});
        rows.forEach(function (row) { observer.observe(row); });
    }

    function collectParcel(orderId, host) {
        const result = {};
        const kgEl = host.querySelector(`.parcel-weight-kg[data-order-id="${orderId}"]`);
        const gEl = host.querySelector(`.parcel-weight-g[data-order-id="${orderId}"]`);
        const kg = kgEl && kgEl.value !== '' ? Number(kgEl.value) : 0;
        const grams = gEl && gEl.value !== '' ? Number(gEl.value) : 0;
        if (kg > 0 || grams > 0) result.weight_kg = kg + (grams / 1000);
        host.querySelectorAll(`.parcel-field[data-order-id="${orderId}"]`).forEach(function (input) {
            if (input.value !== '') result[input.dataset.field] = Number(input.value);
        });
        return result;
    }

    function parcelStatus(host) {
        let state = host.querySelector('.bt38-parcel-save-state');
        if (!state) {
            state = document.createElement('div');
            state.className = 'bt38-parcel-save-state text-muted';
            const parcelBox = host.querySelector('.parcel-weight-kg')?.closest('.border.rounded.p-3');
            if (parcelBox) parcelBox.appendChild(state);
        }
        return state;
    }

    async function saveParcel(orderId, host) {
        const parcel = collectParcel(orderId, host);
        if (!Object.keys(parcel).length) return;
        const state = parcelStatus(host);
        if (state) {
            state.className = 'bt38-parcel-save-state text-muted';
            state.textContent = 'Saving…';
        }
        try {
            await jsonFetch(`/governed/fbm/orders/${encodeURIComponent(orderId)}/parcel`, {
                method: 'POST',
                body: JSON.stringify({parcel: parcel})
            });
            if (state) {
                state.className = 'bt38-parcel-save-state text-success';
                state.textContent = 'Saved';
            }
        } catch (error) {
            if (state) {
                state.className = 'bt38-parcel-save-state text-danger';
                state.textContent = `Not saved · ${error.message}`;
            }
        }
    }

    function queueParcelSave(input) {
        const orderId = input.dataset.orderId;
        if (!orderId) return;
        const host = input.closest('.card[data-order-id]') || document;
        clearTimeout(parcelTimers.get(orderId));
        parcelTimers.set(orderId, setTimeout(function () {
            saveParcel(orderId, host);
        }, 650));
    }

    function alignPrimeShippingCard(card) {
        const orderId = card.dataset.orderId;
        if (!orderId || card.dataset.bt38OperationalAligned === '1') return;
        card.dataset.bt38OperationalAligned = '1';
        jsonFetch(`/governed/fbm/orders/${encodeURIComponent(orderId)}/operational`).then(function (payload) {
            if (payload.is_prime === true) {
                const header = card.querySelector('.card-header');
                if (header) {
                    const old = Array.from(header.querySelectorAll('.badge')).find(function (badge) {
                        return /prime|sfp/i.test(badge.textContent || '');
                    });
                    if (old) old.outerHTML = primeBadge();
                    else header.querySelector('strong')?.insertAdjacentHTML('afterend', primeBadge());
                }
                card.querySelectorAll('.provider-action').forEach(function (button) {
                    if (button.dataset.provider === 'amazon_buy_shipping') return;
                    button.closest('.border.rounded.p-3.mb-2')?.remove();
                });
            }
            const header = card.querySelector('.card-header');
            if (header && payload.promise?.available && payload.promise?.label && !header.querySelector('.bt38-promise')) {
                const line = document.createElement('div');
                line.className = 'bt38-promise bt38-pending';
                line.textContent = `Promise: ${payload.promise.label}`;
                header.firstElementChild?.appendChild(line);
            }
            if (payload.shipping_service && header && !header.querySelector('.bt38-shipping-service')) {
                const line = document.createElement('div');
                line.className = 'bt38-shipping-service';
                line.textContent = payload.shipping_service;
                header.firstElementChild?.appendChild(line);
            }
        }).catch(function () {});
    }

    function installShippingModalAlignment() {
        const host = document.getElementById('fbmShippingOrders');
        if (!host) return;

        host.addEventListener('input', function (event) {
            const input = event.target.closest('.parcel-weight-kg,.parcel-weight-g,.parcel-field');
            if (input) queueParcelSave(input);
        });
        host.addEventListener('change', function (event) {
            const input = event.target.closest('.parcel-weight-kg,.parcel-weight-g,.parcel-field');
            if (input) queueParcelSave(input);
        });

        const observer = new MutationObserver(function () {
            host.querySelectorAll('.card[data-order-id]').forEach(alignPrimeShippingCard);
        });
        observer.observe(host, {childList: true, subtree: true});
        host.querySelectorAll('.card[data-order-id]').forEach(alignPrimeShippingCard);
    }

    function start() {
        installStyles();
        removePageClutter();
        installLazyOperationalHydration();
        installShippingModalAlignment();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
