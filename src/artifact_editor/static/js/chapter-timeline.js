function initializeChapterTimeline() {
    const list = document.getElementById('chapter-phrase-list');
    if (!list) return;
    initializeTimelineImageBackgrounds(list);
    if (list.dataset.initialized) return;
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
        if (saving || !item || !['image', 'scene'].includes(item.dataset.timelineType)) {
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
        status.textContent = 'Saving panel position…';
        try {
            const response = await fetch(list.dataset.moveUrl, {
                method: 'POST',
                body: new URLSearchParams({
                    image_index: item.dataset.timelineIndex,
                    item_type: item.dataset.timelineType,
                    target_type: target.dataset.timelineType,
                    target_index: target.dataset.timelineIndex,
                    position,
                }),
            });
            if (!response.ok) throw new Error(await response.text());
            if (position === 'before') target.before(item);
            else target.after(item);
            htmx.swap(list, await response.text(), { swapStyle: 'none' });
            status.textContent = 'Panel position saved.';
        } catch (error) {
            status.textContent = 'Could not move panel. ' + error.message;
        } finally {
            saving = false;
            dragged = null;
        }
    });
}
document.addEventListener('DOMContentLoaded', initializeChapterTimeline);
document.addEventListener('htmx:afterSettle', initializeChapterTimeline);

