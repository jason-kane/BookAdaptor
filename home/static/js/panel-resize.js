(() => {
  const grid = document.getElementById('service-panels');
  const panels = [...grid.querySelectorAll('.service-panel')];
  let drag;

  function metrics() {
    const style = getComputedStyle(grid);
    const tracks = style.gridTemplateColumns.split(/\s+/).map(Number.parseFloat).filter(Number.isFinite);
    return {columns: tracks.length, column: tracks[0], row: Number.parseFloat(style.gridAutoRows),
      columnGap: Number.parseFloat(style.columnGap) || 0, rowGap: Number.parseFloat(style.rowGap) || 0};
  }
  function size(panel) {
    return {columns: Number(panel.dataset.resizedColumns || 1),
      rows: panel.dataset.expanded === 'true' ? Number(panel.dataset.resizedRows || 3) : 1};
  }
  function apply(panel, columns, rows) {
    const geometry = metrics();
    columns = Math.max(1, Math.min(geometry.columns, columns));
    rows = Math.max(1, rows);
    panel.dataset.resizedColumns = String(columns);
    panel.style.setProperty('--panel-columns', columns);
    const anchoredColumn = Number(panel.style.getPropertyValue('--panel-start-column'));
    if (anchoredColumn) panel.style.setProperty('--panel-start-column', Math.min(anchoredColumn, geometry.columns - columns + 1));
    if (rows > 1) {
      panel.dataset.resizedRows = String(rows);
      panel.style.setProperty('--panel-rows', rows);
    } else {
      delete panel.dataset.resizedRows;
      panel.style.removeProperty('--panel-rows');
    }
    panel.dataset.expanded = String(rows > 1);
    const toggle = panel.querySelector('[data-panel-expand]');
    toggle.setAttribute('aria-expanded', String(rows > 1));
    if (rows === 1) panel.querySelector('[data-service-info]').active = 'open';
  }
  function preview(event) {
    const x = (event.clientX - drag.x) * drag.signX;
    const y = (event.clientY - drag.y) * drag.signY;
    const g = drag.geometry;
    drag.columns = Math.max(1, Math.min(g.columns, Math.round((drag.rect.width + x + g.columnGap) / (g.column + g.columnGap))));
    drag.rows = Math.max(1, Math.round((drag.rect.height + y + g.rowGap) / (g.row + g.rowGap)));
    const width = drag.columns * g.column + (drag.columns - 1) * g.columnGap;
    const height = drag.rows * g.row + (drag.rows - 1) * g.rowGap;
    Object.assign(drag.preview.style, {width: `${width}px`, height: `${height}px`,
      left: `${drag.signX < 0 ? drag.rect.right - width : drag.rect.left}px`,
      top: `${drag.signY < 0 ? drag.rect.bottom - height : drag.rect.top}px`});
    drag.preview.textContent = `${drag.columns} × ${drag.rows} grid slots`;
  }
  function finish(commit) {
    if (!drag) return;
    const current = drag;
    drag = null;
    if (commit) apply(current.panel, current.columns, current.rows);
    current.preview.remove();
    current.panel.classList.remove('is-resizing');
    document.documentElement.classList.remove('panel-drag-resizing');
    document.documentElement.style.removeProperty('--panel-resize-cursor');
    if (current.handle.hasPointerCapture(current.pointerId)) current.handle.releasePointerCapture(current.pointerId);
  }
  grid.querySelectorAll('[data-resize-corner]').forEach(handle => {
    const panel = handle.closest('.service-panel');
    const direction = handle.dataset.resizeCorner;
    const signX = direction.includes('w') ? -1 : direction.includes('e') ? 1 : 0;
    const signY = direction.includes('n') ? -1 : direction.includes('s') ? 1 : 0;
    handle.addEventListener('pointerdown', event => {
      if (grid.dataset.view !== 'array' || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      finish(false);
      const geometry = metrics();
      if (!geometry.columns || !geometry.row || !geometry.column) return;
      panel.classList.remove('is-pressed');
      panel.classList.add('is-resizing');
      const ghost = document.createElement('div');
      ghost.className = 'panel-resize-preview';
      ghost.setAttribute('aria-hidden', 'true');
      document.body.append(ghost);
      drag = {panel, handle, signX, signY, geometry, preview: ghost, pointerId: event.pointerId,
        x: event.clientX, y: event.clientY, rect: panel.getBoundingClientRect()};
      document.documentElement.classList.add('panel-drag-resizing');
      const cursor = !signX ? 'ns-resize' : !signY ? 'ew-resize' : signX === signY ? 'nwse-resize' : 'nesw-resize';
      document.documentElement.style.setProperty('--panel-resize-cursor', cursor);
      handle.setPointerCapture(event.pointerId);
      preview(event);
    });
    handle.addEventListener('pointermove', event => {
      if (drag?.handle === handle && drag.pointerId === event.pointerId) preview(event);
    });
    handle.addEventListener('pointerup', event => {
      if (drag?.handle === handle && drag.pointerId === event.pointerId) finish(true);
    });
    handle.addEventListener('pointercancel', () => { if (drag?.handle === handle) finish(false); });
    handle.addEventListener('lostpointercapture', () => { if (drag?.handle === handle) finish(false); });
    handle.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); });
    handle.addEventListener('keydown', event => {
      if (grid.dataset.view !== 'array') return;
      if (event.key === 'Escape' && drag) {
        event.preventDefault(); event.stopPropagation(); finish(false); return;
      }
      const changes = {ArrowLeft: [-signX, 0], ArrowRight: [signX, 0], ArrowUp: [0, -signY], ArrowDown: [0, signY]};
      const change = changes[event.key];
      if (!change || (!change[0] && !change[1])) return;
      event.preventDefault(); event.stopPropagation();
      const current = size(panel);
      apply(panel, current.columns + change[0], current.rows + change[1]);
    });
  });
  window.addEventListener('blur', () => finish(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drag) { event.preventDefault(); event.stopPropagation(); finish(false); }
  }, true);
  // Saved widths must not create implicit columns when the viewport gets smaller.
  const clampWidths = () => {
    if (grid.dataset.view !== 'array') return;
    const columns = metrics().columns;
    if (!columns) return;
    panels.forEach(panel => {
      const width = Math.min(columns, Number(panel.dataset.resizedColumns || 1));
      panel.style.setProperty('--panel-columns', width);
      const anchoredColumn = Number(panel.style.getPropertyValue('--panel-start-column'));
      if (anchoredColumn) panel.style.setProperty('--panel-start-column', Math.min(anchoredColumn, columns - width + 1));
    });
  };
  if (window.ResizeObserver) new ResizeObserver(clampWidths).observe(grid);
})();
