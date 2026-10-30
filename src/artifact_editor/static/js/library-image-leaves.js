(() => {
    const stage = document.querySelector('[data-library-image-leaves]');
    if (!stage) return;
    const leaves = window.ImageLeaves.create(stage);
    const identity = JSON.parse(stage.dataset.chapterIdentity);
    const seen = new Set();
    const pending = new Map();
    let initialized = false;
    let closing = false;

    function queue(item) {
        if (item?.id && item.url && !seen.has(item.id)) pending.set(item.id, item);
    }

    function revealPending() {
        if (!initialized || closing || document.hidden) return;
        for (const [id, item] of pending) {
            if (!seen.has(id)) {
                leaves.show(item);
                seen.add(id);
            }
            pending.delete(id);
        }
    }

    document.addEventListener('chapter:image-event', ({detail: event}) => {
        if (!event || !Object.keys(identity).every(key => String(event[key]) === String(identity[key]))) return;
        if (event.state === 'chapter_image_snapshot' && Array.isArray(event.images)) {
            if (!initialized) {
                // The first snapshot establishes a baseline. Reconnects reveal
                // only completions missed while the socket was disconnected.
                for (const item of event.images) if (item.id) seen.add(item.id);
                initialized = true;
            } else {
                event.images.forEach(queue);
            }
        } else if (event.state === 'chapter_image_complete') {
            queue(event.image);
        }
        revealPending();
    });
    document.addEventListener('visibilitychange', revealPending);
    window.addEventListener('pagehide', () => { closing = true; });
})();
