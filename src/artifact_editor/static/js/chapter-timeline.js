function initializeChapterTimeline() {
    const list = document.getElementById('chapter-phrase-list');
    if (!list || list.dataset.initialized) return;
    list.dataset.initialized = 'true';
    const status = document.getElementById('chapter-timeline-status');
    const selectPanel = event => {
        const panel = event.target.closest('[data-timeline-type]');
        if (!panel || panel.parentElement !== list) return;
        const timeLabel = event.target.closest('.chapter-timeline-time');
        // Focusing the label must not select it before its click toggles it.
        if (timeLabel && event.type === 'focusin') return;
        if (timeLabel && panel.dataset.timelineType === 'phrase') {
            const textarea = panel.querySelector('wa-textarea.phrase-editor');
            if (textarea) {
                // Use the textarea's existing HTMX request and response context.
                textarea.click();
                return;
            }
            if (panel.dataset.editorLoading) return;
            panel.dataset.editorLoading = 'true';
            htmx.ajax('GET', panel.dataset.editorUrl, {
                source: panel,
                target: panel,
                swap: 'none',
                values: {
                    previous_phrase_index: window.previous_phrase_index ?? -1,
                    collapse: '1',
                },
            }).finally(() => { delete panel.dataset.editorLoading; });
            return;
        }
        const deselect = timeLabel && panel.classList.contains('selected');
        list.querySelectorAll(':scope > .selected').forEach(item => {
            if (item !== panel) item.classList.remove('selected');
        });
        if (panel.dataset.timelineType !== 'image') panel.classList.toggle('selected', !deselect);
        // Image labels live inside summary, whose default click toggles open.
    };
    list.addEventListener('click', selectPanel);
    list.addEventListener('focusin', selectPanel);
    list.addEventListener('keydown', event => {
        const label = event.target.closest('.chapter-timeline-time');
        if (!label || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        label.click();
    });
    list.addEventListener('toggle', event => {
        const expanded = event.target;
        if (!expanded.matches('details.chapter-timeline-image') || !expanded.open) return;
        list.querySelectorAll('details.chapter-timeline-image[open]').forEach(item => {
            if (item !== expanded) item.open = false;
        });
    }, true);
    list.addEventListener('htmx:responseError', () => {
        status.textContent = 'Timeline action failed. Please try again.';
    });
    let dragged = null;
    let saving = false;
    const clear = () => list.querySelectorAll('.drop-before, .drop-after')
        .forEach(item => item.classList.remove('drop-before', 'drop-after'));
    const destination = event => {
        const item = event.target.closest('[data-timeline-type]');
        return item && item.parentElement === list ? item : null;
    };
    list.addEventListener('dragstart', event => {
        const item = destination(event);
        if (saving || !item || item.dataset.timelineType !== 'image') {
            event.preventDefault();
            return;
        }
        dragged = item;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', item.dataset.timelineIndex);
    });
    list.addEventListener('dragover', event => {
        if (!dragged || saving) return;
        const target = destination(event);
        clear();
        if (!target || target === dragged) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        const rect = target.getBoundingClientRect();
        target.classList.add(event.clientY < rect.top + rect.height / 2 ? 'drop-before' : 'drop-after');
    });
    list.addEventListener('dragend', () => { clear(); dragged = null; });
    list.addEventListener('drop', async event => {
        if (!dragged || saving) return;
        event.preventDefault();
        const target = destination(event);
        if (!target || target === dragged) { clear(); return; }
        const item = dragged;
        const rect = target.getBoundingClientRect();
        const position = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
        clear();
        saving = true;
        status.textContent = 'Saving image position…';
        try {
            const response = await fetch(list.dataset.moveUrl, {
                method: 'POST',
                body: new URLSearchParams({
                    image_index: item.dataset.timelineIndex,
                    target_type: target.dataset.timelineType,
                    target_index: target.dataset.timelineIndex,
                    position,
                }),
            });
            if (!response.ok) throw new Error(await response.text());
            if (position === 'before') target.before(item);
            else target.after(item);
            htmx.swap(list, await response.text(), { swapStyle: 'none' });
            status.textContent = 'Image position saved.';
        } catch (error) {
            status.textContent = 'Could not move image. ' + error.message;
        } finally {
            saving = false;
            dragged = null;
        }
    });
}
document.addEventListener('DOMContentLoaded', initializeChapterTimeline);
document.addEventListener('htmx:afterSettle', initializeChapterTimeline);

function initializeChapterAnimationPlayers() {
    if (!window.videojs) return;
    document.querySelectorAll('video.chapter-animation-video').forEach(video => {
        if (videojs.getPlayer(video.id)) return;
        videojs(video, { controls: true, fluid: true, responsive: true, preload: 'metadata' });
    });
}
document.addEventListener('DOMContentLoaded', initializeChapterAnimationPlayers);
document.addEventListener('htmx:afterSettle', initializeChapterAnimationPlayers);
document.addEventListener('htmx:beforeCleanupElement', event => {
    if (!window.videojs) return;
    const element = event.detail.elt;
    Object.values(videojs.getPlayers()).forEach(player => {
        if (!player || player.isDisposed()) return;
        const playerElement = player.el();
        if (playerElement.classList.contains('chapter-animation-video') &&
            (element === playerElement || element.contains(playerElement))) {
            player.dispose();
        }
    });
});

// Websocket progress replaces the status node; refresh only the completed image's editor.
const completedTimelineImages = new Set();
document.addEventListener('htmx:wsAfterMessage', () => {
    document.querySelectorAll('[data-image-editor-url] [data-video-job]').forEach(status => {
        if (status.dataset.videoState !== 'complete' || completedTimelineImages.has(status.dataset.videoJob)) return;
        completedTimelineImages.add(status.dataset.videoJob);
        const panel = status.closest('[data-image-editor-url]');
        const imageIndex = status.closest('[data-center-generated-image]')?.dataset.centerGeneratedImage;
        htmx.ajax('GET', panel.dataset.imageEditorUrl, {
            target: panel.dataset.imageEditorTarget, swap: 'innerHTML',
        }).then(async () => {
            if (imageIndex === undefined) return;
            const preview = document.getElementById(`timeline-image-preview-${imageIndex}`);
            if (!preview) return;
            // Wait for the updated image dimensions before choosing the scroll position.
            try { await preview.decode(); } catch (_) { /* Still center a failed preview. */ }
            requestAnimationFrame(() => {
                if (preview.isConnected) preview.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
            });
        });
    });
});

// Read the current timeline each time so newly generated and reordered images participate.
let viewedChapterImage = null;
function navigateChapterImage(direction = 0) {
    const images = Array.from(document.querySelectorAll('.chapter-timeline-image > summary > img[data-full-image-url]'));
    const position = images.findIndex(image => image.closest('[data-timeline-index]').dataset.timelineIndex === viewedChapterImage);
    const image = images[position + direction];
    if (position < 0 || !image) return;
    viewedChapterImage = image.closest('[data-timeline-index]').dataset.timelineIndex;
    const fullImage = document.getElementById('chapter-image-full');
    fullImage.src = image.dataset.fullImageUrl;
    fullImage.alt = image.alt;
    document.getElementById('chapter-image-viewer').label = `Image ${viewedChapterImage}`;
    document.getElementById('chapter-image-previous').disabled = position + direction === 0;
    document.getElementById('chapter-image-next').disabled = position + direction === images.length - 1;
}
document.addEventListener('click', event => {
    const opener = event.target.closest('[data-view-image]');
    if (opener && !opener.disabled) {
        viewedChapterImage = opener.dataset.viewImage;
        navigateChapterImage();
        document.getElementById('chapter-image-viewer').open = true;
    }
    if (event.target.closest('#chapter-image-previous')) navigateChapterImage(-1);
    if (event.target.closest('#chapter-image-next')) navigateChapterImage(1);
});
document.addEventListener('keydown', event => {
    if (!document.getElementById('chapter-image-viewer')?.open) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        navigateChapterImage(event.key === 'ArrowLeft' ? -1 : 1);
    }
});
