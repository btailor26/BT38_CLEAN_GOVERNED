/* Existing FBM eBay Shipping slot -> native eBay rate/purchase path.
 * This capture-phase alignment supersedes the older Seller Hub handoff without
 * changing the shared FBM modal, shipment models or QZ printing bridge.
 */
(function () {
    'use strict';

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
            ...(options || {}),
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok || payload.success !== true) {
            const error = new Error(payload.message || `HTTP ${response.status}`);
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function priceText(price) {
        if (price == null) return 'Price unavailable';
        if (typeof price === 'number') return `£${price.toFixed(2)}`;
        const value = price.value ?? price.amount ?? price.total ?? price.price;
        const currency = price.currency ?? price.currencyCode ?? price.unit ?? 'GBP';
        if (value == null || Number.isNaN(Number(value))) return 'Price unavailable';
        return `${currency === 'GBP' ? '£' : esc(currency) + ' '}${Number(value).toFixed(2)}`;
    }

    function collectParcel(orderId) {
        const out = {};
        const kgEl = document.querySelector(`.parcel-weight-kg[data-order-id="${CSS.escape(String(orderId))}"]`);
        const gEl = document.querySelector(`.parcel-weight-g[data-order-id="${CSS.escape(String(orderId))}"]`);
        const kg = kgEl && kgEl.value !== '' ? Number(kgEl.value) : 0;
        const grams = gEl && gEl.value !== '' ? Number(gEl.value) : 0;
        if (kg > 0 || grams > 0) out.weight_kg = kg + (grams / 1000);
        document.querySelectorAll(`.parcel-field[data-order-id="${CSS.escape(String(orderId))}"]`).forEach(function (element) {
            if (element.value !== '') out[element.dataset.field] = Number(element.value);
        });
        return out;
    }

    function resultBox(orderId) {
        return document.querySelector(`.rate-results[data-order-id="${CSS.escape(String(orderId))}"]`);
    }

    function capabilityMessage(error) {
        const payload = error && error.payload || {};
        if (payload.limited_release_required) {
            return `${esc(error.message)}<div class="small mt-2">eBay has not enabled the Limited Release Logistics capability for this app/account. No postage purchase was attempted.</div>`;
        }
        if (payload.authorization_required) {
            return `${esc(error.message)}<div class="small mt-2">The connected eBay seller authorization does not currently permit the native shipping call. No postage purchase was attempted.</div>`;
        }
        return esc(error && error.message || 'eBay shipping request failed.');
    }

    function renderRates(orderId, payload) {
        const box = resultBox(orderId);
        if (!box) return;
        const rates = Array.isArray(payload.rates) ? payload.rates : [];
        if (!rates.length) {
            box.innerHTML = '<div class="alert alert-warning mb-0">eBay returned no native shipping services for this parcel.</div>';
            return;
        }
        box.dataset.ebayQuoteId = String(payload.quote_id || '');
        box.innerHTML = '<div class="fw-semibold mb-2">eBay Shipping services</div>' + rates.map(function (rate, index) {
            const carrier = rate.carrier_name || rate.shippingCarrierName || rate.carrier_id || rate.shippingCarrierCode || 'Carrier';
            const service = rate.service_name || rate.shippingServiceName || rate.service_id || rate.shippingServiceCode || 'Service';
            return `<div class="border rounded p-3 mb-2 d-flex justify-content-between align-items-start gap-3 flex-wrap"><div><strong>${esc(carrier)} · ${esc(service)}</strong><div class="small text-muted">${priceText(rate.price || rate.totalShippingCost || rate.baseShippingCost)}</div></div><button class="btn btn-sm btn-success ebay-native-buy" type="button" data-order-id="${esc(orderId)}" data-rate-index="${index}">Buy eBay label · ${priceText(rate.price || rate.totalShippingCost || rate.baseShippingCost)}</button></div>`;
        }).join('');
        box.dataset.ebayRates = JSON.stringify(rates);
    }

    async function loadRates(button) {
        const orderId = button.dataset.orderId;
        const box = resultBox(orderId);
        if (!orderId || !box) return;
        button.disabled = true;
        box.innerHTML = '<div class="text-muted">Fetching live eBay rates…</div>';
        try {
            const payload = await jsonFetch(`/fbm/orders/${encodeURIComponent(orderId)}/ebay/rates`, {
                method: 'POST',
                body: JSON.stringify({parcel: collectParcel(orderId)}),
            });
            renderRates(orderId, payload);
        } catch (error) {
            box.innerHTML = `<div class="alert alert-danger mb-0">${capabilityMessage(error)}</div>`;
        } finally {
            button.disabled = false;
        }
    }

    async function buyLabel(button) {
        const orderId = button.dataset.orderId;
        const box = resultBox(orderId);
        if (!orderId || !box) return;
        let rates = [];
        try { rates = JSON.parse(box.dataset.ebayRates || '[]'); } catch (_) { rates = []; }
        const rate = rates[Number(button.dataset.rateIndex || 0)] || null;
        const rateId = rate && (rate.rate_id || rate.rateId);
        const quoteId = Number(box.dataset.ebayQuoteId || 0);
        if (!rateId || !quoteId) {
            box.insertAdjacentHTML('beforeend', '<div class="alert alert-danger mt-2 mb-0">The selected eBay quote is incomplete. Get fresh rates.</div>');
            return;
        }
        if (!window.confirm('Buy this eBay postage label now?')) return;

        button.disabled = true;
        button.textContent = 'Buying eBay label…';
        try {
            const payload = await jsonFetch(`/fbm/orders/${encodeURIComponent(orderId)}/ebay/purchase`, {
                method: 'POST',
                body: JSON.stringify({quote_id: quoteId, rate_id: String(rateId), confirm_purchase: 'BUY_POSTAGE'}),
            });
            const labelLink = payload.label_url ? `<a class="btn btn-sm btn-outline-primary" href="${esc(payload.label_url)}" target="_blank" rel="noopener">Open / print label</a>` : '';
            box.innerHTML = `<div class="alert alert-success"><strong>eBay label purchased.</strong><div>${esc(payload.carrier || '')}${payload.service ? ' · ' + esc(payload.service) : ''}</div><div class="small">Tracking: <code>${esc(payload.tracking_number || 'pending')}</code></div><div class="small">eBay shipment: <code>${esc(payload.provider_shipment_id || '')}</code></div></div><div class="d-flex gap-2 flex-wrap">${labelLink}</div>`;

            const autoPrint = document.getElementById('qzAutoPrint');
            if (payload.label && autoPrint && autoPrint.checked && window.BT38FBMQZ) {
                try {
                    const printed = await window.BT38FBMQZ.printLabel(payload.label);
                    const qzStatus = document.getElementById('qzStatus');
                    if (qzStatus) {
                        qzStatus.className = 'small text-success mt-2';
                        qzStatus.textContent = `eBay label sent to ${printed.printer}`;
                    }
                } catch (printError) {
                    const qzStatus = document.getElementById('qzStatus');
                    if (qzStatus) {
                        qzStatus.className = 'small text-danger mt-2';
                        qzStatus.textContent = `eBay label is safely purchased; auto-print failed: ${printError.message}`;
                    }
                }
            }
            if (payload.label_error) {
                box.insertAdjacentHTML('beforeend', `<div class="alert alert-warning mt-2 mb-0">Label was purchased, but the immediate PDF download failed: ${esc(payload.label_error)}. Use Open / print label to retry the download only.</div>`);
            }
        } catch (error) {
            box.innerHTML = `<div class="alert alert-danger mb-0">${capabilityMessage(error)}</div>`;
        }
    }

    function alignButtons(root) {
        const scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll('.provider-action[data-provider="ebay_shipping"]').forEach(function (button) {
            button.disabled = false;
            button.textContent = 'Get eBay rates';
            button.title = 'Get eBay-native rates and purchase the selected label inside BT38.';
            button.dataset.ebayNativeAligned = '1';
        });
    }

    document.addEventListener('click', function (event) {
        const providerButton = event.target && event.target.closest ? event.target.closest('.provider-action[data-provider="ebay_shipping"]') : null;
        if (providerButton) {
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            loadRates(providerButton);
            return;
        }
        const buyButton = event.target && event.target.closest ? event.target.closest('.ebay-native-buy') : null;
        if (buyButton) {
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            buyLabel(buyButton);
        }
    }, true);

    const root = document.getElementById('fbmShippingOrders');
    if (root && window.MutationObserver) {
        const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node && node.nodeType === 1) alignButtons(node);
                });
            });
        });
        observer.observe(root, {childList: true, subtree: true});
    }
    alignButtons(document);
})();
