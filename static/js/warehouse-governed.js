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
        throw new Error(data.message || 'Action failed');
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
