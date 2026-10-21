document.addEventListener('click', event => {
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
