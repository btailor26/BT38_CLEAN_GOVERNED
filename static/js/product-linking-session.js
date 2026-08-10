// Product Linking browser-session controller.
// One governed snapshot is bootstrapped once and then kept aligned by exact
// affected-record deltas. No timer-based expiry or routine full refresh.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const CACHE_DB_NAME = "bt38-browser-cache";
  const CACHE_STORE_NAME = "snapshots";
  const CACHE_KEY = "product-linking-v3";
  const FULL_DATASET_LIMIT = 5000;
  const TARGETED_DATASET_LIMIT = 25;
  const PAGE_SIZES = [15, 25, 50, 100];

  const state = {
    hydrated: false,
    hydrating: null,
    products: [],
    unlinked: [],
    listings: [],
    fullLoadedAt: 0,
    page: 1,
    perPage: 15,
    filtered: [],
    pushSettingsLoaded: false,
    pushSettings: { config: {}, stores: [] }
  };

  function uniqueById(items) {
    const seen = new Map();
    (items || []).forEach((item) => {
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

  function listingIdentity(listing) {
    return String(listing?.id ?? listing?.listing_id ?? listing?.marketplace_listing_id ?? "");
  }

  function normaliseIds(values) {
    const result = [];
    const seen = new Set();
    (values || []).forEach((value) => {
      if (value == null || value === "" || Number(value) === 0) return;
      const key = String(value);
      if (!seen.has(key)) { seen.add(key); result.push(key); }
    });
    return result;
  }

  function normalisePageSize(value) {
    const parsed = Number.parseInt(value, 10);
    return PAGE_SIZES.includes(parsed) ? parsed : 15;
  }

  function settingOn(value) {
    if (value === true || value === 1) return true;
    return ["1", "true", "yes", "on", "enabled"].includes(String(value ?? "").trim().toLowerCase());
  }

  function assignLegacyGlobals() {
    try { allWarehouseProducts = state.products; } catch (_) { window.allWarehouseProducts = state.products; }
    try { allUnlinkedListings = state.unlinked; } catch (_) { window.allUnlinkedListings = state.unlinked; }
    try { allMarketplaceListings = state.listings; } catch (_) { window.allMarketplaceListings = state.listings; }
  }

  async function fetchPushSettingsState() {
    if (state.pushSettingsLoaded) return state.pushSettings;
    try {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      let response;
      try {
        response = await fetch("/governed/settings/state", {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal
        });
      } finally {
        window.clearTimeout(timeout);
      }
      if (!response.ok) throw new Error(`Push settings state failed: HTTP ${response.status}`);
      const data = await response.json();
      if (!data || (!data.success && !data.ok)) throw new Error(data?.error || "Push settings state unavailable");
      state.pushSettings = { config: data.config || {}, stores: Array.isArray(data.stores) ? data.stores : [] };
      state.pushSettingsLoaded = true;
    } catch (error) {
      console.warn("[ProductLinkingSession] push settings evidence unavailable", error);
      state.pushSettings = { config: {}, stores: [] };
      state.pushSettingsLoaded = false;
    }
    return state.pushSettings;
  }

  function pushSettingsEvidence(listing, product) {
    if (listing?.push_status === "read_only" || listing?.is_fba) return { label: "Push settings: FBA read-only · Amazon authority", healthy: true, relationshipBlocked: false };
    const config = state.pushSettings?.config || {};
    const store = (state.pushSettings?.stores || []).find((item) => sameId(item?.id, listing?.store_id));
    const globalOn = ["push_enabled", "runtime_push_enabled", "marketplace_push_enabled", "manual_push_enabled"].every((key) => settingOn(config[key]));
    const quantityOn = settingOn(config.quantity_push_enabled);
    const groupOn = settingOn(config.group_push_enabled);
    const autoOn = Boolean(store?.auto_push_enabled);
    const relationshipBlocked = !product?.master_product_group_id;
    return { label: [`Global ${globalOn ? "ON" : "OFF"}`, `Qty ${quantityOn ? "ON" : "OFF"}`, `Group ${groupOn ? "ON" : "OFF"}`, `Auto ${autoOn ? "ON" : "OFF"}`].join(" · "), healthy: globalOn && quantityOn && groupOn, relationshipBlocked };
  }

  function renderRelationshipAndPushEvidence(pageRows) {
    const products = Array.isArray(pageRows) ? pageRows : [];
    document.querySelectorAll(".bt38-push-settings-evidence").forEach((node) => node.remove());
    products.forEach((product) => {
      const row = document.querySelector(`tr[data-warehouse-id="${CSS.escape(String(product?.id ?? ""))}"]`);
      if (!row) return;
      const groupId = product?.master_product_group_id;
      const statusCell = row.children?.[2];
      const groupPushButton = row.querySelector(".bt38-qty-push-open");
      if (!groupId) {
        if (statusCell) { statusCell.innerHTML = '<span class="badge bg-danger">Missing Group ID</span>'; statusCell.title = "Warehouse/listing relationship is incomplete. Group push is blocked until the permanent group ID exists."; }
        if (groupPushButton) { groupPushButton.disabled = true; groupPushButton.setAttribute("aria-disabled", "true"); groupPushButton.title = "Group push blocked: permanent Group ID is missing"; }
      }
      const listingCell = row.children?.[4];
      if (!listingCell) return;
      const cards = Array.from(listingCell.querySelectorAll(":scope > .d-block"));
      (product?.listings || []).forEach((listing, index) => {
        const card = cards[index]; if (!card) return;
        const evidence = pushSettingsEvidence(listing, product);
        const line = document.createElement("div");
        line.className = "bt38-push-settings-evidence small mt-1";
        if (evidence.relationshipBlocked) { line.classList.add("text-danger"); line.textContent = `Relationship BLOCKED · no Group ID · ${evidence.label}`; }
        else { line.classList.add(evidence.healthy ? "text-success" : "text-warning"); line.textContent = evidence.label; }
        card.appendChild(line);
      });
    });
  }

  function openCacheDatabase() {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) return resolve(null);
      const request = window.indexedDB.open(CACHE_DB_NAME, 1);
      request.onupgradeneeded = () => { const database = request.result; if (!database.objectStoreNames.contains(CACHE_STORE_NAME)) database.createObjectStore(CACHE_STORE_NAME); };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Unable to open Product Linking cache"));
    });
  }

  async function readSnapshot() {
    try {
      const database = await openCacheDatabase(); if (!database) return null;
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction(CACHE_STORE_NAME, "readonly");
        const request = transaction.objectStore(CACHE_STORE_NAME).get(CACHE_KEY);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error || new Error("Unable to read Product Linking cache"));
        transaction.oncomplete = () => database.close();
      });
    } catch (error) { console.warn("[ProductLinkingSession] cache read unavailable", error); return null; }
  }

  async function clearSnapshot() {
    try {
      const database = await openCacheDatabase(); if (!database) return;
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(CACHE_STORE_NAME, "readwrite");
        transaction.objectStore(CACHE_STORE_NAME).delete(CACHE_KEY);
        transaction.oncomplete = () => { database.close(); resolve(); };
        transaction.onerror = () => reject(transaction.error || new Error("Unable to clear Product Linking cache"));
      });
    } catch (error) { console.warn("[ProductLinkingSession] cache clear unavailable", error); }
    state.fullLoadedAt = 0;
  }

  async function writeSnapshot() {
    const snapshot = { fullLoadedAt: state.fullLoadedAt, products: state.products, unlinked: state.unlinked, listings: state.listings };
    try {
      const database = await openCacheDatabase(); if (!database) return;
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(CACHE_STORE_NAME, "readwrite");
        transaction.objectStore(CACHE_STORE_NAME).put(snapshot, CACHE_KEY);
        transaction.oncomplete = () => { database.close(); resolve(); };
        transaction.onerror = () => reject(transaction.error || new Error("Unable to save Product Linking cache"));
      });
    } catch (error) { console.warn("[ProductLinkingSession] cache write unavailable", error); }
  }

  function snapshotExists(snapshot) { return Boolean(snapshot && Array.isArray(snapshot.products) && Array.isArray(snapshot.unlinked) && Array.isArray(snapshot.listings)); }
  function applySnapshot(snapshot) { state.products = uniqueById(snapshot?.products || []); state.unlinked = uniqueById(snapshot?.unlinked || []); state.listings = uniqueById(snapshot?.listings || []); state.fullLoadedAt = Number(snapshot?.fullLoadedAt || 0); state.hydrated = true; assignLegacyGlobals(); }

  async function fetchDataset(search, limit) {
    const targeted = Boolean(String(search || "").trim());
    const rowLimit = targeted ? TARGETED_DATASET_LIMIT : (limit || FULL_DATASET_LIMIT);
    const params = new URLSearchParams({ page: "1", per_page: String(rowLimit), limit: String(rowLimit), search: String(search || "").trim(), platform: "all", store: "all", show_linked: "all", section: "all" });
    const response = await fetch(`/governed/product-linking/data?${params.toString()}`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(`Product Linking hydration failed: HTTP ${response.status}`);
    const data = await response.json(); if (!data.success) throw new Error(data.error || "Product Linking hydration failed"); return data;
  }

  async function fetchFullSnapshot() {
    const data = await fetchDataset("", FULL_DATASET_LIMIT);
    state.products = uniqueById(data.warehouse_products || []); state.unlinked = uniqueById(data.unlinked_listings || []); state.listings = uniqueById(data.all_marketplace_listings || data.listings || []); state.fullLoadedAt = Date.now(); state.hydrated = true; state.page = 1; assignLegacyGlobals(); await writeSnapshot();
  }

  async function fetchInitialSnapshotOnce() {
    const work = async () => { const latest = await readSnapshot(); if (snapshotExists(latest)) applySnapshot(latest); else await fetchFullSnapshot(); };
    if (navigator.locks?.request) return navigator.locks.request("bt38-product-linking-initial-snapshot", { mode: "exclusive" }, work);
    return work();
  }

  async function hydrate() {
    if (state.hydrated) { render(); void fetchPushSettingsState().then(render); return; }
    if (state.hydrating) return state.hydrating;
    state.hydrating = (async () => {
      const loading = document.getElementById("warehouseLoadingState"), errorBox = document.getElementById("warehouseErrorState"), container = document.getElementById("warehouseDataContainer");
      if (loading) loading.classList.remove("d-none"); if (errorBox) errorBox.classList.add("d-none"); if (container) container.classList.add("d-none");
      try {
        const cached = await readSnapshot(); if (snapshotExists(cached)) applySnapshot(cached); else await fetchInitialSnapshotOnce();
        render();
        if (loading) loading.classList.add("d-none");
        if (container) container.classList.remove("d-none");
        void fetchPushSettingsState().then(render);
      } catch (error) {
        console.error("[ProductLinkingSession] hydration failed", error); if (errorBox) errorBox.classList.remove("d-none"); const message = document.getElementById("warehouseErrorMessage"); if (message) message.textContent = error.message || "Failed to load product groups."; throw error;
      } finally {
        if (loading) loading.classList.add("d-none");
        state.hydrating = null;
      }
    })(); return state.hydrating;
  }

  function mergeTargetedData(data, affectedListingIds) {
    const changedProducts = uniqueById(data.warehouse_products || []);
    const changedProductIds = new Set(changedProducts.map(productIdentity).filter(Boolean));
    const listingIds = new Set(normaliseIds(affectedListingIds));

    // Remove the affected listing only from stale cached products. Fresh rows
    // returned by the backend are authoritative and must retain the listing so
    // link/re-link/unlink verification sees the relationship that the DB wrote.
    state.products = state.products
      .filter((product) => !changedProductIds.has(productIdentity(product)))
      .map((product) => {
        const listings = (product.listings || []).filter((listing) => !listingIds.has(listingIdentity(listing)));
        return { ...product, listings, linked_count: listings.length };
      })
      .concat(changedProducts);

    const returnedUnlinked = uniqueById(data.unlinked_listings || []);
    returnedUnlinked.forEach((listing) => listingIds.add(listingIdentity(listing)));
    state.unlinked = state.unlinked.filter((listing) => !listingIds.has(listingIdentity(listing))).concat(returnedUnlinked);

    const returnedListings = uniqueById(data.all_marketplace_listings || data.listings || []);
    returnedListings.forEach((listing) => listingIds.add(listingIdentity(listing)));
    state.listings = state.listings.filter((listing) => !listingIds.has(listingIdentity(listing))).concat(returnedListings);
    assignLegacyGlobals();
  }

  function mutationSearchKeys(contract, identity) {
    const keys = []; const add = (value) => { const text = String(value ?? "").trim(); if (text && !keys.includes(text)) keys.push(text); };
    normaliseIds(contract?.affected_warehouse_stock_ids).forEach(add); normaliseIds(contract?.affected_group_ids).forEach(add); normaliseIds(contract?.affected_listing_ids).forEach(add);
    add(identity?.warehouseSku); add(identity?.listingSku); add(identity?.warehouseId); add(identity?.groupId); add(identity?.previousGroupId); add(identity?.originalGroupId); add(identity?.listingId); return keys;
  }

  async function applyMutationContract(contract, identity) {
    if (contract && contract.changed === false) return contract;
    const listingIds = normaliseIds([...(contract?.affected_listing_ids || []), identity?.listingId]);
    const keys = mutationSearchKeys(contract, identity); if (!keys.length) throw new Error("Affected Product Linking rows could not be identified");
    for (const key of keys) { const data = await fetchDataset(key, TARGETED_DATASET_LIMIT); mergeTargetedData(data, listingIds); }
    render(); await writeSnapshot(); return contract;
  }
  async function refreshAffectedRecord(identity) { return applyMutationContract({ changed: true }, identity); }

  function getFilters() {
    const form = document.getElementById("bt38ProductLinkingFilterForm"); if (!form) return { search: "", platform: "", store: "", showLinked: "all" };
    return { search: String(form.querySelector('[name="search"]')?.value || "").trim().toLowerCase(), platform: String(form.querySelector('[name="platform"]')?.value || "").trim().toLowerCase(), store: String(form.querySelector('[name="store"]')?.value || "").trim().toLowerCase(), showLinked: String(form.querySelector('[name="show_linked"]')?.value || "all").trim().toLowerCase() };
  }

  function productMatches(product, filters) {
    const listings = product.listings || [];
    const haystack = [product.sku, product.name, product.group_name, product.barcode, product.master_product_group_id, ...listings.flatMap((listing) => [listing.external_sku, listing.sku, listing.title, listing.external_listing_id, listing.external_id, listing.asin, listing.fnsku, listing.platform, listing.store_name])].filter(Boolean).join(" ").toLowerCase();
    if (filters.search && !haystack.includes(filters.search)) return false;
    if (filters.platform && filters.platform !== "all" && !listings.some((listing) => String(listing.platform || "").toLowerCase().includes(filters.platform))) return false;
    if (filters.store && filters.store !== "all" && !listings.some((listing) => String(listing.store_id || "").toLowerCase() === filters.store)) return false;
    const linkedCount = Number.parseInt(product.linked_count || listings.length || 0, 10);
    if (filters.showLinked === "linked" && linkedCount <= 0) return false; if (filters.showLinked === "unlinked" && linkedCount > 0) return false; return true;
  }

  function render() {
    if (!state.hydrated || typeof renderWarehouseProducts !== "function") return;
    const filters = getFilters(); state.filtered = state.products.filter((product) => productMatches(product, filters));
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.perPage)); state.page = Math.min(Math.max(state.page, 1), totalPages);
    const start = (state.page - 1) * state.perPage, pageRows = state.filtered.slice(start, start + state.perPage);
    try { productLinkingPage = state.page; productLinkingPerPage = state.perPage; productLinkingPagination = { page: state.page, per_page: state.perPage, total_stock: state.filtered.length, total_pages: totalPages, has_prev: state.page > 1, has_next: state.page < totalPages, prev_page: Math.max(1, state.page - 1), next_page: Math.min(totalPages, state.page + 1) }; } catch (_) {}
    renderWarehouseProducts(pageRows); renderRelationshipAndPushEvidence(pageRows); const count = document.getElementById("warehouseGroupsCount"); if (count) count.textContent = `${state.filtered.length} matching of ${state.products.length} warehouse groups`; if (typeof feather !== "undefined") feather.replace();
  }

  function mappingExists(listingId, warehouseId, groupId = null) {
    return state.products.some((product) => {
      const groupMatches = groupId != null && sameId(product.master_product_group_id, groupId);
      const warehouseMatches = [product.id, product.warehouse_stock_id, product.stock_id].some((value) => sameId(value, warehouseId));
      if (!groupMatches && !warehouseMatches) return false;
      return (product.listings || []).some((listing) => [listing.id, listing.listing_id, listing.marketplace_listing_id].some((value) => sameId(value, listingId)));
    });
  }

  let pendingExplicitUnlink = null; let explicitUnlinkInFlight = false;
  function clearPendingExplicitUnlink() { pendingExplicitUnlink = null; explicitUnlinkInFlight = false; const button = document.getElementById("confirmExplicitUnlinkButton"); if (button) { button.disabled = false; button.textContent = "Confirm Unlink"; } }
  function closeOpenModals() { document.querySelectorAll(".modal.show").forEach((modal) => { const instance = window.bootstrap?.Modal?.getInstance(modal); if (instance) instance.hide(); }); }

  window.bt38ProductLinkingSetPage = function (page) { state.page = Number.parseInt(page || 1, 10) || 1; render(); return false; };
  window.bt38ProductLinkingSetPageSize = function (size) { state.perPage = normalisePageSize(size); state.page = 1; render(); return false; };
  window.renderProductLinkingPagination = function () {
    const total = state.filtered.length, totalPages = Math.max(1, Math.ceil(total / state.perPage)); const start = total === 0 ? 0 : ((state.page - 1) * state.perPage) + 1; const end = Math.min(total, state.page * state.perPage);
    const options = PAGE_SIZES.map((size) => `<option value="${size}" ${state.perPage === size ? "selected" : ""}>${size}</option>`).join("");
    return `<div class="d-flex flex-wrap align-items-center justify-content-between gap-2 border rounded p-2 mt-3 bg-light"><small class="text-muted">Showing ${start} to ${end} of ${total} warehouse products</small><div class="d-flex align-items-center gap-2"><label class="small text-muted mb-0" for="bt38ProductLinkingPageSize">Rows</label><select id="bt38ProductLinkingPageSize" class="form-select form-select-sm" style="width:auto" onchange="bt38ProductLinkingSetPageSize(this.value)">${options}</select><div class="btn-group btn-group-sm" role="group" aria-label="Product linking pagination"><button type="button" class="btn btn-outline-secondary" ${state.page > 1 ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(1)">First</button><button type="button" class="btn btn-outline-secondary" ${state.page > 1 ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${Math.max(1, state.page - 1)})">← Prev</button><button type="button" class="btn btn-primary" disabled>Page ${state.page} of ${totalPages}</button><button type="button" class="btn btn-outline-secondary" ${state.page < totalPages ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${Math.min(totalPages, state.page + 1)})">Next →</button><button type="button" class="btn btn-outline-secondary" ${state.page < totalPages ? "" : "disabled"} onclick="bt38ProductLinkingSetPage(${totalPages})">Last</button></div></div></div>`;
  };
  window.loadProductLinkingData = function () { return hydrate(); };
  window.bt38RefreshProductLinkingRecord = refreshAffectedRecord;
  window.bt38ApplyProductLinkingMutation = applyMutationContract;
  window.bt38InvalidateProductLinkingSnapshot = async function () { state.fullLoadedAt = 0; state.hydrated = false; state.hydrating = null; state.pushSettingsLoaded = false; await clearSnapshot(); return hydrate(); };

  window.filterFlatListings = function () {
    const search = String(document.getElementById("modalListingSearch")?.value || "").trim().toLowerCase();
    const linkable = typeof getLinkableListings === "function" ? getLinkableListings(currentWarehouseId) : state.unlinked;
    const filtered = !search ? linkable : linkable.filter((listing) => [listing.external_sku, listing.sku, listing.title, listing.external_listing_id, listing.external_id, listing.asin].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderFlatListings === "function") renderFlatListings(filtered);
  };
  window.searchWarehouseForLinking = function () {
    const search = String(document.getElementById("modalWarehouseSearch")?.value || "").trim().toLowerCase();
    const products = !search ? state.products : state.products.filter((product) => [product.sku, product.name, product.group_name, product.barcode].filter(Boolean).join(" ").toLowerCase().includes(search));
    if (typeof renderWarehouseInModal === "function") renderWarehouseInModal(products, currentListingId, search);
  };

  window.linkListingToWarehouse = async function (listingId, warehouseId, listingSku, warehouseSku, userConfirmed = false) {
    try {
      const response = await fetch("/governed/product-linking/link-listing-to-warehouse", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing_id: listingId, warehouse_id: warehouseId, user_confirmed: userConfirmed }) });
      const data = await response.json();
      if (data.requires_confirmation) {
        const confirmMsg = `This will add "${listingSku}" to the group for warehouse SKU "${data.warehouse_sku || warehouseSku}".\n\nThe group currently has ${data.existing_members || 0} linked listing(s).\n\nAll listings in this group will share the same quantity. Continue?`;
        if (window.confirm(confirmMsg)) return window.linkListingToWarehouse(listingId, warehouseId, listingSku, warehouseSku, true); return;
      }
      if (!response.ok || (!data.success && !data.ok)) throw new Error(data.error || data.message || `HTTP ${response.status}`);
      await applyMutationContract(data, { listingId, warehouseId, listingSku, warehouseSku, groupId: data.group_id, previousGroupId: data.previous_group_id, originalGroupId: data.original_group_id });
      if (!mappingExists(listingId, warehouseId, data.group_id)) throw new Error("The relationship changed, but the affected browser row could not be verified.");
      closeOpenModals(); window.alert(data.changed === false ? `${listingSku} is already linked to ${warehouseSku}.` : `Successfully linked ${listingSku} to ${warehouseSku}.`); return;
    } catch (error) { console.error("[ProductLinkingSession] verified link failed", error); window.alert(`Link failed: ${error.message || error}`); }
  };

  window.unlinkListing = function (listingId, listingSku, userConfirmed = false, groupId = null, warehouseStockId = null) {
    if (!groupId) { window.alert("This listing has no governed group ID and cannot be safely unlinked."); return; }
    pendingExplicitUnlink = { listingId, listingSku, groupId, warehouseStockId };
    const skuElement = document.getElementById("unlinkConfirmSku"), groupElement = document.getElementById("unlinkConfirmGroup"); if (skuElement) skuElement.textContent = String(listingSku || ""); if (groupElement) groupElement.textContent = String(groupId);
    const modalElement = document.getElementById("unlinkListingConfirmModal");
    if (!modalElement || !window.bootstrap?.Modal) { clearPendingExplicitUnlink(); window.alert("Unlink confirmation is unavailable. No relationship was changed."); return; }
    window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
  };

  async function confirmExplicitUnlink() {
    if (!pendingExplicitUnlink || explicitUnlinkInFlight) return;
    const identity = { ...pendingExplicitUnlink }; explicitUnlinkInFlight = true;
    const button = document.getElementById("confirmExplicitUnlinkButton"); if (button) { button.disabled = true; button.textContent = "Unlinking..."; }
    try {
      const response = await fetch(`/governed/groups/${encodeURIComponent(identity.groupId)}/unlink`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing_id: identity.listingId, warehouse_stock_id: identity.warehouseStockId, user_confirmed: true }) });
      const data = await response.json(); if (!response.ok || (!data.success && !data.ok)) throw new Error(data.error || data.message || `HTTP ${response.status}`);
      await applyMutationContract(data, { listingId: identity.listingId, listingSku: identity.listingSku, warehouseId: data.warehouse_stock_id || identity.warehouseStockId, groupId: data.group_id, previousGroupId: data.previous_group_id || identity.groupId, originalGroupId: data.original_group_id });
      closeOpenModals(); window.alert(data.changed === false ? `${identity.listingSku} is already in its original group.` : `Removed ${identity.listingSku} from the shared group and restored its original group.`); clearPendingExplicitUnlink(); return;
    } catch (error) {
      console.error("[ProductLinkingSession] explicit unlink failed", error); window.alert(`Unlink failed: ${error.message || error}`); explicitUnlinkInFlight = false; if (button) { button.disabled = false; button.textContent = "Confirm Unlink"; }
    }
  }

  function wire() {
    const unlinkConfirmButton = document.getElementById("confirmExplicitUnlinkButton");
    if (unlinkConfirmButton && !unlinkConfirmButton.dataset.bt38ExplicitUnlinkWired) { unlinkConfirmButton.dataset.bt38ExplicitUnlinkWired = "1"; unlinkConfirmButton.addEventListener("click", (event) => { event.preventDefault(); event.stopImmediatePropagation(); void confirmExplicitUnlink(); }); }
    const unlinkConfirmModal = document.getElementById("unlinkListingConfirmModal");
    if (unlinkConfirmModal && !unlinkConfirmModal.dataset.bt38ExplicitUnlinkWired) { unlinkConfirmModal.dataset.bt38ExplicitUnlinkWired = "1"; unlinkConfirmModal.addEventListener("hidden.bs.modal", () => { if (!explicitUnlinkInFlight) clearPendingExplicitUnlink(); }); }
    const form = document.getElementById("bt38ProductLinkingFilterForm");
    if (form && !form.dataset.bt38SessionWired) {
      form.dataset.bt38SessionWired = "1";
      form.addEventListener("submit", (event) => { event.preventDefault(); event.stopImmediatePropagation(); state.page = 1; render(); }, true);
      form.querySelectorAll("input, select").forEach((field) => { field.addEventListener(field.tagName === "SELECT" ? "change" : "input", (event) => { event.preventDefault(); event.stopImmediatePropagation(); state.page = 1; render(); }, true); });
    }
    const clear = form?.querySelector('a[href="/product-linking"]');
    if (clear && !clear.dataset.bt38SessionWired) { clear.dataset.bt38SessionWired = "1"; clear.addEventListener("click", (event) => { event.preventDefault(); event.stopImmediatePropagation(); form.reset(); state.page = 1; render(); }, true); }

    window.addEventListener("bt38-marketplace-event", (event) => {
      const detail = event?.detail || {};
      const identity = {
        warehouseId: detail.warehouse_stock_id,
        groupId: detail.group_id,
        listingId: detail.listing_id,
        listingSku: detail.seller_sku,
        warehouseSku: detail.seller_sku
      };
      if (!identity.warehouseId && !identity.groupId && !identity.listingId && !identity.listingSku) return;
      void refreshAffectedRecord(identity).catch((error) => {
        console.warn("[ProductLinkingSession] marketplace event targeted refresh failed", error);
      });
    });
  }

  function boot() { wire(); hydrate(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true }); else boot();
}());