// Shared BT38 operational table UX contract.
// This file changes table/session/navigation behaviour only. It must never own
// marketplace writes, Warehouse quantity authority, linking, push or sync actions.
(function () {
  "use strict";

  const ALLOWED_PAGE_SIZES = [15, 25, 50, 100];
  const SEARCH_DEBOUNCE_MS = 350;

  function pageKey() {
    const root = document.querySelector("[data-bt38-page]");
    return root?.dataset?.bt38Page || window.location.pathname;
  }

  function readState() {
    if (
      window.BT38
      && typeof window.BT38.getPageSession === "function"
    ) {
      return window.BT38.getPageSession(pageKey(), {});
    }
    return {};
  }

  function writeState(patch) {
    if (
      window.BT38
      && typeof window.BT38.setPageSession === "function"
    ) {
      return window.BT38.setPageSession(pageKey(), patch || {});
    }
    return patch || {};
  }

  function currentPageSize() {
    const select = document.getElementById("bt38ResultsPerPageSelect")
      || document.getElementById("mcf-page-size")
      || document.querySelector('select[name="per_page"]');
    const value = Number.parseInt(select?.value || "15", 10);
    return ALLOWED_PAGE_SIZES.includes(value) ? value : 15;
  }

  function showLoading() {
    let node = document.getElementById("bt38OperationalTableLoading");
    if (!node) {
      node = document.createElement("div");
      node.id = "bt38OperationalTableLoading";
      node.setAttribute("aria-live", "polite");
      node.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Loading…';
      Object.assign(node.style, {
        position: "fixed",
        left: "50%",
        top: "84px",
        transform: "translateX(-50%)",
        zIndex: "2000",
        padding: "8px 14px",
        borderRadius: "8px",
        background: "rgba(255,255,255,.96)",
        boxShadow: "0 2px 12px rgba(0,0,0,.12)",
        fontWeight: "600"
      });
      document.body.appendChild(node);
    }
    node.hidden = false;
  }

  function hideLoading() {
    const node = document.getElementById("bt38OperationalTableLoading");
    if (node) node.hidden = true;
  }

  function preserveState() {
    const params = new URLSearchParams(window.location.search);
    writeState({
      page: Number.parseInt(params.get("page") || "1", 10) || 1,
      perPage: currentPageSize(),
      query: params.get("q") || params.get("search") || "",
      href: window.location.href
    });
  }

  function navigate(url) {
    preserveState();
    showLoading();
    window.location.assign(url);
  }

  function serverPageSizeControl() {
    const select = document.getElementById("bt38ResultsPerPageSelect");
    if (!select || select.dataset.bt38SharedContract === "1") return;
    select.dataset.bt38SharedContract = "1";

    select.addEventListener("change", (event) => {
      const perPage = Number.parseInt(event.target.value, 10);
      if (!ALLOWED_PAGE_SIZES.includes(perPage)) return;
      writeState({ perPage, page: 1 });

      // Product Linking owns its server-paged fetch and listens to this same
      // control. Do not create a second request path there.
      if (pageKey() === "productLinking") return;

      const url = new URL(window.location.href);
      url.searchParams.set("per_page", String(perPage));
      url.searchParams.set("page", "1");
      navigate(url.toString());
    }, true);
  }

  function serverPaginationLinks() {
    document.querySelectorAll(".bt38-page-nav a.bt38-page-link[href]").forEach((link) => {
      if (link.dataset.bt38SharedContract === "1") return;
      link.dataset.bt38SharedContract = "1";
      link.addEventListener("click", (event) => {
        if (link.classList.contains("disabled")) {
          event.preventDefault();
          return;
        }
        if (pageKey() === "productLinking") return;
        event.preventDefault();
        event.stopImmediatePropagation();
        navigate(link.href);
      }, true);
    });
  }

  function rememberFilters() {
    document.querySelectorAll('form input[name], form select[name]').forEach((field) => {
      if (field.dataset.bt38SessionRemember === "1") return;
      field.dataset.bt38SessionRemember = "1";
      const save = () => {
        const state = readState();
        const filters = Object.assign({}, state.filters || {});
        filters[field.name] = field.value;
        writeState({ filters });
      };
      field.addEventListener("input", save);
      field.addEventListener("change", save);
    });
  }

  function markTableReady() {
    document.querySelectorAll("table").forEach((table) => {
      if (!table.closest("main")) return;
      table.dataset.bt38TableReady = "1";
    });
    hideLoading();
  }

  document.addEventListener("DOMContentLoaded", () => {
    preserveState();
    serverPageSizeControl();
    serverPaginationLinks();
    rememberFilters();
    markTableReady();
  });

  window.addEventListener("pageshow", hideLoading);
  window.addEventListener("beforeunload", preserveState);

  window.BT38 = window.BT38 || {};
  window.BT38.operationalTableContract = {
    pageSizes: ALLOWED_PAGE_SIZES.slice(),
    sessionControlled: true,
    sessionOwner: "BT38.getPageSession/setPageSession",
    loadingStyle: "shared",
    serverPagedExpansion: true,
    eventOwnership: "page-specific-exact-event-only",
    businessActionsChanged: false,
    searchDebounceMs: SEARCH_DEBOUNCE_MS
  };
}());