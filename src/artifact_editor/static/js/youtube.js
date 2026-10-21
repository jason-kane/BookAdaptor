(() => {
    if (window.youtubeUploadBound) return;
    window.youtubeUploadBound = true;
    document.addEventListener('wa-change', (event) => {
        const control = event.target;
        if (!['title_category', 'title_format'].includes(control.name)) return;
        const form = control.closest('#youtube-upload-form');
        if (!form) return;
        const title = form.querySelector('[name="title"]');
        const tags = ['booktok', form.querySelector('[name="title_category"]').value,
            form.dataset.authorHashtag, form.querySelector('[name="title_format"]').value];
        const generated = `${form.dataset.titleTitle} by ${form.dataset.titleAuthor} ` +
            tags.filter(Boolean).map(tag => `#${tag}`).join(' ');
        if (title.value === form.dataset.generatedTitle) title.value = generated;
        form.dataset.generatedTitle = generated;
        const uploadTags = form.querySelector('[name="tags"]');
        const generatedTags = 'book,literature,story,audiobook' +
            (form.querySelector('[name="title_format"]').value === 'poem' ? ',poem' : '');
        if (uploadTags.value === uploadTags.dataset.generatedTags) uploadTags.value = generatedTags;
        uploadTags.dataset.generatedTags = generatedTags;
    });
    document.addEventListener('submit', async (event) => {
        const form = event.target;
        if (form.id !== 'youtube-upload-form') return;
        event.preventDefault();
        if (form.dataset.uploading === 'true') return;
        const button = form.querySelector('#youtube-upload');
        const status = form.querySelector('#youtube-upload-status');
        const values = new FormData(form);
        form.dataset.uploading = 'true';
        button.disabled = true;
        button.loading = true;
        status.textContent = 'Uploading video to YouTube… Keep this tab open until it finishes.';
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: values,
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'YouTube upload failed.');
            const link = document.createElement('a');
            link.href = `https://www.youtube.com/watch?v=${encodeURIComponent(result.video_id)}`;
            link.textContent = 'View on YouTube';
            link.target = '_blank';
            link.rel = 'noopener';
            status.replaceChildren(result.warning ? `${result.warning} ` : 'Upload complete! ', link);
            // Keep disabled after success to prevent an accidental duplicate upload.
        } catch (error) {
            status.textContent = error instanceof SyntaxError || error instanceof TypeError
                ? 'Upload could not be confirmed. Check YouTube Studio before retrying to avoid a duplicate.'
                : error.message;
            button.disabled = false;
        } finally {
            button.loading = false;
            delete form.dataset.uploading;
        }
    });
})();
