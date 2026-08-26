// ======================================================
// BT38 WAREHOUSE GOVERNED CONTROLLER (CLEAN SAFE VERSION)
// NO RELOADS - NO FORM SUBMIT - GOVERNED ACTION ONLY
// ======================================================

(function () {
  'use strict';

  function warehouseActive() {
    return !!document.querySelector('.bt38-enterprise-stock .bt38-stock-table');
  }

  function selectedRows() {
    return Array.from(document.querySelectorAll('.bt38-row-select:checked'));
  }

  function updateActionBar() {
    const selected = selectedRows();
    const bar = document.getElementById('bt38FloatingActionBar');
    const count = document.getElementById('bt38SelectedCount');

    if (!bar || !count) return;

    count.textContent = selected.length;
    bar.hidden = selected.length === 0;
  }

  function clearSelection() {
    document.querySelectorAll('.bt38-row-select').forEach(cb => cb.checked = false);
    updateActionBar();
  }

  function showPushSuccess() {
    const existing = document.getElementById('bt38PushSuccessOverlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'bt38PushSuccessOverlay';
    overlay.setAttribute('role', 'presentation');
    overlay.style.position = 'fixed';
    overlay.style.inset = '0';
    overlay.style.zIndex = '100000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.padding = '18px';
    overlay.style.background = 'rgba(15, 23, 42, 0.30)';

    const card = document.createElement('div');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-labelledby', 'bt38PushSuccessTitle');
    card.style.width = 'min(440px, calc(100vw - 36px))';
    card.style.minHeight = '205px';
    card.style.boxSizing = 'border-box';
    card.style.position = 'relative';
    card.style.display = 'grid';
    card.style.gridTemplateColumns = '118px 1fr';
    card.style.alignItems = 'center';
    card.style.gap = '12px';
    card.style.padding = '24px 24px 22px';
    card.style.border = '1px solid rgba(37, 99, 235, 0.14)';
    card.style.borderRadius = '18px';
    card.style.background = 'linear-gradient(135deg, #ffffff 0%, #f7fbff 100%)';
    card.style.boxShadow = '0 22px 60px rgba(15, 23, 42, 0.24)';
    card.style.overflow = 'hidden';

    const robotWrap = document.createElement('div');
    robotWrap.style.position = 'relative';
    robotWrap.style.display = 'flex';
    robotWrap.style.alignItems = 'center';
    robotWrap.style.justifyContent = 'center';
    robotWrap.style.height = '128px';

    const robot = document.createElement('img');
    robot.src = '/static/img/bt38-guide-complete.svg';
    robot.alt = '';
    robot.style.width = '104px';
    robot.style.height = '104px';
    robot.style.objectFit = 'contain';
    robotWrap.appendChild(robot);

    const rocket = document.createElement('span');
    rocket.setAttribute('aria-hidden', 'true');
    rocket.textContent = '🚀';
    rocket.style.position = 'absolute';
    rocket.style.right = '-2px';
    rocket.style.top = '2px';
    rocket.style.fontSize = '34px';
    rocket.style.transform = 'rotate(-8deg)';
    rocket.style.filter = 'drop-shadow(0 4px 6px rgba(37, 99, 235, .18))';
    robotWrap.appendChild(rocket);

    const content = document.createElement('div');
    content.style.minWidth = '0';

    const check = document.createElement('div');
    check.setAttribute('aria-hidden', 'true');
    check.textContent = '✓';
    check.style.width = '38px';
    check.style.height = '38px';
    check.style.display = 'inline-flex';
    check.style.alignItems = 'center';
    check.style.justifyContent = 'center';
    check.style.marginBottom = '7px';
    check.style.borderRadius = '50%';
    check.style.background = '#22c55e';
    check.style.color = '#fff';
    check.style.fontSize = '24px';
    check.style.fontWeight = '800';
    check.style.boxShadow = '0 8px 18px rgba(34, 197, 94, .24)';

    const title = document.createElement('div');
    title.id = 'bt38PushSuccessTitle';
    title.textContent = 'Pushed!';
    title.style.margin = '0 0 5px';
    title.style.color = '#0f172a';
    title.style.fontSize = '27px';
    title.style.fontWeight = '800';
    title.style.letterSpacing = '-0.02em';

    const message = document.createElement('div');
    message.textContent = 'Your updates have been sent successfully.';
    message.style.marginBottom = '18px';
    message.style.color = '#475569';
    message.style.fontSize = '15px';
    message.style.lineHeight = '1.45';

    const ok = document.createElement('button');
    ok.type = 'button';
    ok.textContent = 'OK';
    ok.style.minWidth = '92px';
    ok.style.padding = '9px 20px';
    ok.style.border = '0';
    ok.style.borderRadius = '11px';
    ok.style.background = '#2563eb';
    ok.style.color = '#fff';
    ok.style.fontSize = '15px';
    ok.style.fontWeight = '700';
    ok.style.cursor = 'pointer';
    ok.style.boxShadow = '0 8px 18px rgba(37, 99, 235, .22)';

    function closeModal() {
      overlay.remove();
    }

    ok.addEventListener('click', closeModal);
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) closeModal();
    });
    document.addEventListener('keydown', function onKeydown(event) {
      if (event.key !== 'Escape') return;
      document.removeEventListener('keydown', onKeydown);
      closeModal();
    }, { once: true });

    content.appendChild(check);
    content.appendChild(title);
    content.appendChild(message);
    content.appendChild(ok);
    card.appendChild(robotWrap);
    card.appendChild(content);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    window.setTimeout(() => ok.focus(), 0);
  }

  function postJson(endpoint, body, actor, options) {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    const requestOptions = options || {};

    return fetch(endpoint, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRF-Token': csrf,
        'X-Actor': actor || 'warehouse-governed'
      },
      body: JSON.stringify(body || {}),
      signal: requestOptions.signal
    }).then(async res => {
      const data = await res.json().catch(() => ({}));

      if (!res.ok || data.success === false) {
        throw new Error(data.message || data.error || 'Action failed');
      }

      return data;
    });
  }

  function getJson(endpoint) {
    return fetch(endpoint, {
      method: 'GET',
      credentials: 'include',
      headers: { 'Accept': 'application/json' },
      cache: 'no-store'
    }).then(async res => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        throw new Error(data.message || data.error || 'Load failed');
      }
      return data;
    });
  }

  function getRow(row) {
    return {
      listingId: row?.dataset.listingId || '',
      stockId: row?.dataset.stockId || '',
      groupId: row?.dataset.groupId || '',
      sku: row?.dataset.sku || ''
    };
  }

  function pushListing(row) {
    const { listingId } = getRow(row);
    if (!listingId) return Promise.reject("Missing listingId");
    return postJson(`/governed/actions/listings/${listingId}/push`, {}, "push");
  }

  function saveQuantity(row, quantity) {
    const { listingId } = getRow(row);
    if (!listingId) return Promise.reject('Missing listingId');
    return postJson(`/governed/actions/listings/${listingId}/quantity`, { quantity }, 'qty');
  }

  function savePrice(row, price) {
    const { listingId } = getRow(row);
    if (!listingId) return Promise.reject('Missing listingId');
    return postJson(`/governed/actions/listings/${listingId}/price`, { price }, 'price');
  }

  function convertToFbm(row) {
    const { stockId } = getRow(row);
    if (!stockId) return Promise.reject('Missing stockId');

    return postJson(`/governed/warehouse/stock-transfer/convert-to-fbm`, {
      warehouse_stock_id: stockId
    }, 'transfer');
  }

  async function chooseAction(value) {
    const selected = selectedRows();

    if (!selected.length) {
      alert('Select at least one SKU');
      return;
    }

    try {
      if (value === 'push') {
        await Promise.all(selected.map(cb => pushListing(cb.closest('tr'))));
        showPushSuccess();
      }

      if (value === 'transfer') {
        await Promise.all(selected.map(cb => convertToFbm(cb.closest('tr'))));
        alert('Transfer complete');
      }

      if (value === 'archive') {
        await Promise.all(selected.map(cb => {
          const { stockId } = getRow(cb.closest('tr'));
          return postJson(`/governed/warehouse/${stockId}/archive`, {}, 'archive');
        }));
        alert('Archive complete');
      }

      clearSelection();
      updateActionBar();
    } catch (e) {
      alert(e.message || 'Action failed');
    }
  }

  document.addEventListener('click', async function (e) {
    const marketBadge = e.target && e.target.closest ? e.target.closest('.bt38-marketplace-control') : null;
    if (!marketBadge) return;

    e.preventDefault();
    e.stopPropagation();

    const row = marketBadge.closest('tr');
    const listingId = row && row.dataset ? row.dataset.listingId : '';

    if (!listingId) {
      alert('Missing listingId');
      return;
    }

    try {
      await postJson(`/governed/actions/listings/${listingId}/push`, {}, 'warehouse-market-badge');
      showPushSuccess();
    } catch (err) {
      alert(err.message || 'Govern action failed');
      console.error('Warehouse market badge push failed', err);
    }
  });

  document.addEventListener('click', async function (e) {
    const qtyButton = e.target && e.target.closest ? e.target.closest('.bt38-qty-action') : null;
    if (!qtyButton) return;

    e.preventDefault();
    e.stopPropagation();

    const row = qtyButton.closest('tr');
    if (!row) return;

    const current = (qtyButton.querySelector('span') || {}).textContent || '';
    const value = window.prompt('Enter new quantity', current.trim());
    if (value === null) return;

    const quantity = parseInt(value, 10);
    if (!Number.isFinite(quantity)) {
      alert('Invalid quantity');
      return;
    }

    try {
      await saveQuantity(row, quantity);
      const span = qtyButton.querySelector('span');
      if (span) span.textContent = String(quantity);
      console.log('[warehouse-qty-button] quantity updated');
    } catch (err) {
      alert(err.message || 'Quantity update failed');
      console.error(err);
    }
  });

  function money(value) {
    const number = Number(value || 0);
    return `£${number.toFixed(2)}`;
  }

  function numberValue(id) {
    const el = document.getElementById(id);
    const value = Number(el && el.value ? el.value : 0);
    return Number.isFinite(value) ? value : 0;
  }

  function recalcWhatIf() {
    const sale = numberValue('bt38CalcSale');
    const cogs = numberValue('bt38CalcCogs');
    const shipping = numberValue('bt38CalcShipping');
    const fees = numberValue('bt38CalcFees');
    const profit = sale - cogs - shipping - fees;
    const margin = sale > 0 ? (profit / sale) * 100 : 0;

    const profitEl = document.getElementById('bt38CalcProfit');
    const marginEl = document.getElementById('bt38CalcMargin');
    if (profitEl) profitEl.textContent = money(profit);
    if (marginEl) marginEl.textContent = `${margin.toFixed(1)}%`;
  }

  function ensureAutoDefaultsEditor(autoSection) {
    let editor = document.getElementById('bt38AutoDefaultsEditor');
    if (editor) return editor;

    editor = document.createElement('div');
    editor.id = 'bt38AutoDefaultsEditor';
    editor.style.marginTop = '10px';
    editor.style.paddingTop = '10px';
    editor.style.borderTop = '1px solid #e5e7eb';
    editor.innerHTML = `
      <div style="font-size:10px;font-weight:800;color:#64748b;margin-bottom:7px;letter-spacing:.04em">WAREHOUSE DEFAULTS</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px">
        <label class="bt38-profit-input"><span>COGS £</span><input id="bt38AutoCogsInput" type="number" min="0" step="0.01"></label>
        <label class="bt38-profit-input"><span>Weight kg</span><input id="bt38AutoWeightInput" type="number" min="0" step="0.001"></label>
        <label class="bt38-profit-input"><span>Ship £ / kg</span><input id="bt38AutoShipRateInput" type="number" min="0" step="0.01"></label>
        <label class="bt38-profit-input"><span>Fee rate %</span><input id="bt38AutoFeeRateInput" type="number" min="0" step="0.01"></label>
      </div>
      <button type="button" id="bt38AutoDefaultsSave" style="margin-top:8px;width:100%;height:32px;border:0;border-radius:8px;background:#111827;color:#fff;font-weight:700;cursor:pointer">Save warehouse defaults</button>
      <small style="display:block;margin-top:5px;color:#64748b">Local warehouse values only. This does not change Amazon or eBay.</small>
    `;
    autoSection.appendChild(editor);
    return editor;
  }

  function populateAutoEconomics(data, row) {
    const overlay = document.getElementById('bt38ProfitOverlay');
    if (!overlay) return;

    const autoSection = overlay.querySelector('.bt38-profit-section');
    if (!autoSection) return;

    const autoStrong = autoSection.querySelectorAll('.bt38-profit-line strong');
    if (autoStrong[0]) autoStrong[0].textContent = money(data.sale_price);
    if (autoStrong[1]) { autoStrong[1].textContent = money(data.unit_cost); autoStrong[1].classList.remove('muted'); }
    if (autoStrong[2]) { autoStrong[2].textContent = money(data.shipping_cost); autoStrong[2].classList.remove('muted'); }
    if (autoStrong[3]) {
      autoStrong[3].textContent = `${money(data.estimated_marketplace_fee)} est.`;
      autoStrong[3].classList.remove('muted');
      autoStrong[3].title = `Estimated from stored ${Number(data.commission_rate || 0).toFixed(2)}% rate until marketplace fee extraction is connected.`;
    }

    const result = autoSection.querySelector('.bt38-profit-result');
    if (result) {
      const resultValue = result.querySelector('strong');
      if (data.estimated_profit === null || data.estimated_profit === undefined) {
        result.classList.add('pending');
        if (resultValue) resultValue.textContent = 'Set COGS';
      } else {
        result.classList.remove('pending');
        if (resultValue) resultValue.textContent = `${money(data.estimated_profit)} · ${Number(data.estimated_margin || 0).toFixed(1)}%`;
      }
    }

    ensureAutoDefaultsEditor(autoSection);
    document.getElementById('bt38AutoCogsInput').value = Number(data.unit_cost || 0).toFixed(2);
    document.getElementById('bt38AutoWeightInput').value = Number(data.product_weight_kg || 0).toFixed(3);
    document.getElementById('bt38AutoShipRateInput').value = Number(data.shipping_cost_per_kg || 0).toFixed(2);
    document.getElementById('bt38AutoFeeRateInput').value = Number(data.commission_rate || 0).toFixed(2);

    const saleInput = document.getElementById('bt38CalcSale');
    const cogsInput = document.getElementById('bt38CalcCogs');
    const shipInput = document.getElementById('bt38CalcShipping');
    const feeInput = document.getElementById('bt38CalcFees');
    if (saleInput) saleInput.value = Number(data.sale_price || 0).toFixed(2);
    if (cogsInput) cogsInput.value = Number(data.unit_cost || 0).toFixed(2);
    if (shipInput) shipInput.value = Number(data.shipping_cost || 0).toFixed(2);
    if (feeInput) feeInput.value = Number(data.estimated_marketplace_fee || 0).toFixed(2);
    recalcWhatIf();

    const profitButton = row ? row.querySelector('.bt38-profit-action') : null;
    if (profitButton) {
      const valueEl = profitButton.querySelector('.bt38-profit-value');
      const small = profitButton.querySelector('small');
      if (data.estimated_profit === null || data.estimated_profit === undefined) {
        if (valueEl) valueEl.textContent = '—';
        if (small) small.textContent = 'Set cost';
      } else {
        if (valueEl) valueEl.textContent = money(data.estimated_profit);
        if (small) small.textContent = `${Number(data.estimated_margin || 0).toFixed(1)}% est.`;
      }
    }
  }

  async function loadEconomics(row) {
    const { stockId, listingId, sku } = getRow(row);
    if (!stockId || stockId === '0') throw new Error('Warehouse stock identity is missing');
    const query = listingId ? `?listing_id=${encodeURIComponent(listingId)}` : '';
    const data = await getJson(`/governed/warehouse/${stockId}/economics${query}`);
    const skuEl = document.getElementById('bt38ProfitSku');
    if (skuEl) skuEl.textContent = sku || data.sku || 'SKU';
    populateAutoEconomics(data, row);
    return data;
  }

  document.addEventListener('click', async function (e) {
    const button = e.target && e.target.closest ? e.target.closest('.bt38-profit-action') : null;
    if (!button) return;

    e.preventDefault();
    e.stopPropagation();

    const row = button.closest('tr');
    const overlay = document.getElementById('bt38ProfitOverlay');
    if (!row || !overlay) return;

    overlay.hidden = false;
    overlay.dataset.stockId = row.dataset.stockId || '';
    overlay.dataset.listingId = row.dataset.listingId || '';

    try {
      await loadEconomics(row);
    } catch (err) {
      overlay.hidden = true;
      alert(err.message || 'Profitability could not be loaded');
    }
  });

  document.addEventListener('click', async function (e) {
    const save = e.target && e.target.closest ? e.target.closest('#bt38AutoDefaultsSave') : null;
    if (!save) return;

    const overlay = document.getElementById('bt38ProfitOverlay');
    const stockId = overlay && overlay.dataset ? overlay.dataset.stockId : '';
    if (!stockId) return;

    save.disabled = true;
    const oldText = save.textContent;
    save.textContent = 'Saving…';

    try {
      await postJson(`/governed/warehouse/${stockId}/economics`, {
        unit_cost: numberValue('bt38AutoCogsInput'),
        product_weight_kg: numberValue('bt38AutoWeightInput'),
        shipping_cost_per_kg: numberValue('bt38AutoShipRateInput'),
        commission_rate: numberValue('bt38AutoFeeRateInput')
      }, 'warehouse-economics');

      const row = document.querySelector(`.bt38-stock-table tr[data-stock-id="${CSS.escape(String(stockId))}"]`);
      if (row) await loadEconomics(row);
      save.textContent = 'Saved';
      window.setTimeout(() => { save.textContent = oldText; }, 900);
    } catch (err) {
      save.textContent = oldText;
      alert(err.message || 'Warehouse costing defaults could not be saved');
    } finally {
      save.disabled = false;
    }
  });

  document.addEventListener('input', function (e) {
    if (!e.target || !['bt38CalcSale', 'bt38CalcCogs', 'bt38CalcShipping', 'bt38CalcFees'].includes(e.target.id)) return;
    recalcWhatIf();
  });

  document.addEventListener('DOMContentLoaded', function () {
    const overlay = document.getElementById('bt38ProfitOverlay');
    const close = document.getElementById('bt38ProfitClose');
    if (close && overlay) close.addEventListener('click', () => { overlay.hidden = true; });
    if (overlay) overlay.addEventListener('click', function (e) {
      if (e.target === overlay) overlay.hidden = true;
    });
  });

  document.addEventListener('DOMContentLoaded', function () {
    if (!warehouseActive()) return;
    document.querySelectorAll('.bt38-row-select').forEach(cb => {
      cb.addEventListener('change', updateActionBar);
    });

    const select = document.getElementById('bt38ActionSelect');
    if (select) {
      select.onchange = function () {
        chooseAction(this.value);
      };
    }

    updateActionBar();
  });

  window.bt38ChooseAction = chooseAction;
  window.bt38UpdateActionBar = updateActionBar;

  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('governedWarehouseSyncBtn');
    if (!btn) return;

    const wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    wrapper.style.display = 'inline-block';

    btn.parentNode.insertBefore(wrapper, btn);
    wrapper.appendChild(btn);
    btn.textContent = 'Sync ▾';

    const menu = document.createElement('div');
    menu.id = 'governedWarehouseSyncMenu';
    menu.hidden = true;
    menu.style.position = 'absolute';
    menu.style.right = '0';
    menu.style.top = 'calc(100% + 6px)';
    menu.style.minWidth = '180px';
    menu.style.padding = '6px';
    menu.style.background = '#fff';
    menu.style.border = '1px solid rgba(0,0,0,.14)';
    menu.style.borderRadius = '8px';
    menu.style.boxShadow = '0 8px 24px rgba(0,0,0,.14)';
    menu.style.zIndex = '1000';

    function addChoice(label, mode, help) {
      const choice = document.createElement('button');
      choice.type = 'button';
      choice.dataset.syncMode = mode;
      choice.style.display = 'block';
      choice.style.width = '100%';
      choice.style.textAlign = 'left';
      choice.style.padding = '9px 10px';
      choice.style.border = '0';
      choice.style.borderRadius = '6px';
      choice.style.background = 'transparent';
      choice.style.cursor = 'pointer';
      choice.innerHTML = `<strong>${label}</strong><br><small>${help}</small>`;
      choice.addEventListener('mouseenter', () => { choice.style.background = 'rgba(0,0,0,.05)'; });
      choice.addEventListener('mouseleave', () => { choice.style.background = 'transparent'; });
      menu.appendChild(choice);
    }

    addChoice('Sync Orders', 'orders', 'Recover recent or missing orders');
    addChoice('Sync Listings', 'listings', 'Recover only missing eBay listings');
    wrapper.appendChild(menu);

    async function runWarehouseSync(mode) {
      if (btn.disabled) return;

      const originalText = btn.textContent;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 15000);
      const actor = mode === 'listings' ? 'warehouse-sync-listings' : 'warehouse-sync-orders';

      btn.disabled = true;
      btn.textContent = mode === 'listings' ? 'Syncing listings...' : 'Syncing orders...';
      menu.hidden = true;

      try {
        const result = await postJson(
          '/governed/warehouse/sync',
          {
            shortcut_source: actor
          },
          actor,
          {signal: controller.signal}
        );

        alert(result.message || (mode === 'listings' ? 'Listing recovery complete' : 'Order recovery complete'));
      } catch (err) {
        if (err && err.name === 'AbortError') {
          alert('Warehouse sync is taking longer than expected. The page has been released; do not press Sync again immediately.');
        } else {
          alert(err.message || 'Warehouse sync failed');
          console.error(err);
        }
      } finally {
        window.clearTimeout(timeout);
        btn.disabled = false;
        btn.textContent = originalText;
      }
    }

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (btn.disabled) return;
      menu.hidden = !menu.hidden;
    });

    menu.addEventListener('click', function (e) {
      const choice = e.target && e.target.closest ? e.target.closest('[data-sync-mode]') : null;
      if (!choice) return;
      e.preventDefault();
      e.stopPropagation();
      runWarehouseSync(choice.dataset.syncMode);
    });

    document.addEventListener('click', function (e) {
      if (!wrapper.contains(e.target)) menu.hidden = true;
    });
  });

  window.bt38ClearSelection = clearSelection;

})();
