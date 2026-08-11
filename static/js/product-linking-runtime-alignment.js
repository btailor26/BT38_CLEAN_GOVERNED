// Product Linking runtime alignment.
//
// Permanent contract:
// - Product Linking manages relationships only.
// - The right-hand Push button is a shortcut into the governed Warehouse group
//   push and NEVER supplies quantity authority.
// - Group push does not mutate Product Linking relationships, so a successful
//   push must not run relationship-targeted searches/merges afterward.
// - Current MarketplaceListing membership wins over an empty permanent/original
//   Warehouse shadow row in Product Linking display.
// - Store auto_push_enabled is future permission only; no automatic push worker
//   is running, so Product Linking must display Auto OFF.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const SNAPSHOT_REVISION = "current-membership-push-shortcut-v6";
  const SNAPSHOT_MARKER = "bt38-product-linking-runtime-alignment";
  let lastRenderedProducts = [];
  let evidenceCorrectionScheduled = false;

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function sameId(left, right) {
    return left != null && right != null && String(left) === String(right);
  }

  function listingSku(listing) {
    return String(
      listing?.external_sku
      || listing?.sku
      || listing?.seller_sku
      || ""
    ).trim().toLowerCase();
  }

  function productSku(product) {
    return String(product?.sku || "").trim().toLowerCase();
  }

  function isEmptyPermanentShadow(product, products) {
    if (asArray(product?.listings).length) return false;
    const sku = productSku(product);
    if (!sku) return false;

    return asArray(products).some((candidate) => {
      if (sameId(candidate?.id, product?.id)) return false;
      return asArray(candidate?.listings).some((listing) => {
        if (listingSku(listing) !== sku) return false;
        const listingWarehouseId = listing?.warehouse_stock_id;
        return !sameId(listingWarehouseId, product?.id);
      });
    });
  }

  function currentRelationshipProducts(products) {
    const rows = asArray(products);
    return rows.filter((product) => !isEmptyPermanentShadow(product, rows));
  }

  function schedulePushSettingsEvidenceCorrection() {
    if (evidenceCorrectionScheduled) return;
    evidenceCorrectionScheduled = true;
    window.requestAnimationFrame(() => {
      evidenceCorrectionScheduled = false;
      correctPushSettingsEvidence();
    });
  }

  function installRenderAlignment(attempt) {
    const original = window.renderWarehouseProducts;
    if (typeof original !== "function") {
      if (attempt < 80) {
        window.setTimeout(() => installRenderAlignment(attempt + 1), 25);
      }
      return;
    }
    if (original.__bt38CurrentRelationshipAligned) return;

    const aligned = function (products) {
      lastRenderedProducts = currentRelationshipProducts(products);
      const result = original(lastRenderedProducts);
      schedulePushSettingsEvidenceCorrection();
      return result;
    };
    aligned.__bt38CurrentRelationshipAligned = true;
    aligned.__bt38Original = original;
    window.renderWarehouseProducts = aligned;
  }

  function correctPushSettingsEvidence() {
    lastRenderedProducts.forEach((product) => {
      const row = root.querySelector(
        `tr[data-warehouse-id="${CSS.escape(String(product?.id ?? ""))}"]`
      );
      if (!row) return;
      const listingCell = row.children?.[4];
      if (!listingCell) return;
      const cards = Array.from(listingCell.querySelectorAll(":scope > .d-block"));

      asArray(product?.listings).forEach((listing, index) => {
        const card = cards[index];
        if (!card) return;
        const line = card.querySelector(".bt38-push-settings-evidence");
        if (!line) return;

        // auto_push_enabled is only future permission in Settings. There is no
        // automatic push worker/runtime, so the operational UI state is OFF.
        const currentText = line.textContent || "";
        const nextText = currentText.replace(/Auto\s+(ON|OFF)/i, "Auto OFF");

        // Idempotent DOM update: never retrigger the observer when unchanged.
        if (nextText !== currentText) {
          line.textContent = nextText;
        }
      });
    });
  }

  async function pushWarehouseGroup(button) {
    const warehouseId = Number.parseInt(button?.dataset?.warehouseId || "", 10);
    const groupId = Number.parseInt(button?.dataset?.groupId || "", 10);

    if (!Number.isInteger(warehouseId) || warehouseId <= 0) {
      window.alert("A valid Warehouse stock ID is required.");
      return;
    }
    if (!Number.isInteger(groupId) || groupId <= 0) {
      window.alert("This row is not linked to a governed group yet.");
      return;
    }

    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
      const response = await fetch(
        `/governed/actions/groups/${encodeURIComponent(groupId)}/push`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-BT38-Shortcut": "product_linking_warehouse_shortcut"
          },
          body: JSON.stringify({
            warehouse_stock_id: warehouseId,
            source: "product_linking_warehouse_shortcut"
          })
        }
      );

      const responseText = await response.text();
      let data = {};
      try {
        data = responseText ? JSON.parse(responseText) : {};
      } catch (_) {
        throw new Error(`Warehouse group shortcut returned HTTP ${response.status}.`);
      }

      if (!response.ok || (!data.success && !data.ok)) {
        throw new Error(
          data.error || data.reason || data.message || "Warehouse group push failed."
        );
      }

      window.alert(
        data.message
        || (
          "Warehouse group push completed.\n\n"
          + `Listings checked: ${data.total_listings || data.total || 0}\n`
          + `Pushed: ${data.pushed || data.ok_count || 0}\n`
          + `Skipped: ${data.skipped || 0}\n`
          + `Failed: ${data.failed || 0}`
        )
      );
    } catch (error) {
      console.error("[ProductLinkingAlignment] Warehouse shortcut push failed", error);
      window.alert(`Push error: ${error.message || error}`);
    } finally {
      button.disabled = false;
      button.innerHTML = originalHtml;
      if (typeof window.feather !== "undefined") window.feather.replace();
    }
  }

  document.addEventListener(
    "click",
    (event) => {
      const button = event.target?.closest?.(".bt38-qty-push-open");
      if (!button || !root.contains(button)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      void pushWarehouseGroup(button);
    },
    true
  );

  function invalidateCorruptedSnapshotOnce(attempt) {
    let aligned = false;
    try {
      aligned = window.localStorage.getItem(SNAPSHOT_MARKER) === SNAPSHOT_REVISION;
    } catch (_) {}
    if (aligned) return;

    if (typeof window.bt38InvalidateProductLinkingSnapshot !== "function") {
      if (attempt < 80) {
        window.setTimeout(() => invalidateCorruptedSnapshotOnce(attempt + 1), 25);
      }
      return;
    }

    try {
      window.localStorage.setItem(SNAPSHOT_MARKER, SNAPSHOT_REVISION);
    } catch (_) {}

    Promise.resolve(window.bt38InvalidateProductLinkingSnapshot())
      .catch((error) => {
        try { window.localStorage.removeItem(SNAPSHOT_MARKER); } catch (_) {}
        console.error("[ProductLinkingAlignment] snapshot reset failed", error);
      });
  }

  const evidenceObserver = new MutationObserver((mutations) => {
    const structuralChange = mutations.some((mutation) =>
      mutation.type === "childList" &&
      (mutation.addedNodes.length > 0 || mutation.removedNodes.length > 0) &&
      !mutation.target?.closest?.(".bt38-push-settings-evidence")
    );
    if (structuralChange) schedulePushSettingsEvidenceCorrection();
  });
  evidenceObserver.observe(root, { childList: true, subtree: true });

  installRenderAlignment(0);
  invalidateCorruptedSnapshotOnce(0);

  window.BT38 = window.BT38 || {};
  window.BT38.productLinkingRuntimeAlignment = {
    currentMembershipWins: true,
    directWarehousePush: true,
    quantityFromProductLinking: false,
    relationshipRefreshAfterPush: false,
    autoPushOperationalState: "off",
    duplicateSettingsFetchRemoved: true,
    settingsObserverSelfLoopBlocked: true,
    snapshotRevision: SNAPSHOT_REVISION
  };
}());
