// Governed Warehouse inbound visibility on the existing Warehouse page.
// Read-only by design: no inventory mutation is performed here.
(function () {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }

  async function getJson(url) {
    const response = await fetch(url, {
      method: 'GET', credentials: 'include', cache: 'no-store',
      headers: {'Accept': 'application/json'}
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok || data.success === false) {
      throw new Error(data.message || data.error || 'Warehouse inbound request failed');
    }
    return data;
  }

  function install() {
    const page = document.querySelector('.bt38-enterprise-stock');
    const tabs = page && page.querySelector('.bt38-operational-tabs');
    const filter = page && page.querySelector('#bt38WarehouseSearchForm');
    const kpis = page && page.querySelector('.bt38-kpi-row');
    const table = page && page.querySelector('.bt38-table-shell');
    const results = page && page.querySelector('#bt38ResultsPerPageBottom');
    if (!page || !tabs || !table || document.getElementById('bt38GoodsInPanel')) return;

    const masterTab = tabs.querySelector('button');
    const goodsTab = document.createElement('button');
    goodsTab.type = 'button';
    goodsTab.id = 'bt38GoodsInTab';
    goodsTab.textContent = 'Goods In';
    tabs.appendChild(goodsTab);

    const panel = document.createElement('section');
    panel.id = 'bt38GoodsInPanel';
    panel.hidden = true;
    panel.style.cssText = 'background:#fff;border:1px solid #edf1f5;border-radius:10px;padding:14px;margin-bottom:10px';
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:12px">
        <div><h2 style="font-size:18px;margin:0 0 3px">Goods In</h2><small style="color:#64748b">Expected inbound and barcode identity · read only</small></div>
        <span style="font-size:11px;padding:4px 8px;border-radius:999px;background:#eef2ff;color:#2563eb">No stock changes</span>
      </div>
      <div style="display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:8px;margin-bottom:12px">
        <input id="bt38InboundScanInput" type="text" autocomplete="off" placeholder="Scan or enter SKU, EAN, UPC, FNSKU or carton barcode" style="height:40px;border:1px solid #dbe1ea;border-radius:8px;padding:0 11px">
        <button id="bt38InboundScanBtn" type="button" class="btn btn-primary">Resolve</button>
      </div>
      <div id="bt38InboundResolved" hidden style="padding:10px;border-radius:8px;background:#f8fafc;margin-bottom:12px"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px"><strong>Expected inbound</strong><small id="bt38InboundCount" style="color:#64748b">Loading…</small></div>
      <div style="overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="background:#f8fafc"><th style="padding:8px;text-align:left">PO</th><th style="padding:8px;text-align:left">SKU</th><th style="padding:8px;text-align:left">Product</th><th style="padding:8px;text-align:right">Ordered</th><th style="padding:8px;text-align:right">Received</th><th style="padding:8px;text-align:right">Remaining</th><th style="padding:8px;text-align:left">Location</th></tr></thead><tbody id="bt38InboundRows"></tbody></table></div>
      <div id="bt38InboundEmpty" hidden style="padding:18px;text-align:center;color:#64748b">No expected inbound stock found.</div>`;
    table.parentNode.insertBefore(panel, table);

    function showMaster() {
      panel.hidden = true;
      [filter, kpis, table, results].forEach(function (el) { if (el) el.hidden = false; });
      tabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
      if (masterTab) masterTab.classList.add('active');
    }

    async function loadInbound() {
      const body = panel.querySelector('#bt38InboundRows');
      const count = panel.querySelector('#bt38InboundCount');
      const empty = panel.querySelector('#bt38InboundEmpty');
      try {
        const data = await getJson('/governed/warehouse/expected-inbound');
        const rows = data.expected_inbound || [];
        count.textContent = rows.length + ' expected line' + (rows.length === 1 ? '' : 's');
        empty.hidden = rows.length !== 0;
        body.innerHTML = rows.map(function (row) {
          return `<tr style="border-top:1px solid #f1f5f9"><td style="padding:8px">${esc(row.po_number)}</td><td style="padding:8px;font-weight:700">${esc(row.sku)}</td><td style="padding:8px">${esc(row.product_name)}</td><td style="padding:8px;text-align:right">${esc(row.ordered_quantity)}</td><td style="padding:8px;text-align:right">${esc(row.received_quantity)}</td><td style="padding:8px;text-align:right;font-weight:700">${esc(row.remaining_quantity)}</td><td style="padding:8px">${esc(row.location || 'Not assigned')}</td></tr>`;
        }).join('');
      } catch (error) {
        count.textContent = 'Unavailable';
        body.innerHTML = `<tr><td colspan="7" style="padding:14px;color:#b91c1c">${esc(error.message)}</td></tr>`;
      }
    }

    function showGoodsIn() {
      [filter, kpis, table, results].forEach(function (el) { if (el) el.hidden = true; });
      panel.hidden = false;
      tabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
      goodsTab.classList.add('active');
      loadInbound();
    }

    goodsTab.addEventListener('click', showGoodsIn);
    if (masterTab) masterTab.addEventListener('click', showMaster);

    async function resolveScan() {
      const input = panel.querySelector('#bt38InboundScanInput');
      const output = panel.querySelector('#bt38InboundResolved');
      const value = String(input.value || '').trim();
      if (!value) return;
      output.hidden = false;
      output.textContent = 'Resolving…';
      try {
        const data = await getJson('/governed/warehouse/scan/' + encodeURIComponent(value));
        output.innerHTML = `<strong>${esc(data.product_name || data.sku)}</strong><div style="margin-top:4px;color:#475569">SKU: ${esc(data.sku)} · Identity: ${esc(data.identity_type)} · Units/scan: ${esc(data.units_per_scan)} · Available: ${esc(data.available_quantity)} · On order: ${esc(data.on_order_quantity)} · Location: ${esc(data.location || 'Not assigned')}</div>${data.master_product_group_id ? `<div style="margin-top:4px;color:#2563eb">Group authority: ${esc(data.master_product_group_id)} · Warehouse stock: ${esc(data.warehouse_stock_id)}</div>` : ''}<small style="display:block;margin-top:5px;color:#64748b">${esc(data.message || 'No stock has been changed.')}</small>`;
      } catch (error) {
        output.innerHTML = `<span style="color:#b91c1c">${esc(error.message)}</span>`;
      }
    }

    panel.querySelector('#bt38InboundScanBtn').addEventListener('click', resolveScan);
    panel.querySelector('#bt38InboundScanInput').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); resolveScan(); }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();