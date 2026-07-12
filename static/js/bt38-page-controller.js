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
      filteredRows: [],
      currentPage: 1,
      perPage: 15,
      ready: false,
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
      const alternateDatasetValue = (
        row.dataset[name.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] || ""
      ).toLowerCase();

      if (exactDatasetValue || alternateDatasetValue) {
        if (
          !exactDatasetValue.includes(value) &&
          !alternateDatasetValue.includes(value)
        ) return false;
        continue;
      }

      if (!haystack.includes(value)) return false;
    }

    return true;
  },

  getPerPage(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page) return 15;

    const select = document.getElementById("bt38ResultsPerPageSelect");
    const value = Number.parseInt(select ? select.value : page.perPage, 10);
    return [15, 25, 50, 100].includes(value) ? value : 15;
  },

  renderPage(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.ready) return false;

    page.perPage = window.BT38.PageController.getPerPage(pageName);
    const total = page.filteredRows.length;
    const totalPages = Math.max(1, Math.ceil(total / page.perPage));
    page.currentPage = Math.min(Math.max(page.currentPage, 1), totalPages);

    const start = (page.currentPage - 1) * page.perPage;
    const end = start + page.perPage;
    const visibleRows = new Set(page.filteredRows.slice(start, end));

    page.rows.forEach(row => {
      row.el.hidden = !visibleRows.has(row);
    });

    const count = document.querySelector("[data-bt38-count], .bt38-table-count");
    if (count) {
      count.textContent = `${total} matching · showing ${total ? start + 1 : 0}-${Math.min(end, total)}`;
    }

    const status = document.querySelector(".bt38-page-status");
    if (status) {
      status.textContent = `Page ${page.currentPage} of ${totalPages} · ${total} total`;
    }

    const prev = document.querySelector(".bt38-page-nav .bt38-page-link:first-child");
    const next = document.querySelector(".bt38-page-nav .bt38-page-link:last-child");

    if (prev) {
      prev.classList.toggle("disabled", page.currentPage <= 1);
      prev.setAttribute("aria-disabled", page.currentPage <= 1 ? "true" : "false");
    }

    if (next) {
      next.classList.toggle("disabled", page.currentPage >= totalPages);
      next.setAttribute("aria-disabled", page.currentPage >= totalPages ? "true" : "false");
    }

    return true;
  },

  localFilter(pageName, options = {}) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.filterFormSelector || !page.ready) return false;

    const filters = window.BT38.PageController.getFilters(pageName);
    page.filteredRows = page.rows.filter(row =>
      window.BT38.PageController.rowMatchesFilters(row, filters)
    );

    if (options.keepPage !== true) {
      page.currentPage = 1;
    }

    return window.BT38.PageController.renderPage(pageName);
  },

  wireLocalForm(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page || !page.filterFormSelector) return false;

    const form = document.querySelector(page.filterFormSelector);
    if (!form) return false;

    form.addEventListener("submit", function(event) {
      event.preventDefault();
      event.stopPropagation();
      window.BT38.PageController.localFilter(pageName);
      return false;
    });

    form.querySelectorAll("input, select").forEach(field => {
      field.addEventListener("input", () => {
        window.BT38.PageController.localFilter(pageName);
      });
      field.addEventListener("change", event => {
        event.preventDefault();
        event.stopPropagation();
        window.BT38.PageController.localFilter(pageName);
      });
    });

    return true;
  },

  wireLocalPagination(pageName) {
    const page = window.BT38.pages[pageName];
    if (!page) return false;

    const perPageSelect = document.getElementById("bt38ResultsPerPageSelect");
    if (perPageSelect) {
      page.perPage = window.BT38.PageController.getPerPage(pageName);
      perPageSelect.addEventListener("change", event => {
        event.preventDefault();
        event.stopPropagation();
        page.currentPage = 1;
        window.BT38.PageController.renderPage(pageName);
      });
    }

    const nav = document.querySelector(".bt38-page-nav");
    if (nav) {
      const links = nav.querySelectorAll(".bt38-page-link");
      const previous = links[0];
      const next = links[links.length - 1];

      if (previous) {
        previous.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          if (page.currentPage > 1) {
            page.currentPage -= 1;
            window.BT38.PageController.renderPage(pageName);
          }
        });
      }

      if (next && next !== previous) {
        next.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          const totalPages = Math.max(1, Math.ceil(page.filteredRows.length / page.perPage));
          if (page.currentPage < totalPages) {
            page.currentPage += 1;
            window.BT38.PageController.renderPage(pageName);
          }
        });
      }
    }

    const resultsForm = document.querySelector("#bt38ResultsPerPageBottom form");
    if (resultsForm) {
      resultsForm.addEventListener("submit", event => {
        event.preventDefault();
        event.stopPropagation();
        return false;
      });
    }

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

    window.BT38.PageController.wireLocalPagination(pageName);

    if (filterFormSelector) {
      window.BT38.PageController.localFilter(pageName);
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

function bt38BootPageController() {
  if (window.BT38 && window.BT38.PageController) {
    window.BT38.PageController.autoRegisterFromDom();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bt38BootPageController, { once: true });
} else {
  bt38BootPageController();
}

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
