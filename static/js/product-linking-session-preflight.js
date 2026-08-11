// Product Linking session preflight.
//
// Purpose:
// - prevent a stale IndexedDB relationship snapshot from racing the governed
//   Product Linking session controller during deployment alignment;
// - keep Product Linking stock display read-only so quantity authority remains
//   exclusively in Warehouse;
// - load the existing single Product Linking session controller only after the
//   one-time cache generation reset has completed.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const REVISION = "current-relationship-session-v7";
  const MARKER = "bt38-product-linking-session-preflight";
  const DB_NAME = "bt38-browser-cache";
  const STORE_NAME = "snapshots";
  const OLD_CACHE_KEY = "product-linking-v4";

  function markerCurrent() {
    try {
      return window.localStorage.getItem(MARKER) === REVISION;
    } catch (_) {
      return false;
    }
  }

  function setMarker() {
    try {
      window.localStorage.setItem(MARKER, REVISION);
    } catch (_) {}
  }

  function clearOldRelationshipSnapshot() {
    if (markerCurrent() || !window.indexedDB) return Promise.resolve();

    return new Promise((resolve) => {
      const request = window.indexedDB.open(DB_NAME, 1);

      request.onupgradeneeded = function () {
        const database = request.result;
        if (!database.objectStoreNames.contains(STORE_NAME)) {
          database.createObjectStore(STORE_NAME);
        }
      };

      request.onerror = function () {
        console.warn("[ProductLinkingPreflight] cache open failed; session will fetch governed truth");
        resolve();
      };

      request.onsuccess = function () {
        const database = request.result;
        try {
          if (!database.objectStoreNames.contains(STORE_NAME)) {
            database.close();
            setMarker();
            resolve();
            return;
          }

          const transaction = database.transaction(STORE_NAME, "readwrite");
          transaction.objectStore(STORE_NAME).delete(OLD_CACHE_KEY);
          transaction.oncomplete = function () {
            database.close();
            setMarker();
            resolve();
          };
          transaction.onerror = function () {
            database.close();
            console.warn("[ProductLinkingPreflight] stale snapshot delete failed");
            resolve();
          };
        } catch (error) {
          database.close();
          console.warn("[ProductLinkingPreflight] cache reset unavailable", error);
          resolve();
        }
      };
    });
  }

  function loadSessionController() {
    if (document.querySelector('script[data-bt38-product-linking-core-session]')) {
      return;
    }

    const script = document.createElement("script");
    script.src = "/static/js/product-linking-session.js?v=current-relationship-session-v7";
    script.async = false;
    script.dataset.bt38ProductLinkingCoreSession = "1";
    document.head.appendChild(script);
  }

  // Product Linking is relationship-only. The Stock badge must not reopen the
  // retired Adjust & Push quantity modal. Warehouse is the sole quantity editor.
  document.addEventListener(
    "click",
    function (event) {
      const button = event.target?.closest?.(
        '#warehouseDataContainer tbody tr > td:nth-child(2) button'
      );
      if (!button || !root.contains(button)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    },
    true
  );

  clearOldRelationshipSnapshot()
    .catch((error) => {
      console.warn("[ProductLinkingPreflight] reset failed", error);
    })
    .finally(loadSessionController);

  window.BT38 = window.BT38 || {};
  window.BT38.productLinkingSessionPreflight = {
    revision: REVISION,
    staleSnapshotBlockedBeforeSession: true,
    stockQuantityReadOnlyHere: true
  };
}());
