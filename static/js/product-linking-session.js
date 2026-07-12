// Product Linking browser-session controller.
// Read-only browsing hydrates once, then search/filter/pagination/modal search stay local.
// POST mutations remain governed and may rehydrate after a real change.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const state = {
    hydrated: false,
    hydrating: null,
    products: [],
    unlinked: [],
    listings: [],
    page: 1,
    perPage: 25,
    filtered: []
  };

  function uniqueById(items) {
    const seen = new Map();
    (items || []).forEach(item => {
      const key = item && item.id != null ? String(item.id) : JSON.stringify(item);
      if (!seen.has(key)) seen.set(key, item);
    });
    return Array.from(seen.values());
  }

  function assignLegacyGlobals() {
    try { allWarehouseProducts = state.products; } catch (_) { window.allWarehouseProducts = state.products; }
    try { allUnlinkedListings = state.unlinked; } catch (_) { window.allUnlinkedListings = state.unlinked; }
    try { allMarketplaceListings = state.listings; } catch (_) { window.allMarketplaceListings = state.listings; }
  }

  async function fetchDataset() {
    const params = new URLSearchParams({
      page: "1",
      per_page: "5000",
      limit: "5000",
      search: "",
      platform: "all",
      store: "all",
      show_linked: "all",
      section: "all"
    });
    const response = await fetch(`/governed/product-linking/data?${params.toString()}`);
    if (!response.ok) throw new Error(`Product Linking hydration failed: HTTP ${response.status}`);
    const data = await response.json();
    if (!data.success) throw new Error(data.error || "Product Linking hydration failed");
    return data;
  }

  async function hydrate(force) {
    if (state.hydrated && !force) {
      render();
      return;
    }
    if (state.hydrating) return state.hydrating;

    state.hydrating = (async () => {
      const loading = document.getElementById("warehouseLoadingState");
      const errorBox = document.getElementById("warehouseErrorState");
      const container = document.getElementById("warehouseDataContainer");
      if (loading) loading.classList.remove("d-none");
      if (errorBox) errorBox.classList.add("d-none");
      if (container) container.classList.add("d-none");

      try {
        const data = await fetchDataset();
        state.products = uniqueById(data.warehouse_products || []);
        state.unlinked = uniqueById(data.unlinked_listings || []);
        state.listings = uniqueById(data.all_marketplace_listings || data.listings || []);
        state.hydrated = true;
        state.page = 1;
        assignLegacyGlobals();
        render();

        if (loading) loading.classList.add("d-none");
        if (container) container.classList.remove("d-none");
      } catch (error) {
        console.error("[ProductLinkingSession] hydration failed", error);
        if (loading) loading.classList.add("d-none");
        if (errorBox) errorBox.classList.remove("d-none");
        const message = document.getElementById("warehouseErrorMessage");
        if (message) message.textContent = error.message || "Failed to load product groups.";
      } finally {
        state.hydrating = null;
      }
    })();

    return state.hydrating;
  }

  function getFilters() {
    const form = document.getElementById("bt38ProductLinkingFilterForm");
    if (!form) return { search: "", platform: "", store: "", showLinked: "all" };
    return {
      search: String(form.querySelector('[name="search"]')?.value || "").trim().toLowerCase(),
      platform: String(form.querySelector('[name="platform"]')?.value || "").trim().toLowerCase(),
      store: String(form.querySelector('[name="store"]')?.value || "").trim().toLowerCase(),
      showLinked: String(form.querySelector('[name="show_linked"]')?.value || "all").trim().toLowerCase()
    };
  }

  function productMatches(product, filters) {
    const listings = product.listings || [];
    const haystack = [
      product.sku,
      product.name,
      product.group_name,
      product.barcode,
      product.master_product_group_id,
      ...listings.flatMap(listing => [
        listing.external_sku,
        listing.sku,
        listing.title,
        listing.external_listing_id,
        listing.external_id,
        listing.asin,
        listing.fnsku,
        listing.platform,
        listing.store_name
      ])
    ].filter(Boolean).join(" ").toLowerCase();

    if (filters.search && !haystack.includes(filters.search)) return false;
    if (filters.platform && filters.platform !== "all") {
      const matchesPlatform = listings.some(listing => String(listing.platform || "").toLowerCase().includes(filters.platform));
      if (!matchesPlatform) return false;
    }
    if (filters.store && filters.store !== "all") {
      const matchesStore = listings.some(listing => String(listing.store_id || "").toLowerCase() === filters.store);
      if (!matchesStore) return false;
    }

    const linkedCount = Number.parseInt(product.linked_count || listings.length || 0, 10);
    if (filters.showLinked === "linked" && linkedCount <= 0) return false;
    if (filters.showLinked === "unlinked" && linkedCount > 0) return false;
    return true;
  }

  function render() {
    if (!state.hydrated || typeof renderWarehouseProducts !== "function") return;
    const filters = getFilters();
    state.filtered = state.products.filter(product => productMatches(product, filters));
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.perPage));
    state.page = Math.min(Math.max(state.page, 1), totalPages);
    const start = (state.page - 1) * state.perPage;
    const pageRows = state.filtered.slice(start, start + state.perPage);

    try {
      productLinkingPage = state.page;
      productLinkingPerPage = state.perPage;
      productLinkingPagination = {
        page: state.page,
        per_page: state.perPage,
        total_stock: state.filtered.length,
        total_pages: totalPages,
        has_prev: state.page > 1,
        has_next: state.page < totalPages,
        prev_page: Math.max(1, state.page - 1),
        next_page: Math.min(totalPages, state.page + 1)
      };
    } catch (_) {}

    renderWarehouseProducts(pageRows);
    const count = document.getElementById("warehouseGroupsCount");
    if (count) count.textContent = `${state.filtered.length} matching of ${state.products.length} warehouse groups`;
    if (typeof feather !== "undefined") feather.replace();
  }

  window.bt38ProductLinkingSetPage = function (page) {
    state.page = Number.parseInt(page || 1, 10) || 1;
    render();
    return false;
  };

  window.renderProductLinkingPagination = function () {
    const total = state.filtered.length;
    const totalPages = Math.max(1, Math.ceil(total / state.perPage));
    const start = total === 0 ? 0 : ((state.page - 1) * state.perPage) + 1;
    const end = Math.min(total, state.page * state.perPage);
    return `
      <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 border rounded p-2 mt-3 bg-light">
        <small class="text-muted">Showing ${start} to ${end} of ${total} warehouse products</small>
        <div class="btn-group btn-group-sm" role="group" aria-label="Product linking pagination">
          <button type="button" class="btn btn-outline-secondary" ${state.page > 1 ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(1)">First</button>
          <button type="button" class="btn btn-outline-secondary" ${state.page > 1 ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${Math.max(1, state.page - 1)})">← Prev</button>
          <button type="button" class="btn btn-primary" disabled>Page ${state.page} of ${totalPages}</button>
          <button type="button" class="btn btn-outline-secondary" ${state.page < totalPages ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${Math.min(totalPages, state.page + 1)})">Next →</button>
          <button type="button" class="btn btn-outline-secondary" ${state.page < totalPages ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${totalPages})">Last</button>
        </div>
      </div>`;
  };

  window.loadProductLinkingData = function (force) {
    return hydrate(force === true);
  };

  window.filterFlatListings = function () {
    const search = String(document.getElementById("modalListingSearch")?.value || "").trim().toLowerCase();
    const linkable = typeof getLinkableListings === "function" ? getLinkableListings(currentWarehouseId) : state.unlinked;
    const filtered = !search ? linkable : linkable.filter(listing => [
      listing.external_sku,
      listing.sku,
      listing.title,
      listing.external_listing_id,
      listing.external_id,
      listing.asin
    ].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderFlatListings === "function") renderFlatListings(filtered);
  };

  window.searchWarehouseForLinking = function () {
    const search = String(document.getElementById("modalWarehouseSearch")?.value || "").trim().toLowerCase();
    const products = !search ? state.products : state.products.filter(product => [
      product.sku,
      product.name,
      product.group_name,
      product.barcode
    ].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderWarehouseInModal === "function") {
      renderWarehouseInModal(products, currentListingId, search);
    }
  };

  function wire() {
    const form = document.getElementById("bt38ProductLinkingFilterForm");
    if (form && !form.dataset.bt38SessionWired) {
      form.dataset.bt38SessionWired = "1";
      form.addEventListener("submit", event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        state.page = 1;
        render();
      }, true);
      form.querySelectorAll("input, select").forEach(field => {
        field.addEventListener(field.tagName === "SELECT" ? "change" : "input", event => {
          event.preventDefault();
          event.stopImmediatePropagation();
          state.page = 1;
          render();
        }, true);
      });
    }

    const clear = form?.querySelector('a[href="/product-linking"]');
    if (clear && !clear.dataset.bt38SessionWired) {
      clear.dataset.bt38SessionWired = "1";
      clear.addEventListener("click", event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        form.reset();
        state.page = 1;
        render();
      }, true);
    }
  }

  function boot() {
    wire();
    hydrate(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();