document.querySelectorAll('[data-service-members]').forEach(browser => {
  browser.querySelectorAll('[data-member-start]').forEach(button => {
    button.addEventListener('click', async () => {
      const service = button.dataset.memberStart;
      if (button.disabled) return;
      const stopping = button.dataset.running === 'true';
      const detail = [...browser.querySelectorAll('[data-member-detail]')].find(item => item.dataset.memberDetail === service);
      const message = detail.querySelector('[data-member-message]');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      message.textContent = stopping ? 'Stopping service…' : 'Starting service…';
      try {
        const response = await fetch(stopping ? button.dataset.stopUrl : button.dataset.startUrl, {method: 'POST'});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Unable to ${stopping ? 'stop' : 'start'} service.`);
        message.textContent = stopping ? 'Stopped.' : 'Started. The service may need a moment to become ready.';
        document.dispatchEvent(new Event('service-members-started'));
      } catch (error) {
        message.textContent = error.message || `Unable to ${stopping ? 'stop' : 'start'} service. Please try again.`;
      } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
    });
  });
});
