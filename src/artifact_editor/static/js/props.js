(() => {
    if (window.propImageHandlersInstalled) return;
    window.propImageHandlersInstalled = true;
    document.addEventListener('htmx:wsConfigSend', event => {
        if (event.detail.parameters.action !== 'prop_image') return;
        const container = event.target?.closest?.('[data-prop-image]');
        const field = container?.closest('form')?.querySelector('[name="description"]');
        if (typeof field?.value !== 'string' || !field.value.trim()) {
            const status = container?.querySelector('[role="status"]');
            if (status) status.textContent = 'Enter a prop description before generating an image.';
            event.preventDefault();
            field?.focus();
            return;
        }
        event.detail.parameters.description = field.value;
    });
    document.addEventListener('htmx:wsAfterSend', event => {
        const container = event.target?.closest?.('[data-prop-image]');
        if (!container) return;
        container.querySelector('wa-button').disabled = true;
        const status = container.querySelector('[role="status"]');
        if (status) {
            delete status.dataset.videoState;
            delete status.dataset.imageRefreshed;
            status.textContent = 'Waiting for image service to accept the request…';
        }
    });
    document.addEventListener('htmx:afterRequest', event => {
        const field = event.detail?.elt;
        if (!field?.matches?.('wa-input[name="name"], wa-textarea[name="description"]')) return;
        const form = field.closest('[data-prop-autosave]');
        const status = form?.querySelector('[data-save-status]');
        if (!status) return;
        status.textContent = event.detail.successful ? 'Saved' : 'Could not save. Check the prop name and try again.';
        if (!event.detail.successful) return;
        const panel = form.closest('wa-tab-panel');
        const group = panel?.closest('wa-tab-group');
        const tab = Array.from(group?.querySelectorAll('wa-tab') || []).find(tab => tab.getAttribute('panel') === panel.getAttribute('name'));
        if (tab) tab.textContent = form.querySelector('[name="name"]').value;
    });
    document.addEventListener('htmx:wsError', event => {
        const container = event.target?.closest?.('[data-prop-image]');
        const status = container?.querySelector('[role="status"]');
        if (status) status.textContent = 'Image service connection failed. Refresh the page and try again.';
        const button = container?.querySelector('wa-button');
        if (button) button.disabled = false;
    });
    document.addEventListener('htmx:wsAfterMessage', () => {
        document.querySelectorAll('[data-prop-image]').forEach(container => {
            const status = document.getElementById(`video-progress-${container.dataset.propImage}`);
            const state = status?.dataset.videoState;
            if (state !== 'complete' && state !== 'failed') return;
            container.querySelector('wa-button').disabled = false;
            if (state !== 'complete' || status.dataset.imageRefreshed) return;
            const image = document.createElement('img');
            image.src = `${container.dataset.imageSrc}?v=${Date.now()}`;
            image.alt = 'Prop reference image';
            image.style.cssText = 'max-width:100%;max-height:300px;object-fit:contain;background:white';
            container.querySelector('[data-prop-preview]').replaceChildren(image);
            status.dataset.imageRefreshed = 'true';
        });
    });
})();
