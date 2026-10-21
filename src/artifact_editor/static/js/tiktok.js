(() => {
    if (window.tiktokPublishBound) return;
    window.tiktokPublishBound = true;
    async function send(form, button, check) {
        if (form.dataset.busy) return;
        form.dataset.busy = 'true';
        button.disabled = true;
        button.loading = true;
        const status = form.querySelector('.tiktok-status');
        status.textContent = check ? 'Checking TikTok processing…' : 'Uploading video to TikTok…';
        try {
            const response = await fetch(check ? form.dataset.statusUrl : form.action, {
                method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest'}
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error);
            status.textContent = check ? `TikTok processing status: ${result.status}`
                : 'Video transferred. TikTok may still be processing it. Open your TikTok inbox notification to review and publish.';
        } catch (error) {
            status.textContent = error instanceof TypeError || error instanceof SyntaxError
                ? 'Could not confirm the result. Check your TikTok inbox before retrying.' : error.message;
            button.disabled = false;
        } finally {
            button.loading = false;
            if (check) button.disabled = false;
            delete form.dataset.busy;
        }
    }
    document.addEventListener('submit', event => {
        if (event.target.id !== 'tiktok-upload-form') return;
        event.preventDefault();
        send(event.target, event.target.querySelector('[type="submit"]'), false);
    });
    document.addEventListener('click', event => {
        const button = event.target.closest('.tiktok-check');
        if (button) send(button.closest('form'), button, true);
    });
})();
