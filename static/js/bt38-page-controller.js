// BT38 production-aligned browser page controller.
// Initial HTML may come from the server. Search, filters and modal lookup stay in memory.

window.BT38 = window.BT38 || {};
window.BT38.pages = window.BT38.pages || {};

(function () {
  "use strict";

  const allowedPageSizes = [15, 25, 50, 100];

  function text(value) {
    return String(value == null ? "" : value).trim();
  }

  function lower(value) {
    return text(value).toLowerCase();
  }

  function pageState(name) {
    return window.BT38.pages[name] || null;
  }

  function currentRoot() {
    return document.querySelector("[data-bt38-page]");
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
      const camel = name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
      const scoped = lower(row.dataset[name] || row.dataset[camel]);
      return scoped ? scoped.includes(value) : haystack.includes(value);
    });
  }

  function pageSize(page) {
    if (page.name === "productLinking") return 25;
    const select = document.getElementById("bt38ResultsPerPageSelect");
    const parsed = Number.parseInt(select ? select.value : page.perPage, 10);
    return allowedPageSizes.includes(parsed) ? parsed : 15;
  }

  function updateCount(page, start, end) {
    const total = page.filteredRows.length;
    const count = document.querySelector("[data-bt38-count], .bt38-table-count");
    if (count) count.textContent = `${total} matching · showing ${total ? start + 1 : 0}-${Math.min(end, total)}`;

    const status = document.querySelector(".bt38-page-status");
    if (status) {
      const totalPages = Math.max(1, Math.ceil(total / page.perPage));
      status.textContent = `Page ${page.currentPage} of ${totalPages} · ${total} total`;
    }
  }

  function renderProductLinkingPagination(page, totalPages, start, end) {
    const nav = document.querySelector('[aria-label="Product linking pagination"]');
    if (!nav) return;

    const previous = Math.max(1, page.currentPage - 1);
    const next = Math.min(totalPages, page.currentPage + 1);
    nav.innerHTML = `
      <button type="button" class="btn btn-outline-secondary" data-bt38-local-page="1" ${page.currentPage <= 1 ? "disabled" : ""}>First</button>
      <button type="button" class="btn btn-outline-secondary" data-bt38-local-page="${previous}" ${page.currentPage <= 1 ? "disabled" : ""}>← Prev</button>
      <button type="button" class="btn btn-primary" disabled>Page ${page.currentPage} of ${totalPages}</button>
      <button type="button" class="btn btn-outline-secondary" data-bt38-local-page="${next}" ${page.currentPage >= totalPages ? "disabled" : ""}>Next →</button>
      <button type="button" class="btn btn-outline-secondary" data-bt38-local-page="${totalPages}" ${page.currentPage >= totalPages ? "disabled" : ""}>Last</button>`;

    nav.querySelectorAll("[data-bt38-local-page]").forEach((button) => {
      button.addEventListener("click", () => {
        page.currentPage = Number.parseInt(button.dataset.bt38LocalPage || "1", 10);
        render(page.name);
      });
    });

    const summary = nav.closest(".d-flex")?.querySelector("small");
    if (summary) summary.textContent = `Showing ${page.filteredRows.length ? start + 1 : 0} to ${Math.min(end, page.filteredRows.length)} of ${page.filteredRows.length} warehouse products`;
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
    const visible = new Set(page.filteredRows.slice(start, end));

    page.rows.forEach((row) => { row.el.hidden = !visible.has(row); });
    updateCount(page, start, end);

    if (name === "productLinking") {
      renderProductLinkingPagination(page, totalPages, start, end);
    } else {
      const links = document.querySelectorAll(".bt38-page-nav .bt38-page-link");
      const previous = links[0];
      const next = links[links.length - 1];
      if (previous) previous.classList.toggle("disabled", page.currentPage <= 1);
      if (next) next.classList.toggle("disabled", page.currentPage >= totalPages);
    }
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
    page.rows = Array.from(table.querySelectorAll(page.rowSelector)).map((el) => ({
      el,
      text: lower(el.textContent),
      dataset: Object.assign({}, el.dataset)
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
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        filter(page.name, false);
      };
      field.addEventListener("input", apply);
      field.addEventListener("change", apply);
    });

    const clear = form.querySelector('a[href="/product-linking"]');
    if (clear) {
      clear.addEventListener("click", (event) => {
        event.preventDefault();
        form.querySelectorAll("input[name], select[name]").forEach((field) => {
          if (field.tagName === "SELECT") field.value = field.querySelector('option[value="all"]') ? "all" : "";
          else field.value = "";
        });
        filter(page.name, false);
      });
    }
  }

  function wirePagination(page) {
    if (page.name === "productLinking") return;
    const select = document.getElementById("bt38ResultsPerPageSelect");
    if (select) select.addEventListener("change", () => { page.currentPage = 1; render(page.name); });

    const links = document.querySelectorAll(".bt38-page-nav .bt38-page-link");
    if (links[0]) links[0].addEventListener("click", (event) => {
      event.preventDefault();
      if (page.currentPage > 1) { page.currentPage -= 1; render(page.name); }
    });
    if (links.length > 1) links[links.length - 1].addEventListener("click", (event) => {
      event.preventDefault();
      const totalPages = Math.max(1, Math.ceil(page.filteredRows.length / page.perPage));
      if (page.currentPage < totalPages) { page.currentPage += 1; render(page.name); }
    });
  }

  function wireAsyncProductLinking(page) {
    const container = document.getElementById("warehouseDataContainer");
    if (!container) return;

    const refresh = () => {
      const table = container.querySelector("table");
      if (!table || !table.querySelector("tbody tr")) return false;
      cacheRows(page.name);
      filter(page.name, false);
      return true;
    };

    if (refresh()) return;
    const observer = new MutationObserver(() => { if (refresh()) observer.disconnect(); });
    observer.observe(container, { childList: true, subtree: true });
  }

  function installLocalProductLinkingSearch() {
    if (!document.querySelector('[data-bt38-page="productLinking"]')) return;

    window.filterModalWarehouse = function () {
      window.searchWarehouseForLinking();
    };

    window.searchWarehouseForLinking = function () {
      const input = document.getElementById("modalWarehouseSearch");
      const query = lower(input ? input.value : "");
      const source = Array.isArray(window.allWarehouseProducts) ? window.allWarehouseProducts : [];
      const filtered = query ? source.filter((item) => {
        const haystack = lower([
          item.sku,
          item.name,
          item.product_name,
          item.barcode,
          ...(Array.isArray(item.platforms) ? item.platforms : [])
        ].join(" "));
        return haystack.includes(query);
      }) : source;

      if (typeof window.renderWarehouseInModal === "function") {
        window.renderWarehouseInModal(filtered, window.currentListingId, "");
      }
      return false;
    };

    document.querySelectorAll("button, a").forEach((element) => {
      const label = lower(element.textContent || element.title);
      if (["repair", "reset failures", "rebuild", "create missing", "sync now"].some((term) => label.includes(term))) {
        element.hidden = true;
        element.setAttribute("aria-hidden", "true");
      }
    });
  }

  function normalizeWarehouseUi() {
    if (!document.querySelector('[data-bt38-page="warehouse"]')) return;

    const tabs = Array.from(document.querySelectorAll(".bt38-operational-tabs button"));
    const routes = {
      "master stock": "/warehouse",
      "fba read only": "/fba-inventory",
      "group view": "/product-linking",
      "listings": "/warehouse?view=listings",
      "orders": "/orders",
      "stock transfer": "/warehouse?view=stock-transfer"
    };
    tabs.forEach((button) => {
      const route = routes[lower(button.textContent)];
      if (!route) return;
      button.type = "button";
      button.addEventListener("click", () => { window.location.href = route; });
    });

    const cards = Array.from(document.querySelectorAll(".bt38-kpi-card"));
    const rows = Array.from(document.querySelectorAll(".bt38-stock-table tbody tr"));
    const listingCard = cards.find((card) => lower(card.querySelector("span")?.textContent) === "listings");
    if (listingCard) {
      const strong = listingCard.querySelector("strong");
      if (strong) strong.textContent = String(rows.filter((row) => text(row.dataset.listingId)).length);
      const small = listingCard.querySelector("small");
      if (small) small.textContent = "Loaded marketplace listings";
    }

    const valueCard = cards.find((card) => lower(card.querySelector("span")?.textContent) === "inventory value");
    if (valueCard) {
      let total = 0;
      rows.forEach((row) => {
        const priceText = row.querySelector(".bt38-price-action span")?.textContent || "";
        const qtyText = row.querySelector(".bt38-qty-action span, .bt38-qty-locked span")?.textContent || "0";
        const price = Number.parseFloat(priceText.replace(/[^0-9.-]/g, "")) || 0;
        const qty = Number.parseInt(qtyText.replace(/[^0-9-]/g, ""), 10) || 0;
        total += price * qty;
      });
      const strong = valueCard.querySelector("strong");
      if (strong) strong.textContent = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 }).format(total);
      const small = valueCard.querySelector("small");
      if (small) small.textContent = "Loaded stock × price";
    }
  }

  const controller = {
    register(name, config) {
      window.BT38.pages[name] = {
        name,
        filterFormSelector: config.filterFormSelector || null,
        tableSelector: config.tableSelector || null,
        rowSelector: config.rowSelector || "tbody tr",
        currentPage: 1,
        perPage: name === "productLinking" ? 25 : 15,
        rows: [],
        filteredRows: [],
        ready: false
      };
      return window.BT38.pages[name];
    },
    initTableCache: cacheRows,
    refreshTableCache(name) { const ok = cacheRows(name); if (ok) filter(name, true); return ok; },
    localFilter: filter,
    renderPage: render,
    autoRegisterFromDom() {
      const root = currentRoot();
      if (!root || !root.dataset.bt38Page) return false;
      const page = controller.register(root.dataset.bt38Page, {
        filterFormSelector: root.dataset.bt38FilterForm,
        tableSelector: root.dataset.bt38Table,
        rowSelector: root.dataset.bt38Row || "tbody tr"
      });

      if (root.dataset.bt38SubmitName) {
        window[root.dataset.bt38SubmitName] = function (event) {
          if (event) { event.preventDefault(); event.stopPropagation(); }
          return filter(page.name, false);
        };
      }

      wireForm(page);
      wirePagination(page);
      if (!cacheRows(page.name) && page.name === "productLinking") wireAsyncProductLinking(page);
      else filter(page.name, false);
      return true;
    }
  };

  window.BT38.PageController = controller;
  window.bt38SetFilter = function () {
    const root = currentRoot();
    return root ? filter(root.dataset.bt38Page, false) : false;
  };

  document.addEventListener("DOMContentLoaded", () => {
    controller.autoRegisterFromDom();
    installLocalProductLinkingSearch();
    normalizeWarehouseUi();
  });
}());
