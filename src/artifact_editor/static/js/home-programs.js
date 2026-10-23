document.querySelectorAll('[data-program]').forEach(card => {
  const controls = card.querySelector('[data-program-controls]');
  const button = card.querySelector('[data-program-stop]');
  const message = card.querySelector('[data-program-message]');
  let checking = false;
  let stopping = false;
  let version = 0;

  async function refresh() {
    if (checking || stopping || document.hidden) return;
    checking = true;
    const requestVersion = version;
    try {
      const response = await fetch(card.dataset.runningUrl, {cache: 'no-store'});
      if (response.ok) {
        const state = await response.json();
        if (!stopping && requestVersion === version) controls.hidden = !state.running;
      }
    } catch (_) {
      // A temporary status failure should not remove an available Stop button.
    } finally {
      checking = false;
    }
  }

  button.addEventListener('click', async () => {
    if (stopping) return;
    stopping = true;
    version += 1;
    button.setAttribute('disabled', '');
    button.setAttribute('loading', '');
    message.textContent = 'Stopping…';
    try {
      const response = await fetch(card.dataset.stopUrl, {method: 'POST'});
      if (!response.ok) throw new Error('Unable to stop. Please try again.');
      controls.hidden = true;
      message.textContent = '';
    } catch (_) {
      message.textContent = 'Unable to stop. Please try again.';
    } finally {
      button.removeAttribute('disabled');
      button.removeAttribute('loading');
      stopping = false;
    }
  });

  refresh();
  setInterval(refresh, 5000);
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', refresh);
});
