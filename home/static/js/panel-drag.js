(() => {
  const grid = document.getElementById('service-panels');
  const entries = () => [...grid.querySelectorAll('.service-panel')];
  let drag;

  function geometry() {
    const style = getComputedStyle(grid);
    const tracks = style.gridTemplateColumns.split(/\s+/).map(Number.parseFloat).filter(Number.isFinite);
    const bounds = grid.getBoundingClientRect();
    return {bounds, columns: tracks.length, column: tracks[0], row: Number.parseFloat(style.gridAutoRows),
      columnGap: Number.parseFloat(style.columnGap) || 0, rowGap: Number.parseFloat(style.rowGap) || 0};
  }
  function clearAnchors() {
    entries().forEach(panel => {
      panel.style.removeProperty('--panel-start-column');
      panel.style.removeProperty('--panel-start-row');
    });
  }
  function move(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.active && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 7) return;
    if (!drag.active) {
      drag.active = true;
      // Use the rendered border box, including any custom grid spans. Measure
      // when dragging starts so a pending resize/layout update is included.
      drag.rect = drag.panel.getBoundingClientRect();
      drag.header.dataset.suppressPanelClick = 'true';
      drag.panel.classList.remove('is-pressed', 'is-activated');
      drag.panel.classList.add('is-dragging');
      drag.ghost = document.createElement('div');
      drag.ghost.className = 'panel-drag-ghost';
      drag.ghost.setAttribute('aria-hidden', 'true');
      const width = `${drag.rect.width}px`;
      const height = `${drag.rect.height}px`;
      Object.assign(drag.ghost.style, {width, minWidth: width, maxWidth: width,
        height, minHeight: height, maxHeight: height});
      document.body.append(drag.ghost);
      document.documentElement.classList.add('panel-drag-reordering');
    }
    event.preventDefault();
    const g = geometry();
    const {bounds} = g;
    const column = Math.floor((event.clientX - bounds.left) / (g.column + g.columnGap)) + 1;
    const row = Math.floor((event.clientY - bounds.top) / (g.row + g.rowGap)) + 1;
    const widthInSlots = Math.round((drag.rect.width + g.columnGap) / (g.column + g.columnGap));
    drag.destination = null;
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top ||
        event.clientY > bounds.bottom || column + widthInSlots - 1 > g.columns) {
      Object.assign(drag.ghost.style, {left: `${event.clientX}px`, top: `${event.clientY}px`});
      drag.ghost.textContent = 'Choose a slot with room for this panel';
      return;
    }
    drag.destination = {column, row};
    Object.assign(drag.ghost.style, {
      left: `${bounds.left + (column - 1) * (g.column + g.columnGap)}px`,
      top: `${bounds.top + (row - 1) * (g.row + g.rowGap)}px`
    });
    drag.ghost.textContent = `Move ${drag.panel.getAttribute('aria-label')} to column ${column}, row ${row}`;
  }

  function finish(commit) {
    if (!drag) return;
    const current = drag;
    drag = null;
    current.ghost?.remove();
    current.panel.classList.remove('is-dragging', 'is-pressed');
    document.documentElement.classList.remove('panel-drag-reordering');
    if (current.header.hasPointerCapture(current.pointerId)) current.header.releasePointerCapture(current.pointerId);
    if (commit && current.active && current.destination) {
      // Reserve the exact destination first; CSS dense packing places the other
      // panels around it. Older anchors return to the normal packing flow.
      clearAnchors();
      current.panel.style.setProperty('--panel-start-column', current.destination.column);
      current.panel.style.setProperty('--panel-start-row', current.destination.row);
      current.header.focus({preventScroll: true});
    }
  }
  entries().forEach(panel => {
    const header = panel.querySelector('[data-panel-expand]');
    header.addEventListener('pointerdown', event => {
      if (grid.dataset.view !== 'array' || event.button !== 0) return;
      finish(false);
      delete header.dataset.suppressPanelClick;
      const rect = panel.getBoundingClientRect();
      drag = {panel, header, rect, pointerId: event.pointerId, x: event.clientX, y: event.clientY,
        active: false};
      header.setPointerCapture(event.pointerId);
    });
    header.addEventListener('pointermove', move);
    header.addEventListener('pointerup', event => { if (drag?.pointerId === event.pointerId) finish(true); });
    header.addEventListener('pointercancel', () => { if (drag?.header === header) finish(false); });
    header.addEventListener('lostpointercapture', () => { if (drag?.header === header) finish(false); });
    header.addEventListener('keydown', event => {
      if (grid.dataset.view !== 'array' || !event.altKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const order = entries();
      const index = order.indexOf(panel);
      const earlier = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
      const target = order[index + (earlier ? -1 : 1)];
      if (!target) return;
      clearAnchors();
      if (earlier) target.before(panel); else target.after(panel);
      header.focus({preventScroll: true});
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drag?.active) {
      event.preventDefault(); event.stopPropagation(); finish(false);
    }
  }, true);
  window.addEventListener('blur', () => finish(false));
})();
