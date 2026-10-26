(() => {
  const grid = document.getElementById('service-panels');
  const key = 'home.array-layout.v1';
  const entries = () => [...grid.querySelectorAll('.service-panel')];
  const positiveInteger = value => Number.isInteger(value) && value > 0 && value <= 10000;
  const properties = ['--panel-start-column', '--panel-start-row'];
  let preferredView;
  try { preferredView = localStorage.getItem('home.panel-view'); } catch (_) {}
  const validView = view => ['list', 'array', 'icons'].includes(view);
  if (validView(preferredView)) {
    document.querySelector(`[data-panel-view="${preferredView}"]`)?.click();
  }

  function save() {
    const layout = {
      view: grid.dataset.view,
      panels: entries().map(panel => ({
        id: panel.dataset.panelId,
        expanded: panel.dataset.expanded === 'true',
        columns: Number(panel.dataset.resizedColumns || 1),
        rows: Number(panel.dataset.resizedRows || 3),
        position: properties.map(name => Number(panel.style.getPropertyValue(name)) || null)
      }))
    };
    try { localStorage.setItem(key, JSON.stringify(layout)); } catch (_) {
      // The dashboard remains usable when browser storage is disabled or full.
    }
  }

  try {
    let stored = localStorage.getItem(key);
    if (!stored) {
      // Preserve layouts saved before preferences moved to persistent storage.
      try { stored = sessionStorage.getItem(key); } catch (_) {}
      if (stored) localStorage.setItem(key, stored);
    }
    const saved = JSON.parse(stored);
    if (saved && Array.isArray(saved.panels)) {
      if (!validView(preferredView) && validView(saved.view)) {
        document.querySelector(`[data-panel-view="${saved.view}"]`)?.click();
      }
      const panels = new Map(entries().map(panel => [panel.dataset.panelId, panel]));
      for (const item of saved.panels) {
        if (!item || typeof item.id !== 'string') continue;
        const panel = panels.get(item.id);
        if (!panel) continue;
        panels.delete(item.id);
        grid.append(panel);
        if (positiveInteger(item.columns) && item.columns <= 3) {
          panel.dataset.resizedColumns = String(item.columns);
          panel.style.setProperty('--panel-columns', item.columns);
        }
        if (positiveInteger(item.rows)) {
          panel.dataset.resizedRows = String(item.rows);
          panel.style.setProperty('--panel-rows', item.rows);
        }
        const expanded = grid.dataset.view === 'array' && item.expanded === true;
        panel.dataset.expanded = String(expanded);
        panel.querySelector('[data-panel-expand]').setAttribute('aria-expanded', String(expanded));
        if (Array.isArray(item.position)) properties.forEach((name, index) => {
          if (positiveInteger(item.position[index]) && (index !== 0 || item.position[index] <= 3)) {
            panel.style.setProperty(name, item.position[index]);
          }
        });
      }
      // Newly introduced panels follow the saved entries in their default order.
      for (const panel of panels.values()) grid.append(panel);
    }
  } catch (_) {
    // Ignore stale/malformed saved data and unavailable storage.
  }

  new MutationObserver(records => {
    if (records.some(record => record.type === 'attributes' || record.target === grid)) save();
  }).observe(grid, {subtree: true, childList: true, attributes: true,
    attributeFilter: ['data-view', 'data-expanded', 'data-resized-columns', 'data-resized-rows', 'style']});
  window.addEventListener('pagehide', save);
})();
