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

  function postJson(endpoint, body, actor) {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    return fetch(endpoint, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRF-Token': csrf,
        'X-Actor': actor || 'warehouse-governed'
      },
      body: JSON.stringify(body || {})
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

  // ==============================
  // GOVERNED ACTIONS (NO RELOAD)
  // ==============================

  function pushListing(row) {
    const { listingId } = getRow(row);

    // Original warehouse rule:
    // marketplace icon push is listing-specific.
    // Group push belongs to explicit group actions only.
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

  // ==============================
  // ACTION HANDLER
  // ==============================

  async function chooseAction(value) {
    const selected = selectedRows();

    if (!selected.length) {
      alert('Select at least one SKU');
      return;
    }

    try {

      if (value === 'push') {
        await Promise.all(selected.map(cb => pushListing(cb.closest('tr'))));
        alert('Push complete');
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
  // ==============================
  // BROWSER ROW CACHE / LOCAL FILTER
  // ==============================

  function initBrowserRowCache() {
    window.BT38 = window.BT38 || { state: { cache: {} } };

    if (typeof window.BT38.initPage === 'function') {
      window.BT38.initPage('warehouse');
    }

    const cache = window.BT38.state.cache.warehouse = window.BT38.state.cache.warehouse || {};
    cache.rows = Array.from(document.querySelectorAll('.bt38-stock-table tbody tr')).map(row => ({
      el: row,
      text: (row.textContent || '').toLowerCase(),
      sku: (row.dataset.sku || '').toLowerCase(),
      platform: (row.dataset.platform || '').toLowerCase(),
      channel: (row.dataset.channel || '').toLowerCase(),
      status: (row.dataset.status || '').toLowerCase(),
      groupId: (row.dataset.groupId || '').toLowerCase(),
      listingId: (row.dataset.listingId || '').toLowerCase(),
      marketplace: (row.dataset.marketplace || '').toLowerCase()
    }));

    cache.ready = true;
  }

  function getWarehouseFilters() {
    const form = document.getElementById('bt38WarehouseSearchForm');
    if (!form) return {};

    return {
      q: ((form.querySelector('[name="q"]') || {}).value || '').trim().toLowerCase(),
      marketplace: ((form.querySelector('[name="marketplace"]') || {}).value || 'all').toLowerCase(),
      status: ((form.querySelector('[name="status"]') || {}).value || 'all').toLowerCase(),
      group: ((form.querySelector('[name="group"]') || {}).value || 'all').toLowerCase(),
      listingStatus: ((form.querySelector('[name="listing_status"]') || {}).value || 'all').toLowerCase()
    };
  }

  function rowMatches(row, filters) {
    if (filters.q && !row.text.includes(filters.q) && !row.sku.includes(filters.q)) return false;

    if (filters.marketplace !== 'all') {
      const hay = `${row.platform} ${row.marketplace}`.toLowerCase();
      if (!hay.includes(filters.marketplace)) return false;
    }

    if (filters.status !== 'all') {
      if (filters.status === 'active' && !row.text.includes('active')) return false;
      if (filters.status === 'inactive' && !row.text.includes('inactive')) return false;
      if (filters.status === 'blocked' && !row.text.includes('blocked')) return false;
    }

    if (filters.group !== 'all') {
      const grouped = !!row.groupId;
      if (filters.group === 'grouped' && !grouped) return false;
      if (filters.group === 'ungrouped' && grouped) return false;
    }

    if (filters.listingStatus !== 'all') {
      const linked = !!row.listingId;
      if (filters.listingStatus === 'linked' && !linked) return false;
      if (filters.listingStatus === 'unlinked' && linked) return false;
    }

    return true;
  }

  function applyLocalWarehouseFilter() {
    const cache = window.BT38?.state?.cache?.warehouse;
    if (!cache || !cache.ready || !Array.isArray(cache.rows)) return false;

    const filters = getWarehouseFilters();
    let visible = 0;

    cache.rows.forEach(row => {
      const match = rowMatches(row, filters);
      row.el.hidden = !match;
      if (match) visible += 1;
    });

    const count = document.querySelector('.bt38-table-count');
    if (count) count.textContent = `${visible} visible in browser session`;

    return true;
  }

  function wireLocalWarehouseSearch() {
    const form = document.getElementById('bt38WarehouseSearchForm');
    if (!form) return;

    form.addEventListener('submit', function (e) {
      if (applyLocalWarehouseFilter()) {
        e.preventDefault();
        e.stopPropagation();
      }
    });

    form.querySelectorAll('select').forEach(select => {
      select.addEventListener('change', function (e) {
        if (applyLocalWarehouseFilter()) {
          e.preventDefault();
          e.stopPropagation();
        }
      });
    });

    const input = form.querySelector('[name="q"]');
    if (input) input.addEventListener('input', applyLocalWarehouseFilter);

    window.bt38WarehouseLocalSubmit = function(event) {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      applyLocalWarehouseFilter();
      return false;
    };
  }


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
      alert('Market badge push complete');
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

    initBrowserRowCache();
    wireLocalWarehouseSearch();
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
  window.bt38ClearSelection = clearSelection;

})();
