document.addEventListener('click', event => {
    const actionsButton = event.target.closest('.chapter-actions-trigger');
    if (actionsButton) {
        const drawer = document.getElementById(actionsButton.dataset.drawer);
        if (drawer) {
            const column = actionsButton.closest('.chapter-column');
            const bounds = column.getBoundingClientRect();
            drawer.style.setProperty('--chapter-drawer-left', `${bounds.left}px`);
            drawer.style.setProperty('--chapter-drawer-width', `${bounds.width}px`);
            const triggerBounds = actionsButton.getBoundingClientRect();
            drawer.style.setProperty('--chapter-trigger-bottom', `${window.innerHeight - triggerBounds.bottom}px`);
            drawer.style.left = `${bounds.left}px`;
            drawer.style.width = `${bounds.width}px`;
            drawer.open = true;
            drawer.setAttribute('open', '');
            drawer.addEventListener('wa-after-hide', () => drawer.removeAttribute('open'), { once: true });
        }
        return;
    }

    const button = event.target.closest('.chapter-column-toggle');
    if (!button) return;
    const column = button.closest('.chapter-column');
    const content = document.getElementById(button.getAttribute('aria-controls'));
    const collapsed = column.classList.toggle('is-collapsed');
    content.hidden = collapsed;
    button.setAttribute('aria-expanded', String(!collapsed));
    const label = button.dataset.columnLabel;
    button.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${label} column`);
    button.setAttribute('title', button.getAttribute('aria-label'));
    let icon = button.querySelector('wa-icon');
    if (!icon) {
        icon = document.createElement('wa-icon');
        icon.setAttribute('aria-hidden', 'true');
    }
    icon.setAttribute('name', collapsed ? 'arrow-right' : 'arrow-left');
    button.replaceChildren(icon);
    if (!collapsed) htmx.trigger(content, 'column-expanded');
});

window.openChapterActions = button => {
    const drawer = document.getElementById(button.dataset.drawer);
    if (!drawer) return;
    const bounds = button.closest('.chapter-column').getBoundingClientRect();
    drawer.style.setProperty('--chapter-drawer-left', `${bounds.left}px`);
    drawer.style.setProperty('--chapter-drawer-width', `${bounds.width}px`);
    const triggerBounds = button.getBoundingClientRect();
    drawer.style.setProperty('--chapter-trigger-bottom', `${window.innerHeight - triggerBounds.bottom}px`);
    drawer.style.left = `${bounds.left}px`;
    drawer.style.width = `${bounds.width}px`;
    drawer.open = true;
    drawer.setAttribute('open', '');
    drawer.addEventListener('wa-after-hide', () => drawer.removeAttribute('open'), { once: true });
};

// Keep the appearance controls responsive while their queued bulk job runs.
document.addEventListener('htmx:wsAfterSend', event => {
    const group = event.detail.elt?.closest('[data-chapter-action="chapter_character_appearances"]');
    if (!group) return;
    group.querySelector('wa-button').disabled = true;
    const status = document.getElementById(`video-progress-${group.dataset.chapterAssets}`);
    if (status) {
        delete status.dataset.videoState;
        delete status.dataset.appearanceRefreshed;
        status.textContent = 'Queued for character appearance processing…';
    }
});

document.addEventListener('htmx:wsAfterMessage', () => {
    document.querySelectorAll('[data-chapter-action="chapter_character_appearances"]').forEach(group => {
        const status = group.querySelector('[data-video-state]');
        if (!status || !['complete', 'failed', 'cancelled'].includes(status.dataset.videoState)) return;
        group.querySelector('wa-button').disabled = false;
        if (status.dataset.appearanceRefreshed) return;
        status.dataset.appearanceRefreshed = 'true';
        const panel = document.querySelector('#chapter-timeline-content > wa-tab-group > wa-tab-panel[name="characters"]');
        if (panel) htmx.trigger(panel, 'load-column');
    });
});
