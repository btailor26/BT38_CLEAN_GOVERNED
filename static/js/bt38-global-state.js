// ======================================================
// BT38 GLOBAL STATE ENGINE (STABLE CLEAN VERSION)
// ======================================================

window.BT38 = window.BT38 || {};

window.BT38.state = {
  page: null,
  cache: {},
  session: {
    allowFetch: false
  }
};

window.BT38.canFetch = function(context) {
  return window.BT38.state.session.allowFetch === true;
};

window.BT38.fetch = async function(url, options = {}, context = "default") {
  if (!window.BT38.canFetch(context)) {
    console.warn("[BT38 BLOCKED FETCH]", url);
    return null;
  }
  return fetch(url, options);
};

window.BT38.initPage = function(pageName) {
  window.BT38.state.page = pageName;
  window.BT38.state.cache[pageName] = window.BT38.state.cache[pageName] || {};
};

window.BT38.enableFetch = function() {
  window.BT38.state.session.allowFetch = true;
};

window.BT38.disableFetch = function() {
  window.BT38.state.session.allowFetch = false;
};

// Shared browser-session state for all BT38 pages.
// UI state belongs in sessionStorage, not in the database.
window.BT38.getPageSession = function(pageName, defaults = {}) {
  const key = `bt38:page:${pageName}`;

  try {
    const stored = window.sessionStorage.getItem(key);
    return {
      ...defaults,
      ...(stored ? JSON.parse(stored) : {})
    };
  } catch (error) {
    console.warn("[BT38 PAGE SESSION READ FAILED]", pageName, error);
    return { ...defaults };
  }
};

window.BT38.setPageSession = function(pageName, values = {}) {
  const key = `bt38:page:${pageName}`;

  try {
    const current = window.BT38.getPageSession(pageName, {});
    const next = {
      ...current,
      ...values
    };

    window.sessionStorage.setItem(
      key,
      JSON.stringify(next)
    );

    window.BT38.state.cache[pageName] = next;
    return next;
  } catch (error) {
    console.warn("[BT38 PAGE SESSION WRITE FAILED]", pageName, error);
    return values;
  }
};

// Product Linking uses the same browser-session structure as Warehouse.
// The existing template still defines a legacy server-driven loader. Replace it
// immediately before DOMContentLoaded so it cannot issue page-by-page reads.
(function loadProductLinkingSessionController() {
  if (!document.querySelector('[data-bt38-page="productLinking"]')) return;
  if (document.querySelector('script[data-bt38-product-linking-session]')) return;

  window.loadProductLinkingData = function () {
    console.debug("[ProductLinkingSession] waiting for session controller");
    return Promise.resolve();
  };

  const script = document.createElement("script");
  const loaderUrl = new URL(document.currentScript.src, window.location.origin);
  const assetVersion = loaderUrl.searchParams.get("v") || "bt38-runtime";
  script.src = `/static/js/product-linking-session.js?v=${encodeURIComponent(assetVersion)}`;
  script.async = false;
  script.dataset.bt38ProductLinkingSession = "1";

  // Marketplace write truth and browser refresh truth are separate concerns.
  // A successful group push must never be relabelled as "Push error" only
  // because the targeted Product Linking row refresh could not be reconciled.
  // Real marketplace failures are still re-thrown unchanged.
  script.onload = function () {
    const applyMutation = window.bt38ApplyProductLinkingMutation;
    if (typeof applyMutation !== "function" || applyMutation.bt38PushTruthGuard) {
      return;
    }

    const guardedApplyMutation = async function (contract, identity) {
      try {
        return await applyMutation(contract, identity);
      } catch (error) {
        const marketplacePushSucceeded = Boolean(
          contract
          && (contract.success || contract.ok)
          && Number(contract.failed || 0) === 0
          && Number(contract.pushed || contract.ok_count || 0) > 0
          && Array.isArray(contract.affected_group_ids)
        );

        if (!marketplacePushSucceeded) {
          throw error;
        }

        console.warn(
          "[ProductLinkingSession] marketplace push succeeded; targeted UI refresh could not be reconciled",
          error,
          contract
        );

        return contract;
      }
    };

    guardedApplyMutation.bt38PushTruthGuard = true;
    window.bt38ApplyProductLinkingMutation = guardedApplyMutation;
  };

  document.head.appendChild(script);
})();
