// Product Linking browser-session controller.
// The full governed working set is read at most once every 24 hours per browser.
// Search, filters, pagination and modal search remain local. Governed mutations
// refresh only the affected Warehouse record and merge it into the saved snapshot.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const CACHE_DB_NAME = "bt38-browser-cache";
  const CACHE_STORE_NAME = "snapshots";
  const CACHE_KEY = "product-linking-v2";
  const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
  const FULL_DATASET_LIMIT = 5000;
  const TARGETED_DATASET_LIMIT = 25;

  const state = {
    hydrated: false,
    hydrating: null,
    products: [],
    unlinked: [],
    listings: [],
    fullLoadedAt: 0,
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

  function sameId(left, right) {
    return left != null && right != null && String(left) === String(right);
  }

  function productIdentity(product) {
    return String(product?.id ?? product?.warehouse_stock_id ?? product?.stock_id ?? "");
  }

  function assignLegacyGlobals() {
    try { allWarehouseProducts = state.products; } catch (_) { window.allWarehouseProducts = state.products; }
    try { allUnlinkedListings = state.unlinked; } catch (_) { window.allUnlinkedListings = state.unlinked; }
    try { allMarketplaceListings = state.listings; } catch (_) { window.allMarketplaceListings = state.listings; }
  }

  function openCacheDatabase() {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) {
        resolve(null);
        return;
      }
      const request = window.indexedDB.open(CACHE_DB_NAME, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(CACHE_STORE_NAME)) {
          database.createObjectStore(CACHE_STORE_NAME);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Unable to open Product Linking cache"));
    });
  }

  async function readSnapshot() {
    try {
      const database = await openCacheDatabase();
      if (!database) return null;
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction(CACHE_STORE_NAME, "readonly");
        const request = transaction.objectStore(CACHE_STORE_NAME).get(CACHE_KEY);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error || new Error("Unable to read Product Linking cache"));
        transaction.oncomplete = () => database.close();
      });
    } catch (error) {
      console.warn("[ProductLinkingSession] cache read unavailable", error);
      return null;
    }
  }

  async function writeSnapshot() {
    const snapshot = {
      fullLoadedAt: state.fullLoadedAt,
      products: state.products,
      unlinked: state.unlinked,
      listings: state.listings
    };
    try {
      const database = await openCacheDatabase();
      if (!database) return;
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(CACHE_STORE_NAME, "readwrite");
        transaction.objectStore(CACHE_STORE_NAME).put(snapshot, CACHE_KEY);
        transaction.oncomplete = () => { database.close(); resolve(); };
        transaction.onerror = () => reject(transaction.error || new Error("Unable to save Product Linking cache"));
      });
    } catch (error) {
      console.warn("[ProductLinkingSession] cache write unavailable", error);
    }
  }

  function snapshotIsFresh(snapshot) {
    const loadedAt = Number(snapshot?.fullLoadedAt || 0);
    return loadedAt > 0 && (Date.now() - loadedAt) < CACHE_TTL_MS;
  }

  function applySnapshot(snapshot) {
    state.products = uniqueById(snapshot?.products || []);
    state.unlinked = uniqueById(snapshot?.unlinked || []);
    state.listings = uniqueById(snapshot?.listings || []);
    state.fullLoadedAt = Number(snapshot?.fullLoadedAt || 0);
    state.hydrated = true;
    assignLegacyGlobals();
  }

  async function fetchDataset(search, limit) {
    const targeted = Boolean(String(search || "").trim());
    const rowLimit = targeted ? TARGETED_DATASET_LIMIT : (limit || FULL_DATASET_LIMIT);
    const params = new URLSearchParams({
      page: "1",
      per_page: String(rowLimit),
      limit: String(rowLimit),
      search: String(search || "").trim(),
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

  async function fetchFullSnapshot() {
    const data = await fetchDataset("", FULL_DATASET_LIMIT);
    state.products = uniqueById(data.warehouse_products || []);
    state.unlinked = uniqueById(data.unlinked_listings || []);
    state.listings = uniqueById(data.all_marketplace_listings || data.listings || []);
    state.fullLoadedAt = Date.now();
    state.hydrated = true;
    state.page = 1;
    assignLegacyGlobals();
    await writeSnapshot();
  }

  async function fetchFullSnapshotOnceDaily() {
    const work = async () => {
      const latest = await readSnapshot();
      if (snapshotIsFresh(latest)) {
        applySnapshot(latest);
        return;
      }
      await fetchFullSnapshot();
    };

    if (navigator.locks?.request) {
      return navigator.locks.request("bt38-product-linking-daily-snapshot", { mode: "exclusive" }, work);
    }
    return work();
  }

  async function hydrate() {
    if (state.hydrated) {
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
        const cached = await readSnapshot();
        if (snapshotIsFresh(cached)) applySnapshot(cached);
        else await fetchFullSnapshotOnceDaily();
        render();
        if (loading) loading.classList.add("d-none");
        if (container) container.classList.remove("d-none");
      } catch (error) {
        console.error("[ProductLinkingSession] hydration failed", error);
        if (loading) loading.classList.add("d-none");
        if (errorBox) errorBox.classList.remove("d-none");
        const message = document.getElementById("warehouseErrorMessage");
        if (message) message.textContent = error.message || "Failed to load product groups.";
        throw error;
      } finally {
        state.hydrating = null;
      }
    })();

    return state.hydrating;
  }

  function mergeTargetedData(data, listingId) {
    const changedProducts = uniqueById(data.warehouse_products || []);
    const changedIds = new Set(changedProducts.map(productIdentity).filter(Boolean));
    state.products = state.products
      .filter(product => !changedIds.has(productIdentity(product)))
      .concat(changedProducts);

    const returnedUnlinked = uniqueById(data.unlinked_listings || []);
    state.unlinked = state.unlinked
      .filter(listing => !sameId(listing.id ?? listing.listing_id, listingId))
      .concat(returnedUnlinked.filter(listing => !state.unlinked.some(existing => sameId(existing.id, listing.id))));

    const returnedListings = uniqueById(data.all_marketplace_listings || data.listings || []);
    if (returnedListings.length) {
      const returnedIds = new Set(returnedListings.map(item => String(item.id ?? item.listing_id ?? "")));
      state.listings = state.listings
        .filter(item => !returnedIds.has(String(item.id ?? item.listing_id ?? "")))
        .concat(returnedListings);
    }

    assignLegacyGlobals();
    render();
    return writeSnapshot();
  }

  async function refreshAffectedRecord(identity) {
    const search = String(identity?.warehouseSku || identity?.listingSku || identity?.warehouseId || "").trim();
    if (!search) throw new Error("Affected Product Linking record could not be identified");
    const data = await fetchDataset(search, TARGETED_DATASET_LIMIT);
    await mergeTargetedData(data, identity?.listingId);
    return data;
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
      product.sku, product.name, product.group_name, product.barcode, product.master_product_group_id,
      ...listings.flatMap(listing => [
        listing.external_sku, listing.sku, listing.title, listing.external_listing_id,
        listing.external_id, listing.asin, listing.fnsku, listing.platform, listing.store_name
      ])
    ].filter(Boolean).join(" ").toLowerCase();

    if (filters.search && !haystack.includes(filters.search)) return false;
    if (filters.platform && filters.platform !== "all" &&
        !listings.some(listing => String(listing.platform || "").toLowerCase().includes(filters.platform))) return false;
    if (filters.store && filters.store !== "all" &&
        !listings.some(listing => String(listing.store_id || "").toLowerCase() === filters.store)) return false;

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
        page: state.page, per_page: state.perPage, total_stock: state.filtered.length,
        total_pages: totalPages, has_prev: state.page > 1, has_next: state.page < totalPages,
        prev_page: Math.max(1, state.page - 1), next_page: Math.min(totalPages, state.page + 1)
      };
    } catch (_) {}

    renderWarehouseProducts(pageRows);
    const count = document.getElementById("warehouseGroupsCount");
    if (count) count.textContent = `${state.filtered.length} matching of ${state.products.length} warehouse groups`;
    if (typeof feather !== "undefined") feather.replace();
  }

  function mappingExists(listingId, warehouseId) {
    return state.products.some(product => {
      const productMatchesWarehouse = [product.id, product.warehouse_stock_id, product.stock_id]
        .some(value => sameId(value, warehouseId));
      if (!productMatchesWarehouse) return false;
      return (product.listings || []).some(listing => [listing.id, listing.listing_id, listing.marketplace_listing_id]
        .some(value => sameId(value, listingId)));
    });
  }

  function closeOpenModals() {
    document.querySelectorAll(".modal.show").forEach(modal => {
      const instance = window.bootstrap?.Modal?.getInstance(modal);
      if (instance) instance.hide();
    });
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

  // Page reloads and legacy force=true calls never trigger another full read inside
  // the 24-hour window. A governed mutation must call the targeted record refresh.
  window.loadProductLinkingData = function () {
    return hydrate();
  };
  window.bt38RefreshProductLinkingRecord = refreshAffectedRecord;

  window.filterFlatListings = function () {
    const search = String(document.getElementById("modalListingSearch")?.value || "").trim().toLowerCase();
    const linkable = typeof getLinkableListings === "function" ? getLinkableListings(currentWarehouseId) : state.unlinked;
    const filtered = !search ? linkable : linkable.filter(listing => [
      listing.external_sku, listing.sku, listing.title, listing.external_listing_id, listing.external_id, listing.asin
    ].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderFlatListings === "function") renderFlatListings(filtered);
  };

  window.searchWarehouseForLinking = function () {
    const search = String(document.getElementById("modalWarehouseSearch")?.value || "").trim().toLowerCase();
    const products = !search ? state.products : state.products.filter(product => [
      product.sku, product.name, product.group_name, product.barcode
    ].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderWarehouseInModal === "function") renderWarehouseInModal(products, currentListingId, search);
  };

  window.linkListingToWarehouse = async function (listingId, warehouseId, listingSku, warehouseSku, userConfirmed = false) {
    try {
      const response = await fetch("/governed/product-linking/link-listing-to-warehouse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listing_id: listingId, warehouse_id: warehouseId, user_confirmed: userConfirmed })
      });
      const data = await response.json();

      if (data.requires_confirmation) {
        const confirmMsg = `This will add "${listingSku}" to the group for warehouse SKU "${data.warehouse_sku || warehouseSku}".\n\n` +
          `The group currently has ${data.existing_members || 0} linked listing(s).\n\n` +
          "All listings in this group will share the same quantity. Continue?";
        if (window.confirm(confirmMsg)) {
          return window.linkListingToWarehouse(listingId, warehouseId, listingSku, warehouseSku, true);
        }
        return;
      }

      if (!response.ok || !data.success) {
        throw new Error(data.error || data.message || `HTTP ${response.status}`);
      }

      await refreshAffectedRecord({ listingId, warehouseId, listingSku, warehouseSku });
      if (!mappingExists(listingId, warehouseId)) {
        throw new Error("The server returned success, but the affected relationship could not be verified.");
      }

      closeOpenModals();
      window.alert(`Successfully linked ${listingSku} to ${warehouseSku}.`);
    } catch (error) {
      console.error("[ProductLinkingSession] verified link failed", error);
      window.alert(`Link failed: ${error.message || error}`);
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
    hydrate();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();