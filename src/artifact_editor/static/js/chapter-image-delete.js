(() => {
    const dialog = document.getElementById('chapter-image-delete-dialog');
    if (!dialog) return;
    const checkbox = document.getElementById('chapter-image-delete-skip');
    const message = document.getElementById('chapter-image-delete-message');
    const key = 'chapter-image-delete-skip-confirmation';
    let skip = false;
    let pending = null;
    try { skip = sessionStorage.getItem(key) === 'true'; } catch (_) { /* Keep the in-page session usable. */ }

    document.addEventListener('htmx:confirm', event => {
        const button = event.detail.elt;
        if (!button?.hasAttribute('data-confirm-image-delete')) return;
        event.preventDefault();
        if (skip) {
            event.detail.issueRequest(true);
            return;
        }
        pending = event.detail.issueRequest;
        checkbox.checked = false;
        message.textContent = `Delete Image ${button.getAttribute('data-confirm-image-delete')} from the timeline?`;
        dialog.open = true;
    });
    document.getElementById('chapter-image-delete-confirm').addEventListener('click', () => {
        if (!pending) return;
        const request = pending;
        pending = null;
        if (checkbox.checked) {
            skip = true;
            try { sessionStorage.setItem(key, 'true'); } catch (_) { /* Retain the in-page choice. */ }
        }
        dialog.open = false;
        request(true);
    });
    document.getElementById('chapter-image-delete-cancel').addEventListener('click', () => {
        pending = null;
        dialog.open = false;
    });
    dialog.addEventListener('wa-hide', () => { pending = null; });
})();
