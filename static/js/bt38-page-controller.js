// BT38 browser page controller for server-rendered operational tables.
// Product Linking is owned exclusively by product-linking-session.js.

window.BT38 = window.BT38 || {};
window.BT38.pages = window.BT38.pages || {};

(function () {
  "use strict";

  const root = document.querySelector("[data-bt38-page]");

  if (root && root.dataset.bt38Page === "productLinking") {
    const revealServerRenderedProductLinking = function () {
      const loading = document.getElementById("warehouseLoadingState");
      const data = document.getElementById("warehouseDataContainer");
      if (loading) loading.classList.add("d-none");
      if (data) data.classList.remove("d-none");
    };

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", revealServerRenderedProductLinking, {once: true});
    } else {
      revealServerRenderedProductLinking();
    }

    if (typeof window.loadProductLinkingData !== "function") {
      window.loadProductLinkingData = function () {
        revealServerRenderedProductLinking();
        return Promise.resolve({success: true, server_rendered: true, network_request_started: false});
      };
    }

    window.BT38.PageController = {
      owner: "product-linking-session.js",
      skipped: true,
      serverRendered: true
    };
    return;
  }

  const allowedPageSizes = [15, 25, 50, 100];

  function text(value) {
    return String(value == null ? "" : value).trim();
  }

  function lower(value) {
    return text(value).toLowerCase();
  }

  function currentRoot() {
    return document.querySelector("[data-bt38-page]");
  }

  function pageState(name) {
    return window.BT38.pages[name] || null;
  }

  function getFilters(page) {
    const form = page.filterFormSelector ? document.querySelector(page.filterFormSelector) : null;
    if (!form) return {};

    const filters = {};
    form.querySelectorAll("input[name], select[name]").forEach((field) => {
      if (field.type === "hidden") return;
      const value = lower(field.value);
      if (!value || value === "all") return;
      filters[field.name] = value;
    });
    return filters;
  }

  function rowMatches(row, filters) {
    const haystack = `${row.text} ${Object.values(row.dataset).join(" ")}`.toLowerCase();
    return Object.entries(filters).every(([name, value]) => {
      if (name === "q" || name === "search") return haystack.includes(value);
      const camel = name.replace(/_([a-z])/g, (_, character) => character.toUpperCase());
      const scoped = lower(row.dataset[name] || row.dataset[camel]);
      return scoped ? scoped.includes(value) : haystack.includes(value);
    });
  }

  function pageSize(page) {
    const select = document.getElementById("bt38ResultsPerPageSelect");
    const parsed = Number.parseInt(select ? select.value : page.perPage, 10);
    return allowedPageSizes.includes(parsed) ? parsed : 15;
  }

  function updateCount(page, start, end) {
    const total = page.filteredRows.length;
    const count = document.querySelector("[data-bt38-count], .bt38-table-count");
    if (count) {
      count.textContent = `${total} matching · showing ${total ? start + 1 : 0}-${Math.min(end, total)}`;
    }

    const status = document.querySelector(".bt38-page-status");
    if (status) {
      const totalPages = Math.max(1, Math.ceil(total / page.perPage));
      status.textContent = `Page ${page.currentPage} of ${totalPages} · ${total} total`;
    }
  }

  function render(name) {
    const page = pageState(name);
    if (!page || !page.ready) return false;

    page.perPage = pageSize(page);
    const total = page.filteredRows.length;
    const totalPages = Math.max(1, Math.ceil(total / page.perPage));
    page.currentPage = Math.min(Math.max(page.currentPage, 1), totalPages);

    const start = (page.currentPage - 1) * page.perPage;
    const end = start + page.perPage;
    const visibleRows = new Set(page.filteredRows.slice(start, end));

    page.rows.forEach((row) => {
      row.el.hidden = !visibleRows.has(row);
    });

    updateCount(page, start, end);

    const links = document.querySelectorAll(".bt38-page-nav .bt38-page-link");
    const previous = links[0];
    const next = links[links.length - 1];
    if (previous) previous.classList.toggle("disabled", page.currentPage <= 1);
    if (next) next.classList.toggle("disabled", page.currentPage >= totalPages);
    return true;
  }

  function filter(name, keepPage) {
    const page = pageState(name);
    if (!page || !page.ready) return false;

    const filters = getFilters(page);
    page.filteredRows = page.rows.filter((row) => rowMatches(row, filters));
    if (!keepPage) page.currentPage = 1;
    return render(name);
  }

  function cacheRows(name) {
    const page = pageState(name);
    if (!page) return false;

    const table = page.tableSelector ? document.querySelector(page.tableSelector) : null;
    if (!table) return false;

    page.rows = Array.from(table.querySelectorAll(page.rowSelector)).map((element) => ({
      el: element,
      text: lower(element.textContent),
      dataset: Object.assign({}, element.dataset)
    }));
    page.filteredRows = page.rows.slice();
    page.ready = true;
    return true;
  }

  function wireForm(page) {
    const form = page.filterFormSelector ? document.querySelector(page.filterFormSelector) : null;
    if (!form) return;

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      event.stopPropagation();
      filter(page.name, false);
    });

    form.querySelectorAll("input, select").forEach((field) => {
      const apply = (event) => {
        event.preventDefault();
        event.stopPropagation();
        filter(page.name, false);
      };
      field.addEventListener("input", apply);
      field.addEventListener("change", apply);
    });
  }

  function wirePagination(page) {
    const select = document.getElementById("bt38ResultsPerPageSelect");
    if (select) {
      select.addEventListener("change", () => {
        page.currentPage = 1;
        render(page.name);
      });
    }

    const links = document.querySelectorAll(".bt38-page-nav .bt38-page-link");
    if (links[0]) {
      links[0].addEventListener("click", (event) => {
        event.preventDefault();
        if (page.currentPage > 1) {
          page.currentPage -= 1;
          render(page.name);
        }
      });
    }

    if (links.length > 1) {
      links[links.length - 1].addEventListener("click", (event) => {
        event.preventDefault();
        const totalPages = Math.max(1, Math.ceil(page.filteredRows.length / page.perPage));
        if (page.currentPage < totalPages) {
          page.currentPage += 1;
          render(page.name);
        }
      });
    }
  }

  function warehouseKpiCard(label) {
    return Array.from(document.querySelectorAll(".bt38-kpi-card")).find(
      (card) => lower(card.querySelector("span")?.textContent) === lower(label)
    );
  }

  function setWarehouseKpi(label, value, note) {
    const card = warehouseKpiCard(label);
    if (!card) return;
    const strong = card.querySelector("strong");
    const small = card.querySelector("small");
    if (strong) strong.textContent = value;
    if (small && note) small.textContent = note;
  }

  function quantityForWarehouseRow(row) {
    const quantityText = row.querySelector(".bt38-qty-action span, .bt38-qty-locked span")?.textContent || "0";
    return Number.parseInt(quantityText.replace(/[^0-9-]/g, ""), 10) || 0;
  }

  function updateWarehouseListingKpi(rows) {
    const listingIds = new Set();
    rows.forEach((row) => {
      const listingId = text(row.dataset.listingId);
      if (listingId) listingIds.add(listingId);
    });
    setWarehouseKpi("Listings", String(listingIds.size), "Active linked listings");
  }

  async function updateWarehouseInventoryValueKpi(rows) {
    const stockRows = new Map();
    rows.forEach((row) => {
      const stockId = text(row.dataset.stockId);
      if (!stockId || stockRows.has(stockId)) return;
      stockRows.set(stockId, row);
    });

    const stockIds = Array.from(stockRows.keys());
    if (!stockIds.length) {
      setWarehouseKpi("Inventory Value", "£0", "No warehouse stock loaded");
      return;
    }

    try {
      const response = await fetch(
        `/governed/warehouse/economics-batch?stock_ids=${encodeURIComponent(stockIds.join(","))}`,
        {credentials: "include", headers: {Accept: "application/json"}, cache: "no-store"}
      );
      const data = await response.json();
      if (!response.ok || data.success === false) throw new Error(data.error || data.message || "Cost data unavailable");

      const byStockId = new Map((data.economics || []).map((item) => [String(item.warehouse_stock_id), item]));
      let total = 0;
      let costed = 0;
      let missing = 0;

      stockRows.forEach((row, stockId) => {
        const economics = byStockId.get(String(stockId));
        const unitCost = Number(economics && economics.unit_cost ? economics.unit_cost : 0);
        if (!(unitCost > 0)) {
          missing += 1;
          return;
        }
        total += quantityForWarehouseRow(row) * unitCost;
        costed += 1;
      });

      const formatted = new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "GBP",
        maximumFractionDigits: 0
      }).format(total);
      const note = missing
        ? `${costed} SKUs costed · ${missing} missing COGS`
        : `${costed} SKUs costed · stock × COGS`;
      setWarehouseKpi("Inventory Value", formatted, note);
    } catch (error) {
      setWarehouseKpi("Inventory Value", "—", "COGS data unavailable");
      console.warn("[warehouse-kpi] inventory value unavailable", error);
    }
  }

  async function updateWarehouseKpis() {
    try {
      const response = await fetch("/governed/warehouse/kpis", {
        credentials: "include",
        headers: {Accept: "application/json"},
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok || data.success === false) throw new Error(data.error || data.message || "Warehouse KPI data unavailable");

      setWarehouseKpi("Total SKUs", String(Number(data.total_skus || 0)), "Warehouse master SKUs");
      setWarehouseKpi("Available Units", String(Number(data.total_available || 0)), "Warehouse Available");
      setWarehouseKpi("Low Stock", String(Number(data.low_stock_count || 0)), "Needs Attention");
      setWarehouseKpi("Listings", String(Number(data.listing_count || 0)), "Active linked listings");

      const formatted = new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "GBP",
        maximumFractionDigits: 0
      }).format(Number(data.inventory_value || 0));
      const missing = Number(data.missing_cogs_skus || 0);
      const costed = Number(data.costed_skus || 0);
      setWarehouseKpi(
        "Inventory Value",
        formatted,
        missing ? `${costed} SKUs costed · ${missing} missing COGS` : `${costed} SKUs costed · stock × COGS`
      );
    } catch (error) {
      console.warn("[warehouse-kpi] aggregate read unavailable", error);
    }
  }

  async function updateWarehouseStatusKpi() {
    setWarehouseKpi("Warehouse Status", "Checking", "Runtime heartbeat");
    try {
      const response = await fetch("/governed/warehouse/runtime-state", {
        credentials: "include",
        headers: {Accept: "application/json"},
        cache: "no-store"
      });
      const data = await response.json();
      const live = response.ok && data && data.ok === true;
      setWarehouseKpi(
        "Warehouse Status",
        live ? "Live" : "Attention",
        live ? "Runtime healthy" : "Runtime check required"
      );
    } catch (error) {
      setWarehouseKpi("Warehouse Status", "Attention", "Runtime unavailable");
    }
  }

  function normalizeWarehouseUi() {
    if (!document.querySelector('[data-bt38-page="warehouse"]')) return;

    const routes = {
      "master stock": "/warehouse",
      "fba read only": "/fba-inventory",
      "group view": "/product-linking",
      "listings": "/warehouse?view=listings",
      "orders": "/orders",
      "stock transfer": "/warehouse?view=stock-transfer"
    };

    document.querySelectorAll(".bt38-operational-tabs button").forEach((button) => {
      const route = routes[lower(button.textContent)];
      if (!route) return;
      button.type = "button";
      button.addEventListener("click", () => {
        window.location.href = route;
      });
    });

    // KPI totals come from one compact aggregate endpoint. Do not re-read
    // economics for every rendered Warehouse row during page refresh.
    updateWarehouseKpis();
    updateWarehouseStatusKpi();
  }

  const controller = {
    register(name, config) {
      window.BT38.pages[name] = {
        name,
        filterFormSelector: config.filterFormSelector || null,
        tableSelector: config.tableSelector || null,
        rowSelector: config.rowSelector || "tbody tr",
        currentPage: 1,
        perPage: 15,
        rows: [],
        filteredRows: [],
        ready: false
      };
      return window.BT38.pages[name];
    },

    initTableCache: cacheRows,

    refreshTableCache(name) {
      const cached = cacheRows(name);
      if (cached) filter(name, true);
      return cached;
    },

    localFilter: filter,
    renderPage: render,

    autoRegisterFromDom() {
      const pageRoot = currentRoot();
      if (!pageRoot || !pageRoot.dataset.bt38Page) return false;

      const page = controller.register(pageRoot.dataset.bt38Page, {
        filterFormSelector: pageRoot.dataset.bt38FilterForm,
        tableSelector: pageRoot.dataset.bt38Table,
        rowSelector: pageRoot.dataset.bt38Row || "tbody tr"
      });

      if (pageRoot.dataset.bt38SubmitName) {
        window[pageRoot.dataset.bt38SubmitName] = function (event) {
          if (event) {
            event.preventDefault();
            event.stopPropagation();
          }
          return filter(page.name, false);
        };
      }

      wireForm(page);
      wirePagination(page);
      if (cacheRows(page.name)) filter(page.name, false);
      return true;
    }
  };

  window.BT38.PageController = controller;
  window.bt38SetFilter = function () {
    const pageRoot = currentRoot();
    return pageRoot ? filter(pageRoot.dataset.bt38Page, false) : false;
  };

  document.addEventListener("DOMContentLoaded", () => {
    controller.autoRegisterFromDom();
    normalizeWarehouseUi();
  });
}());
