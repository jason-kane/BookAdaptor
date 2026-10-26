document.querySelectorAll('[data-service-info]').forEach(group => {
  const button = group.querySelector('[data-info-refresh]');
  const message = group.querySelector('[data-info-message]');
  const content = group.querySelector('[data-info-content]');
  let active = false;
  let loading = false;

  async function refresh() {
    if (!active || loading || document.hidden || group.isConnected === false || group.closest?.('[hidden]') || globalThis.getSelection?.()?.toString()) return;
    loading = true;
    button.setAttribute('loading', '');
    button.disabled = true;
    message.textContent = 'Reading current service data…';
    try {
      const response = await fetch(group.dataset.infoUrl, {cache: 'no-store'});
      if (!response.ok) throw new Error('Unable to read service data. Try Refresh.');
      const html = await response.text();
      if (globalThis.getSelection?.()?.toString()) {
        message.textContent = 'Refresh paused while text is selected.';
        return;
      }
      content.innerHTML = html;
      content.querySelectorAll('time[datetime]').forEach(time => {
        time.textContent = new Date(time.dateTime).toLocaleString();
      });
      message.textContent = '';
    } catch (_) {
      message.textContent = 'Unable to read service data. Try Refresh. Previous data, if shown, has not been updated.';
    } finally {
      button.removeAttribute('loading');
      button.disabled = false;
      loading = false;
    }
  }

  group.addEventListener('service-info-toggle', event => {
    active = event.detail.open;
    if (active) refresh();
  });
  group.addEventListener('wa-tab-show', event => {
    if (event.target !== group) return;
    active = event.detail.name === 'info';
    if (active) refresh();
  });
  button.addEventListener('click', refresh);
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
});
