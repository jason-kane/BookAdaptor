(() => {
  const labels = document.querySelectorAll('[data-service-version]');
  const launchers = document.querySelectorAll('[data-member-start]');
  const fields = Object.fromEntries(['state', 'image', 'ports'].map(field =>
    [field, document.querySelectorAll(`[data-service-${field}]`)]));
  const table = document.querySelector('.service-table');
  function updateLauncher(button, running) {
    button.dataset.running = String(running);
    const action = running ? 'Stop' : 'Start';
    button.querySelector('.member-launch-play').textContent = running ? '■' : '▶';
    button.querySelector('.member-launch-label').textContent = action;
    button.setAttribute('aria-label', `${action} ${button.dataset.memberName}`);
    button.title = `${action} ${button.dataset.memberName}`;
  }
  let loading = false;
  async function refresh() {
    if (loading || document.hidden || globalThis.getSelection?.()?.toString()) return;
    loading = true;
    try {
      const response = await fetch('/programs/panels/versions', {cache: 'no-store'});
      if (!response.ok) throw new Error('Versions unavailable');
      const panels = await response.json();
      if (globalThis.getSelection?.()?.toString()) return;
      const services = new Map(Object.values(panels).flat().map(item => [item.service, item]));
      launchers.forEach(button => {
        updateLauncher(button, services.get(button.dataset.memberStart)?.state === 'running');
      });
      labels.forEach(label => {
        const service = services.get(label.dataset.serviceVersion);
        label.textContent = service?.version || 'Version unavailable';
        label.title = service ? `${service.service}: ${service.image || service.version}` : '';
      });
      for (const field of ['state', 'image', 'ports']) {
        fields[field].forEach(cell => {
          const service = services.get(cell.dataset[`service${field[0].toUpperCase()}${field.slice(1)}`]);
          cell.textContent = service?.[field] || '—';
        });
      }
      table?.dispatchEvent(new Event('service-table-data'));
    } catch (_) {
      launchers.forEach(button => { updateLauncher(button, false); });
      if (globalThis.getSelection?.()?.toString()) return;
      labels.forEach(label => {
        if (label.textContent.startsWith('Checking')) label.textContent = 'Version unavailable';
      });
      Object.values(fields).forEach(cells => cells.forEach(cell => {
        if (cell.textContent.startsWith('Checking')) cell.textContent = 'Unavailable';
      }));
      table?.dispatchEvent(new Event('service-table-data'));
    } finally {
      loading = false;
    }
  }
  document.addEventListener('service-members-started', refresh);
  refresh();
  setInterval(refresh, 60000);
})();
