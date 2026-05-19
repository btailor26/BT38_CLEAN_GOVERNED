// BT38 warehouse page governed button wiring.
// This file is intentionally narrow: it only activates on the warehouse table.
(function () {
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
    if (selected.length > 0) {
      bar.hidden = false;
    } else {
      bar.hidden = true;
      const select = document.getElementById('bt38ActionSelect');
      if (select) select.value = '';
    }
  }

  function clearSelection() {
    document.querySelectorAll('.bt38-row-select').forEach(function (cb) {
      cb.checked = false;
    });
    updateActionBar();
  }

  function postJson(endpoint, body) {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    return fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRF-Token': csrf
      },
      body: JSON.stringify(body || {})
    }).then(async function (response) {
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok || data.success === false || data.ok === false) {
        throw new Error(data.reason || data.error || data.message || 'Governed action failed.');
      }
      return data;
    });
  }

  function pushGovernedListing(row) {
    if (!row) return Promise.reject(new Error('Missing row for governed push.'));
    const listingId = row.dataset.listingId || '';
    const sku = row.dataset.sku || '';
    if (!listingId || listingId === '0') {
      return Promise.reject(new Error('Missing marketplace listing id for ' + (sku || 'this row') + '.'));
    }
    return postJson('/governed/actions/listings/' + encodeURIComponent(listingId) + '/push', {});
  }

  function chooseAction(value) {
    if (!value) return;
    const selected = selectedRows();
    if (!selected.length) return alert('Select at least one SKU first.');

    if (value !== 'push' && value !== 'sync') {
      alert('Only governed Push is wired on this page right now. Other actions remain unchanged until approved.');
      const select = document.getElementById('bt38ActionSelect');
      if (select) select.value = '';
      return;
    }

    if (!confirm('Run governed push for ' + selected.length + ' selected SKU(s)?')) {
      const select = document.getElementById('bt38ActionSelect');
      if (select) select.value = '';
      return;
    }

    Promise.allSettled(selected.map(function (cb) {
      return pushGovernedListing(cb.closest('tr'));
    })).then(function (results) {
      const passed = results.filter(function (result) { return result.status === 'fulfilled'; }).length;
      const failed = results.length - passed;
      alert('Governed push complete. Success: ' + passed + '. Failed: ' + failed + '.');
      window.location.reload();
    });
  }

  function openRowAction(button) {
    const row = button.closest('tr');
    if (!row) return;

    const itemId = row.dataset.itemId;
    const stockId = row.dataset.stockId;
    const listingId = row.dataset.listingId;
    const sku = row.dataset.sku || '';

    if (button.classList.contains('bt38-marketplace-control')) {
      if (!listingId || listingId === '0') return alert('Missing marketplace listing id for governed push.');
      if (!confirm('Run governed marketplace push for ' + sku + '?')) return;
      pushGovernedListing(row)
        .then(function (data) {
          alert(data.reason || data.message || 'Governed marketplace push completed.');
          window.location.reload();
        })
        .catch(function (err) { alert('Governed marketplace push failed: ' + err.message); });
      return;
    }

    if (button.classList.contains('bt38-qty-action')) {
      if (!itemId) return alert('Missing item id for quantity update.');
      const current = button.querySelector('span')?.innerText?.trim() || '0';
      const next = prompt('New quantity for ' + sku + ':', current);
      if (next === null) return;
      const qty = parseInt(next, 10);
      if (Number.isNaN(qty) || qty < 0) return alert('Enter a valid quantity.');

      postJson('/update_stock/' + encodeURIComponent(itemId), { quantity: qty })
        .then(function (data) {
          if (data.success === false) return alert(data.error || data.message || 'Quantity update failed.');
          const span = button.querySelector('span');
          if (span) span.innerText = qty;
          alert(data.message || 'Quantity saved. Use the marketplace icon to run governed push.');
          window.location.reload();
        })
        .catch(function (err) { alert('Quantity update failed: ' + err.message); });
      return;
    }

    if (button.classList.contains('bt38-price-action')) {
      alert('Price editing will be wired after quantity and governed push actions are stable.');
      return;
    }

    if (button.classList.contains('bt38-warehouse-action')) {
      if (stockId) window.location.href = '/warehouse/' + encodeURIComponent(stockId);
      return;
    }

    if (button.classList.contains('bt38-action-btn')) {
      alert('Use the marketplace icon for governed single push, Qty Save for quantity, or row select for bulk governed push.');
    }
  }

  window.bt38SelectedRows = selectedRows;
  window.bt38UpdateActionBar = updateActionBar;
  window.bt38ClearSelection = clearSelection;
  window.bt38ChooseAction = chooseAction;
  window.bt38OpenRowAction = openRowAction;
  window.bt38PushGovernedListing = pushGovernedListing;

  document.addEventListener('DOMContentLoaded', function () {
    if (!warehouseActive()) return;
    document.querySelectorAll('.bt38-row-select').forEach(function (cb) {
      cb.addEventListener('change', updateActionBar);
    });
  });
})();
