(() => {
    document.addEventListener('htmx:wsConfigSend', event => {
        if (event.detail.parameters.action !== 'chapter_import') return;
        event.detail.parameters.sections = JSON.stringify(window.getChapterSectionsPayload());
    });

    document.addEventListener('htmx:wsAfterSend', event => {
        if (event.detail.elt?.id !== 'import_btn' &&
            !event.detail.elt?.hasAttribute('data-metadata-button')) return;
        event.detail.elt.disabled = true;
    });

    document.addEventListener('htmx:wsAfterMessage', () => {
        document.querySelectorAll('[data-chapter-import], [data-chapter-metadata], [data-chapter-assets]').forEach(container => {
            const job = container.dataset.chapterImport || container.dataset.chapterMetadata || container.dataset.chapterAssets;
            const status = document.getElementById(`video-progress-${job}`);
            const state = status?.dataset.videoState;
            if (state === 'complete') {
                // Reload the typeset text, phrase audio, and image timeline together.
                window.location.reload();
            } else if (state === 'failed') {
                container.querySelector('wa-button').disabled = false;
            }
        });
    });
})();