function timelineImageHashValue() {
    const value = new URLSearchParams(location.hash.slice(1)).get('image');
    const match = value?.trim().replace(/^['"]|['"]$/g, '').match(/^image\s+(\d+)$/i);
    return match ? Number(match[1]) : null;
}

function setTimelineImageHash(index) {
    if (timelineImageHashValue() === Number(index)) return;
    const fields = new URLSearchParams(location.hash.slice(1));
    fields.set('image', `image ${index}`);
    history.replaceState(history.state, '', `${location.pathname}${location.search}#${fields}`);
}

function scrollToTimelineImage(index) {
    const viewport = document.getElementById('chapter-timeline-scroll');
    const list = document.getElementById('chapter-phrase-list');
    const image = list && Array.from(list.children).find(item =>
        item.dataset.timelineType === 'image' && Number(item.dataset.timelineIndex) === Number(index)
    );
    if (!viewport || !image) return false;
    image.open = true;
    const editor = image.querySelector('.chapter-mini-image-editor');
    const summary = image.querySelector(':scope > summary[hx-get]');
    if (editor?.textContent.includes('Loading image editor') && summary && window.htmx) {
        htmx.ajax('GET', summary.getAttribute('hx-get'), { target: editor, swap: 'innerHTML' });
    }
    requestAnimationFrame(() => {
        const offset = image.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
        viewport.scrollTop += offset;
    });
    return true;
}

function initializeTimelineImageHash() {
    const viewport = document.getElementById('chapter-timeline-scroll');
    const list = document.getElementById('chapter-phrase-list');
    if (!viewport || !list || viewport.dataset.imageHashInitialized) return;
    viewport.dataset.imageHashInitialized = 'true';
    let updateTimer;
    const updateHashFromScroll = () => {
        const viewportTop = viewport.getBoundingClientRect().top + 1;
        const images = Array.from(list.children).filter(item => item.dataset.timelineType === 'image');
        if (!images.length) return;
        const current = images.filter(image => image.getBoundingClientRect().top <= viewportTop).at(-1)
            || images.find(image => image.getBoundingClientRect().bottom > viewportTop)
            || images[0];
        setTimelineImageHash(current.dataset.timelineIndex);
    };
    viewport.addEventListener('scroll', () => {
        clearTimeout(updateTimer);
        updateTimer = setTimeout(updateHashFromScroll, 80);
    }, { passive: true });
    window.addEventListener('pagehide', updateHashFromScroll);
    window.addEventListener('hashchange', () => {
        const imageIndex = timelineImageHashValue();
        if (imageIndex !== null) scrollToTimelineImage(imageIndex);
    });
    const requestedImage = timelineImageHashValue();
    if (requestedImage !== null && scrollToTimelineImage(requestedImage)) return;
    // Migrate the scroll bookmark saved by the previous timeline script so
    // its first reload lands at the same image before the URL hash takes over.
    try {
        const key = `chapter-timeline-scroll:${location.pathname}`;
        const previous = JSON.parse(sessionStorage.getItem(key) || 'null');
        sessionStorage.removeItem(key);
        const match = previous?.anchorId?.match(/^timeline-image-(\d+)$/);
        if (match && scrollToTimelineImage(Number(match[1]))) {
            setTimelineImageHash(Number(match[1]));
            return;
        }
        if (previous && Number.isFinite(Number(previous.scrollTop))) {
            viewport.scrollTop = Number(previous.scrollTop);
        }
    } catch (_) {
        // Ignore unavailable browser storage and fall back to the current view.
    }
    requestAnimationFrame(updateHashFromScroll);
}

document.addEventListener('DOMContentLoaded', initializeTimelineImageHash);
document.addEventListener('htmx:afterSettle', initializeTimelineImageHash);

document.addEventListener('chapter-image-selected', event => {
    const detail = event.detail || {};
    const panel = document.getElementById(`timeline-image-${detail.image_index}`);
    if (!panel) return;
    const preview = panel.querySelector('.chapter-mini-image-preview');
    if (preview && detail.preview_url) {
        preview.src = detail.preview_url;
        preview.alt = detail.alt || 'Chapter image';
    }
    panel.querySelector('.chapter-image-viewer-open')?.removeAttribute('disabled');
    if (detail.full_image_url) {
        panel.dataset.timelineBackgroundUrl = detail.full_image_url;
        panel.dataset.fullImageUrl = detail.full_image_url;
        panel.dataset.imageAlt = detail.alt || 'Chapter image';
        const marker = panel.querySelector('[data-timeline-background-marker]');
        if (marker) {
            marker.dataset.backgroundUrl = detail.full_image_url;
            marker.dataset.fullImageUrl = detail.full_image_url;
            marker.dataset.imageAlt = detail.alt || 'Chapter image';
        }
        panel.parentElement?._timelineBackgroundUpdate?.();
    }
});

function initializeTimelineImageBackgrounds(list) {
    const syncBackgroundMarkers = () => {
        list.querySelectorAll(':scope > details[data-timeline-type="image"]').forEach(panel => {
            const marker = panel.querySelector('[data-timeline-background-marker]');
            if (!marker) return;
            panel.dataset.timelineBackgroundUrl = marker.dataset.backgroundUrl || '';
            panel.dataset.fullImageUrl = marker.dataset.fullImageUrl || '';
            panel.dataset.imageAlt = marker.dataset.imageAlt || 'Chapter image';
        });
    };
    const update = () => {
        syncBackgroundMarkers();
        const entries = Array.from(list.children).filter(entry => entry.matches('[data-timeline-type]'));
        entries.forEach(entry => {
            entry.style.removeProperty('background-image');
            entry.style.removeProperty('background-size');
            entry.style.removeProperty('background-position');
            entry.style.removeProperty('background-repeat');
            entry.style.removeProperty('background-color');
            entry.style.removeProperty('background-blend-mode');
            delete entry.dataset.imageBackground;
        });
        for (let start = 0; start < entries.length;) {
            if (entries[start].dataset.timelineType !== 'image') { start++; continue; }
            let end = start + 1;
            while (end < entries.length && entries[end].dataset.timelineType !== 'image') end++;
            const imageUrl = entries[start].dataset.timelineBackgroundUrl;
            if (imageUrl) {
                const firstTop = entries[start].getBoundingClientRect().top;
                const lastBottom = entries[end - 1].getBoundingClientRect().bottom;
                const spanHeight = Math.max(1, lastBottom - firstTop);
                entries.slice(start, end).forEach(entry => {
                    const offset = entry.getBoundingClientRect().top - firstTop;
                    entry.style.backgroundImage = `url("${imageUrl}")`;
                    entry.style.backgroundSize = `auto ${spanHeight}px`;
                    entry.style.backgroundPosition = `0px -${offset}px`;
                    entry.style.backgroundRepeat = 'no-repeat';
                    entry.style.backgroundColor = 'rgba(0, 0, 0, 0.38)';
                    entry.style.backgroundBlendMode = 'multiply';
                    entry.dataset.imageBackground = 'true';
                });
            }
            start = end;
        }
    };
    if (!list.dataset.backgroundObserverInitialized) {
        list.dataset.backgroundObserverInitialized = 'true';
        let frame = 0;
        const schedule = () => {
            if (frame) return;
            frame = requestAnimationFrame(() => { frame = 0; update(); });
        };
        const resizeObserver = new ResizeObserver(schedule);
        resizeObserver.observe(list);
        list.querySelectorAll(':scope > [data-timeline-type]')
            .forEach(entry => resizeObserver.observe(entry));
        new MutationObserver(() => {
            list.querySelectorAll(':scope > [data-timeline-type]')
                .forEach(entry => resizeObserver.observe(entry));
            schedule();
        }).observe(list, { childList: true });
        list.addEventListener('toggle', schedule, true);
        window.addEventListener('resize', schedule);
        list._timelineBackgroundUpdate = schedule;
    }
    list._timelineBackgroundUpdate?.();
}

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
async function refreshTimelineScene(panel) {
    const response = await fetch(panel.dataset.imageEditorUrl);
    if (!response.ok) throw new Error('Could not refresh the scene editor');
    const updated = new DOMParser().parseFromString(await response.text(), 'text/html');
    // Keep the mounted controls, focus, and layout intact during background updates.
    panel.querySelectorAll('[name]').forEach(control => {
        const replacement = updated.querySelector(`[name="${control.name}"]`);
        if (replacement) control.value = replacement.getAttribute('value') ?? '';
    });
    const scene = panel.closest('[data-timeline-type="scene"]');
    const preview = updated.querySelector('img');
    if (preview) {
        scene.querySelectorAll('img').forEach(image => {
            if (image.getAttribute('src') !== preview.getAttribute('src')) {
                image.src = preview.getAttribute('src');
            }
            if (image.hasAttribute('data-full-image-url')) image.dataset.fullImageUrl = preview.getAttribute('src');
        });
        panel.querySelector('[data-view-image]')?.toggleAttribute('disabled',
            updated.querySelector('[data-view-image]').hasAttribute('disabled'));
    }
}
document.addEventListener('htmx:wsAfterMessage', () => {
    document.querySelectorAll('[data-image-editor-url] [data-video-job]').forEach(status => {
        if (status.dataset.videoState !== 'complete' || completedTimelineImages.has(status.dataset.videoJob)) return;
        completedTimelineImages.add(status.dataset.videoJob);
        const panel = status.closest('[data-image-editor-url]');
        if (panel.closest('[data-timeline-type="scene"]')) {
            refreshTimelineScene(panel).catch(error => {
                completedTimelineImages.delete(status.dataset.videoJob);
                console.error(error);
            });
            return;
        }
        const imageIndex = status.closest('[data-center-generated-image]')?.dataset.centerGeneratedImage;
        htmx.ajax('GET', panel.dataset.imageEditorUrl, {
            target: panel.dataset.imageEditorTarget, swap: 'innerHTML',
        }).then(async () => {
            if (imageIndex === undefined) return;
            const panel = document.getElementById(`timeline-image-${imageIndex}`);
            if (!panel) return;
            requestAnimationFrame(() => {
                if (panel.isConnected) panel.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
            });
        });
    });
});

// Read the current timeline each time so newly generated and reordered images participate.
let viewedChapterImage = null;
let viewedChapterImageType = 'image';
function navigateChapterImage(direction = 0) {
    const images = Array.from(document.querySelectorAll(
        'details.chapter-timeline-image[data-full-image-url], .chapter-timeline-scene > summary > img[data-full-image-url]'
    )).filter(image => image.dataset.fullImageUrl);
    const position = images.findIndex(image => image.closest('[data-timeline-index]').dataset.timelineIndex === viewedChapterImage && image.closest('[data-timeline-type]').dataset.timelineType === viewedChapterImageType);
    const image = images[position + direction];
    if (position < 0 || !image) return;
    viewedChapterImage = image.closest('[data-timeline-index]').dataset.timelineIndex;
    viewedChapterImageType = image.closest('[data-timeline-type]').dataset.timelineType;
    const fullImage = document.getElementById('chapter-image-full');
    fullImage.src = image.dataset.fullImageUrl;
    fullImage.alt = image.dataset.imageAlt || image.alt || 'Chapter image';
    document.getElementById('chapter-image-viewer').label = `${viewedChapterImageType === 'scene' ? 'Scene' : 'Image'} ${viewedChapterImage}`;
    document.getElementById('chapter-image-previous').disabled = position + direction === 0;
    document.getElementById('chapter-image-next').disabled = position + direction === images.length - 1;
}
document.addEventListener('click', event => {
    const opener = event.target.closest('[data-view-image]');
    if (opener && !opener.disabled) {
        viewedChapterImage = opener.dataset.viewImage;
        viewedChapterImageType = opener.dataset.viewType || 'image';
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
