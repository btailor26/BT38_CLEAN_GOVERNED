// BT38 Unified Page Controller
// One browser-session path for all operational pages.
// No business logic belongs here.

window.BT38 = window.BT38 || {};
window.BT38.pages = window.BT38.pages || {};

window.BT38.PageController = {
  register(pageName, config = {}) {
    window.BT38.pages[pageName] = {
      name: pageName,
      cacheKey: config.cacheKey || pageName,
      rootSelector: config.rootSelector || "body",
      filterFormSelector: config.filterFormSelector || null,
      tableSelector: config.tableSelector || null,
      rowSelector: config.rowSelector || "tbody tr",
      allowInitialFetch: config.allowInitialFetch === true,
      dirty: false,
      rows: [],
      config
    };

    if (window.BT38.initPage) {
      window.BT38.initPage(pageName);
    }

    return window.BT38.pages[pageName];
  },

  initTableCache(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.tableSelector) return false;

    const table = document.querySelector(page.tableSelector);
    if (!table) return false;

    page.rows = Array.from(table.querySelectorAll(page.rowSelector)).map(row => ({
      el: row,
      text: (row.textContent || "").toLowerCase(),
      dataset: Object.assign({}, row.dataset)
    }));

    page.ready = true;
    return true;
  },

  localFilter(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.ready || !page.filterFormSelector) return false;

    const form = document.querySelector(page.filterFormSelector);
    if (!form) return false;

    const q = ((form.querySelector('[name="q"]') || {}).value || "").trim().toLowerCase();
    let visible = 0;

    page.rows.forEach(row => {
      const match = !q || row.text.includes(q) || Object.values(row.dataset).join(" ").toLowerCase().includes(q);
      row.el.hidden = !match;
      if (match) visible += 1;
    });

    const count = document.querySelector("[data-bt38-count], .bt38-table-count");
    if (count) count.textContent = `${visible} visible in browser session`;

    return true;
  },

  wireLocalForm(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.filterFormSelector) return false;

    const form = document.querySelector(page.filterFormSelector);
    if (!form) return false;

    form.addEventListener("submit", function(event) {
      if (window.BT38.PageController.localFilter(pageName)) {
        event.preventDefault();
        event.stopPropagation();
      }
    });

    form.querySelectorAll("input, select").forEach(field => {
      field.addEventListener("input", () => window.BT38.PageController.localFilter(pageName));
      field.addEventListener("change", event => {
        if (window.BT38.PageController.localFilter(pageName)) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    });

    return true;
  },

  markDirty(pageName, reason = "unknown") {
    const page = window.BT38.pages[pageName];
    if (!page) return;
    page.dirty = true;
    page.dirtyReason = reason;
    page.lastDirtyAt = Date.now();
  }
};
