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

// Product Linking uses the same browser-session structure as Warehouse.
// Load its local controller only on that page; POST mutations remain governed.
(function loadProductLinkingSessionController() {
  if (!document.querySelector('[data-bt38-page="productLinking"]')) return;
  if (document.querySelector('script[data-bt38-product-linking-session]')) return;

  const script = document.createElement("script");
  script.src = "/static/js/product-linking-session.js?v=product-linking-session-only";
  script.async = false;
  script.dataset.bt38ProductLinkingSession = "1";
  document.head.appendChild(script);
})();
