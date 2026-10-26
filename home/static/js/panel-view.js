const panels = document.getElementById('service-panels');
const viewButtons = document.querySelectorAll('[data-panel-view]');
const servicePanels = panels.querySelectorAll('.service-panel');
const usesButtonRows = () => panels.dataset.view === 'array';

function collapsePanel(panel) {
  panel.dataset.expanded = 'false';
  panel.querySelector('[data-service-info]').active = 'open';
  const button = panel.querySelector('[data-panel-expand]');
  button.setAttribute('aria-expanded', 'false');
}

function togglePanel(panel) {
  if (!usesButtonRows()) return;
  panel.classList.remove('is-activated');
  void panel.offsetWidth; // Restart the CSS flash for repeated activations.
  panel.classList.add('is-activated');
  const wasExpanded = panel.dataset.expanded === 'true';
  servicePanels.forEach(collapsePanel);
  if (wasExpanded) return;
  panel.dataset.expanded = 'true';
  const button = panel.querySelector('[data-panel-expand]');
  button.setAttribute('aria-expanded', 'true');
}

servicePanels.forEach(panel => {
  panel.addEventListener('animationend', event => {
    if (event.animationName === 'brass-activation') panel.classList.remove('is-activated');
  });
  const release = () => panel.classList.remove('is-pressed');
  panel.addEventListener('pointerdown', event => {
    if (!usesButtonRows() || event.button !== 0) return;
    const path = event.composedPath();
    const expanding = path.some(element => element.matches?.('[data-panel-expand]'));
    if (expanding) panel.classList.add('is-pressed');
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(name => panel.addEventListener(name, release));
  const expandButton = panel.querySelector('[data-panel-expand]');
  expandButton.addEventListener('keydown', event => {
    if (usesButtonRows() && (event.key === ' ' || event.key === 'Enter')) {
      event.preventDefault?.();
      if (event.repeat) return;
      panel.classList.add('is-pressed');
      togglePanel(panel);
    }
  });
  expandButton.addEventListener('keyup', release);
  expandButton.addEventListener('blur', release);
  expandButton.addEventListener('click', event => {
    if (expandButton.dataset.suppressPanelClick === 'true') {
      delete expandButton.dataset.suppressPanelClick;
      event?.preventDefault();
      return;
    }
    togglePanel(panel);
  });
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape' && panel.dataset.expanded === 'true') {
      collapsePanel(panel);
      panel.querySelector('[data-panel-expand]').focus();
    }
  });
});

viewButtons.forEach(button => {
  button.addEventListener('click', () => {
    servicePanels.forEach(collapsePanel);
    panels.dataset.view = button.dataset.panelView;
    try { localStorage.setItem('home.panel-view', panels.dataset.view); } catch (_) {
      // View switching still works if browser storage is unavailable.
    }
    const table = document.getElementById('service-table-view');
    if (table) table.hidden = panels.dataset.view !== 'list';
    viewButtons.forEach(option => {
      const selected = option === button;
      option.setAttribute('aria-pressed', String(selected));
      option.setAttribute('appearance', selected ? 'filled' : 'outlined');
    });
  });
});
