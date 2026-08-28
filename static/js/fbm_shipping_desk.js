(function () {
  'use strict';

  const packlinkProUrl = 'https://pro.packlink.com/';
  const selected = () => Array.from(document.querySelectorAll('.fbm-order-checkbox:checked')).map(el => el.value);
  const qs = (sel, root=document) => root.querySelector(sel);
  const qsa = (sel, root=document) => Array.from(root.querySelectorAll(sel));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Accept':'application/json','Content-Type':'application/json', ...(options.headers || {})},
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.success !== true) {
      const error = new Error(payload.message || `HTTP ${response.status}`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function parcelFor(card) {
    const kg = Number(qs('.parcel-weight-kg', card)?.value || 0);
    const grams = Number(qs('.parcel-weight-g', card)?.value || 0);
    const total = kg + (grams / 1000);
    const positive = value => {
      const number = Number(value || 0);
      return number > 0 ? number : null;
    };
    return {
      weight_kg: total > 0 ? Number(total.toFixed(3)) : null,
      length_cm: positive(qs('[data-field="length_cm"]', card)?.value),
      width_cm: positive(qs('[data-field="width_cm"]', card)?.value),
      height_cm: positive(qs('[data-field="height_cm"]', card)?.value),
    };
  }

  function splitWeight(weight) {
    const value = Number(weight || 0);
    if (!(value > 0)) return {kg:'', grams:''};
    const total = Math.round(value * 1000);
    return {kg:String(Math.floor(total / 1000)), grams:String(total % 1000)};
  }

  function priceText(value) {
    if (value == null) return 'Price unavailable';
    if (typeof value === 'number') return `£${value.toFixed(2)}`;
    const amount = value.value ?? value.amount ?? value.total ?? value.price;
    const currency = value.unit ?? value.currency ?? value.currencyCode ?? 'GBP';
    if (amount == null) return 'Price unavailable';
    return `${currency === 'GBP' ? '£' : esc(currency) + ' '}${Number(amount).toFixed(2)}`;
  }

  function updateSelection() {
    const ids = selected();
    const count = qs('#selectedOrderCount');
    const button = qs('#readyToShipSelected');
    const all = qs('#selectAllOrders');
    if (count) count.textContent = `${ids.length} selected`;
    if (button) button.disabled = ids.length === 0;
    const boxes = qsa('.fbm-order-checkbox');
    if (all) {
      all.checked = boxes.length > 0 && ids.length === boxes.length;
      all.indeterminate = ids.length > 0 && ids.length < boxes.length;
    }
  }

  function setModalState({loading=false, error='', subtitle=''}) {
    const loadingBox = qs('#fbmShippingLoading');
    const errorBox = qs('#fbmShippingError');
    if (loadingBox) loadingBox.classList.toggle('d-none', !loading);
    if (errorBox) {
      errorBox.textContent = error || '';
      errorBox.classList.toggle('d-none', !error);
    }
    if (subtitle) qs('#fbmShippingModalSubtitle').textContent = subtitle;
  }

  async function openShipping(orderIds) {
    if (!orderIds.length) return;
    const modalElement = qs('#fbmShippingModal');
    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    qs('#fbmShippingOrders').innerHTML = '';
    setModalState({loading:true, subtitle:`${orderIds.length} order${orderIds.length === 1 ? '' : 's'}`});
    modal.show();
    try {
      const payload = await jsonFetch(`/fbm/shipping-options?order_ids=${encodeURIComponent(orderIds.join(','))}`);
      renderOrders(payload.orders || []);
      setModalState({loading:false, subtitle:`${payload.selected_count || 0} order${payload.selected_count === 1 ? '' : 's'} ready for shipping choice`});
    } catch (error) {
      setModalState({loading:false, error:error.message});
    }
  }

  function providerButton(provider, orderId) {
    const available = provider.available === true;
    const cls = provider.kind === 'marketplace' ? 'btn-outline-primary' : provider.kind === 'provider' ? 'btn-outline-success' : 'btn-outline-secondary';
    return `<button type="button" class="btn btn-sm ${cls} provider-action" data-provider="${esc(provider.provider)}" data-order-id="${orderId}" ${available ? '' : 'disabled'}>${esc(provider.label)}</button>`;
  }

  function renderOrders(orders) {
    const root = qs('#fbmShippingOrders');
    if (!orders.length) {
      root.innerHTML = '<div class="text-muted py-4">No selected orders are eligible for FBM shipping.</div>';
      return;
    }
    root.innerHTML = orders.map(order => {
      const weight = splitWeight(order.parcel?.weight_kg);
      const providers = (order.providers || []).map(p => providerButton(p, order.id)).join('');
      const prime = order.is_prime === true ? '<span class="badge bg-warning text-dark ms-2">Prime / SFP</span>' : '';
      const providerMessage = (order.providers || []).filter(p => p.available === true).map(p => `<div class="small text-muted">${esc(p.label)}: ${esc(p.message || '')}</div>`).join('');
      return `<div class="card mb-3" data-order-id="${order.id}">
        <div class="card-header d-flex justify-content-between align-items-start gap-2 flex-wrap">
          <div><strong>${esc(order.platform)} · ${esc(order.marketplace_order_id)}</strong>${prime}<div class="small text-muted">${esc(order.store_name || '')} · Qty ${esc(order.quantity || 0)} · ${esc(order.postcode || 'Postcode missing')}</div></div>
          <div class="parcel-save-state small text-muted">Parcel values save automatically</div>
        </div>
        <div class="card-body">
          <div class="row g-2 align-items-end mb-3">
            <div class="col-6 col-md-2"><label class="form-label small">Weight kg</label><input class="form-control form-control-sm parcel-weight-kg" inputmode="numeric" value="${esc(weight.kg)}"></div>
            <div class="col-6 col-md-2"><label class="form-label small">Grams</label><input class="form-control form-control-sm parcel-weight-g" inputmode="numeric" value="${esc(weight.grams)}"></div>
            <div class="col-4 col-md-2"><label class="form-label small">Length cm</label><input class="form-control form-control-sm parcel-field" data-field="length_cm" inputmode="decimal" value="${esc(order.parcel?.length_cm || '')}"></div>
            <div class="col-4 col-md-2"><label class="form-label small">Width cm</label><input class="form-control form-control-sm parcel-field" data-field="width_cm" inputmode="decimal" value="${esc(order.parcel?.width_cm || '')}"></div>
            <div class="col-4 col-md-2"><label class="form-label small">Height cm</label><input class="form-control form-control-sm parcel-field" data-field="height_cm" inputmode="decimal" value="${esc(order.parcel?.height_cm || '')}"></div>
          </div>
          <div class="d-flex flex-wrap gap-2 mb-2">${providers}</div>
          ${providerMessage}
          <div class="rate-results mt-3" data-order-id="${order.id}"></div>
        </div>
      </div>`;
    }).join('');
    wireParcelAutosave(root);
  }

  function wireParcelAutosave(root) {
    qsa('.card[data-order-id]', root).forEach(card => {
      let timer = null;
      const save = () => {
        clearTimeout(timer);
        timer = setTimeout(() => autosaveParcel(card), 450);
      };
      qsa('.parcel-weight-kg,.parcel-weight-g,.parcel-field', card).forEach(input => {
        input.addEventListener('input', save);
        input.addEventListener('change', save);
      });
    });
  }

  async function autosaveParcel(card) {
    const parcel = parcelFor(card);
    if (!Object.values(parcel).some(value => value != null)) return;
    const state = qs('.parcel-save-state', card);
    if (state) state.textContent = 'Saving…';
    try {
      await jsonFetch(`/fbm/orders/${card.dataset.orderId}/parcel`, {method:'POST', body:JSON.stringify({parcel})});
      if (state) {
        state.textContent = 'Saved';
        state.className = 'parcel-save-state small text-success';
      }
    } catch (error) {
      if (state) {
        state.textContent = `Save failed: ${error.message}`;
        state.className = 'parcel-save-state small text-danger';
      }
    }
  }

  async function getAmazonRates(card) {
    const box = qs('.rate-results', card);
    box.innerHTML = '<div class="text-muted">Loading Amazon Buy Shipping rates…</div>';
    try {
      const payload = await jsonFetch(`/fbm/orders/${card.dataset.orderId}/amazon/rates`, {method:'POST', body:JSON.stringify({parcel:parcelFor(card)})});
      renderAmazonRates(card, payload);
    } catch (error) {
      box.innerHTML = `<div class="alert alert-danger py-2">${esc(error.message)}</div>`;
    }
  }

  function renderAmazonRates(card, payload) {
    const box = qs('.rate-results', card);
    box.dataset.amazonPanel = '1';
    if (!(payload.rates || []).length) {
      box.innerHTML = '<div class="alert alert-warning py-2">Amazon returned no eligible shipping services.</div>';
      return;
    }
    box.innerHTML = `<div class="fw-semibold mb-2">Amazon Buy Shipping</div>${payload.rates.map((rate, index) => {
      const rateId = rate.rate_id ?? rate.id ?? rate.service_id ?? '';
      const docs = Array.isArray(rate.supported_documents) ? rate.supported_documents : [];
      const royal = String(rate.carrier_name || '').toLowerCase().includes('royal mail');
      return `<div class="border rounded p-2 mb-2 amazon-rate" data-rate-id="${esc(rateId)}" data-quote-id="${esc(payload.quote_id)}">
        <div class="d-flex justify-content-between gap-2"><div><strong>${esc(rate.carrier_name || 'Carrier')}</strong> · ${esc(rate.service_name || rate.service || 'Service')}</div><strong>${priceText(rate.price)}</strong></div>
        <div class="small text-muted">${docs.length ? esc(docs.map(d => d.format || '').filter(Boolean).join(' / ')) : 'Amazon label format'}</div>
        ${royal ? `<label class="form-check small mt-2"><input class="form-check-input amazon-terms" type="checkbox"> <span class="form-check-label">Accept Royal Mail terms for this purchase</span></label>` : ''}
        <button type="button" class="btn btn-sm btn-primary mt-2 amazon-buy-rate" data-index="${index}">Buy postage</button>
      </div>`;
    }).join('')}`;
  }

  async function buyAmazon(button) {
    const rate = button.closest('.amazon-rate');
    const card = button.closest('.card[data-order-id]');
    const royalTerms = qs('.amazon-terms', rate);
    if (royalTerms && !royalTerms.checked) {
      alert('Accept Royal Mail terms before purchasing this service.');
      return;
    }
    button.disabled = true;
    button.textContent = 'Buying…';
    try {
      const payload = await jsonFetch(`/fbm/orders/${card.dataset.orderId}/amazon/purchase`, {
        method:'POST',
        body:JSON.stringify({
          confirm_purchase:'BUY_POSTAGE',
          quote_id:Number(rate.dataset.quoteId),
          rate_id:rate.dataset.rateId,
          document_index:0,
          accept_carrier_terms:royalTerms ? royalTerms.checked : true,
        }),
      });
      const box = qs('.rate-results', card);
      box.innerHTML = `<div class="alert alert-success py-2">${esc(payload.message || 'Postage purchased.')}${payload.tracking_number ? `<div><code>${esc(payload.tracking_number)}</code></div>` : ''}</div>`;
      if (payload.label) {
        box.dataset.label = JSON.stringify(payload.label);
        if (qs('#qzAutoPrint')?.checked && window.BT38FBMQZ) {
          try { await window.BT38FBMQZ.printLabel(payload.label); }
          catch (printError) { box.insertAdjacentHTML('beforeend', `<div class="small text-warning">Label saved; print failed: ${esc(printError.message)}</div>`); }
        }
      }
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Buy postage';
      alert(error.message);
    }
  }

  async function getPacklinkRates(card) {
    const box = qs('.rate-results', card);
    box.innerHTML = '<div class="text-muted">Loading Packlink rates…</div>';
    try {
      const payload = await jsonFetch(`/fbm/orders/${card.dataset.orderId}/packlink/rates`, {method:'POST', body:JSON.stringify({parcel:parcelFor(card)})});
      renderPacklinkRates(card, payload);
    } catch (error) {
      box.innerHTML = `<div class="alert alert-danger py-2">${esc(error.message)}</div>`;
    }
  }

  function renderPacklinkRates(card, payload) {
    const box = qs('.rate-results', card);
    if (!(payload.rates || []).length) {
      box.innerHTML = '<div class="alert alert-warning py-2">Packlink returned no services.</div>';
      return;
    }
    box.innerHTML = `<div class="fw-semibold mb-2">Packlink PRO</div>${payload.rates.map(rate => {
      const rateId = rate.rate_id ?? rate.id ?? rate.service_id ?? '';
      return `<div class="border rounded p-2 mb-2 packlink-rate" data-rate-id="${esc(rateId)}" data-quote-id="${esc(payload.quote_id)}">
        <div class="d-flex justify-content-between gap-2"><div><strong>${esc(rate.carrier_name || rate.carrier || 'Carrier')}</strong> · ${esc(rate.service_name || rate.service || 'Service')}</div><strong>${priceText(rate.price)}</strong></div>
        <button type="button" class="btn btn-sm btn-success mt-2 packlink-draft">Prepare Packlink</button>
      </div>`;
    }).join('')}`;
  }

  async function createPacklinkDraft(button) {
    const rate = button.closest('.packlink-rate');
    const card = button.closest('.card[data-order-id]');
    button.disabled = true;
    try {
      const payload = await jsonFetch(`/fbm/orders/${card.dataset.orderId}/packlink/draft`, {
        method:'POST',
        body:JSON.stringify({confirm_create:'CREATE_PACKLINK_DRAFT', quote_id:Number(rate.dataset.quoteId), rate_id:rate.dataset.rateId}),
      });
      const box = qs('.rate-results', card);
      box.innerHTML = `<div class="alert alert-info py-2">Packlink reference <code>${esc(payload.provider_reference || '')}</code>. Payment is completed in Packlink.</div>
        <a class="btn btn-sm btn-success me-2" href="${packlinkProUrl}" target="_blank" rel="noopener">Pay in Packlink</a>
        <button type="button" class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(payload.shipment_id)}">Check label</button>`;
    } catch (error) {
      button.disabled = false;
      alert(error.message);
    }
  }

  async function checkPacklink(button) {
    const shipmentId = button.dataset.shipmentId;
    const box = button.closest('.rate-results') || button.closest('td') || button.parentElement;
    button.disabled = true;
    try {
      const payload = await jsonFetch(`/fbm/shipments/${shipmentId}/packlink/status`);
      if (payload.label_ready && payload.label) {
        if (box.classList.contains('rate-results')) box.dataset.label = JSON.stringify(payload.label);
        box.insertAdjacentHTML('beforeend', `<div class="alert alert-success py-2 mt-2">Label ready${payload.tracking ? ` · <code>${esc(payload.tracking)}</code>` : ''}</div>`);
        if (qs('#qzAutoPrint')?.checked && window.BT38FBMQZ) {
          try { await window.BT38FBMQZ.printLabel(payload.label); }
          catch (printError) { box.insertAdjacentHTML('beforeend', `<div class="small text-warning">Label saved; print failed: ${esc(printError.message)}</div>`); }
        }
      } else {
        box.insertAdjacentHTML('beforeend', `<div class="small text-muted mt-2">${esc(payload.blocking_reason || payload.provider_status || 'Waiting for Packlink payment/label.')}</div>`);
      }
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function renderManual(card) {
    const box = qs('.rate-results', card);
    box.innerHTML = `<div class="fw-semibold mb-2">Manual / own carrier</div>
      <div class="row g-2"><div class="col-md-4"><input class="form-control form-control-sm manual-carrier" placeholder="Carrier"></div><div class="col-md-4"><input class="form-control form-control-sm manual-service" placeholder="Service"></div><div class="col-md-4"><input class="form-control form-control-sm manual-tracking" placeholder="Tracking number"></div></div>
      <button type="button" class="btn btn-sm btn-outline-primary mt-2 manual-dispatch">Confirm dispatch</button>`;
  }

  async function manualDispatch(button) {
    const card = button.closest('.card[data-order-id]');
    try {
      const payload = await jsonFetch(`/fbm/orders/${card.dataset.orderId}/manual/dispatch`, {
        method:'POST',
        body:JSON.stringify({
          carrier:qs('.manual-carrier', card)?.value,
          service:qs('.manual-service', card)?.value,
          tracking_number:qs('.manual-tracking', card)?.value,
          confirm_dispatch:'CONFIRM_MANUAL_DISPATCH',
        }),
      });
      qs('.rate-results', card).innerHTML = `<div class="alert alert-success py-2">${esc(payload.message || 'Dispatch confirmed.')}</div>`;
    } catch (error) {
      alert(error.message);
    }
  }

  async function testPacklinkConnection() {
    const status = qs('#packlinkConnectionStatus');
    status.textContent = 'Testing…';
    try {
      const payload = await jsonFetch('/fbm/providers/packlink/connection');
      status.textContent = payload.message || 'Packlink connected.';
      status.className = 'small text-success';
    } catch (error) {
      status.textContent = error.message;
      status.className = 'small text-danger';
    }
  }

  async function connectQz() {
    const status = qs('#qzStatus');
    try {
      await window.BT38FBMQZ.connect();
      const printers = await window.BT38FBMQZ.printers();
      const select = qs('#qzPrinter');
      select.innerHTML = '<option value="">Saved/default printer</option>' + printers.map(name => `<option>${esc(name)}</option>`).join('');
      status.textContent = 'QZ connected.';
      status.className = 'small text-success';
    } catch (error) {
      status.textContent = error.message;
      status.className = 'small text-danger';
    }
  }

  document.addEventListener('click', event => {
    const target = event.target.closest('button,a');
    if (!target) return;
    if (target.matches('.fbm-shipping-options')) return openShipping([target.dataset.orderId]);
    if (target.id === 'readyToShipSelected') return openShipping(selected());
    if (target.matches('.provider-action')) {
      const card = target.closest('.card[data-order-id]');
      if (target.dataset.provider === 'amazon_buy_shipping') return getAmazonRates(card);
      if (target.dataset.provider === 'packlink') return getPacklinkRates(card);
      if (target.dataset.provider === 'manual') return renderManual(card);
    }
    if (target.matches('.amazon-buy-rate')) return buyAmazon(target);
    if (target.matches('.packlink-draft')) return createPacklinkDraft(target);
    if (target.matches('.packlink-status,.packlink-existing-status')) return checkPacklink(target);
    if (target.matches('.manual-dispatch')) return manualDispatch(target);
    if (target.id === 'packlinkConnectionTest') return testPacklinkConnection();
    if (target.id === 'qzConnect') return connectQz();
    if (target.id === 'qzSavePrinter') {
      try {
        const name = qs('#qzPrinter').value;
        window.BT38FBMQZ.savePrinter(name);
        qs('#qzStatus').textContent = `Saved printer: ${name}`;
      } catch (error) { qs('#qzStatus').textContent = error.message; }
    }
  });

  document.addEventListener('change', event => {
    if (event.target.matches('.fbm-order-checkbox')) updateSelection();
    if (event.target.id === 'selectAllOrders') {
      qsa('.fbm-order-checkbox').forEach(box => { box.checked = event.target.checked; });
      updateSelection();
    }
  });

  document.addEventListener('click', event => {
    const row = event.target.closest('.fbm-order-row');
    if (!row || event.target.closest('[data-no-row-click="1"],button,a,input,select,textarea')) return;
    openShipping([row.dataset.orderId]);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', updateSelection, {once:true});
  else updateSelection();
})();
