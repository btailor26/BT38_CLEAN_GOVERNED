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

  getFilters(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.filterFormSelector) return {};

    const form = document.querySelector(page.filterFormSelector);
    if (!form) return {};

    const filters = {};
    form.querySelectorAll("input[name], select[name]").forEach(field => {
      const name = field.name;
      const value = (field.value || "").trim().toLowerCase();
      if (!name) return;
      if (!value || value === "all") return;
      if (field.type === "hidden") return;
      filters[name] = value;
    });

    return filters;
  },

  rowMatchesFilters(row, filters) {
    const haystack = `${row.text} ${Object.values(row.dataset).join(" ")}`.toLowerCase();

    for (const [name, value] of Object.entries(filters)) {
      if (name === "q" || name === "search") {
        if (!haystack.includes(value)) return false;
        continue;
      }

      const exactDatasetValue = (row.dataset[name] || "").toLowerCase();
      const alternateDatasetValue = (row.dataset[name.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] || "").toLowerCase();

      if (exactDatasetValue || alternateDatasetValue) {
        if (!exactDatasetValue.includes(value) && !alternateDatasetValue.includes(value)) return false;
        continue;
      }

      if (!haystack.includes(value)) return false;
    }

    return true;
  },

  localFilter(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.filterFormSelector) return false;

    // Rebuild row cache before every filter so server-rendered or replaced rows are included.
    if (page.tableSelector) {
      window.BT38.PageController.initTableCache(pageName);
    }

    if (!page.ready) return false;

    const filters = window.BT38.PageController.getFilters(pageName);
    let visible = 0;

    page.rows.forEach(row => {
      const match = window.BT38.PageController.rowMatchesFilters(row, filters);
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

  autoRegisterFromDom() {
    const root = document.querySelector("[data-bt38-page]");
    if (!root) return false;

    const pageName = root.dataset.bt38Page;
    if (!pageName) return false;

    const filterFormSelector = root.dataset.bt38FilterForm || null;
    const tableSelector = root.dataset.bt38Table || null;
    const rowSelector = root.dataset.bt38Row || "tbody tr";
    const submitName = root.dataset.bt38SubmitName;

    if (submitName) {
      window[submitName] = function(event) {
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        if (window.BT38 && window.BT38.PageController) {
          window.BT38.PageController.localFilter(pageName);
        }
        return false;
      };
    }

    window.BT38.PageController.register(pageName, {
      filterFormSelector,
      tableSelector,
      rowSelector
    });

    if (tableSelector) {
      window.BT38.PageController.initTableCache(pageName);
    }

    if (filterFormSelector) {
      window.BT38.PageController.wireLocalForm(pageName);
    }

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


document.addEventListener("DOMContentLoaded", function() {
  if (window.BT38 && window.BT38.PageController) {
    window.BT38.PageController.autoRegisterFromDom();
  }
});

window.bt38SetFilter = window.bt38SetFilter || function(name, value) {
  const root = document.querySelector("[data-bt38-page]");
  if (!root || !window.BT38 || !window.BT38.PageController) return false;

  const pageName = root.dataset.bt38Page;
  const formSelector = root.dataset.bt38FilterForm;
  const form = formSelector ? document.querySelector(formSelector) : null;

  if (form && name) {
    const field = form.querySelector(`[name="${name}"]`);
    if (field) field.value = value;
  }

  window.BT38.PageController.localFilter(pageName);
  return false;
};

window.BT38.sessionFetch = window.BT38.sessionFetch || async function(key, url, options = {}) {
  window.BT38.state = window.BT38.state || { cache: {}, session: {} };
  window.BT38.state.cache = window.BT38.state.cache || {};
  window.BT38.state.cache.fetch = window.BT38.state.cache.fetch || {};

  const force = options.force === true;
  const ttlMs = options.ttlMs || 60000;
  const now = Date.now();
  const cached = window.BT38.state.cache.fetch[key];

  if (!force && cached && (now - cached.at) < ttlMs) {
    return cached.data;
  }

  const fetchOptions = Object.assign({}, options);
  delete fetchOptions.force;
  delete fetchOptions.ttlMs;

  const response = await fetch(url, fetchOptions);
  const data = await response.json();

  window.BT38.state.cache.fetch[key] = {
    at: now,
    data
  };

  return data;
};
