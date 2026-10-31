(() => {
    const stage = document.querySelector('[data-frame-aspect]');
    if (!stage || !window.ImageLeaves) return;
    const identity = JSON.parse(stage.dataset.chapterIdentity || '{}');
    const aspect = stage.dataset.frameAspect;
    const leaves = window.ImageLeaves.create(stage);
    let nextRevealAt = performance.now() + randomInterval();
    let latestCompletedFrame = -1;
    let socket;
    let stopped = false;

    function randomInterval() {
        return 30_000 + Math.random() * 15_000;
    }

    function reveal(event) {
        if (!event || event.state !== 'chapter_frame_complete' || event.aspect !== aspect || !event.url) return;
        if (!Object.keys(identity).every(key => String(event[key]) === String(identity[key]))) return;
        const now = performance.now();
        if (now < nextRevealAt || document.hidden) return;

        // Wait for the saved PNG to be available and decoded before starting
        // the falling leaf animation, so the completed frame is what appears.
        const image = new Image();
        image.onload = async () => {
            if (image.decode) {
                try { await image.decode(); } catch (_) { return; }
            }
            if (document.hidden || stopped) return;
            leaves.show({index: event.frame_index, url: event.url});
        };
        image.src = event.url;
        nextRevealAt = now + randomInterval();
    }

    function revealFromQueue(plan) {
        if (plan?.state !== 'cpu_queue_plan') return;
        const frames = (plan.running || []).filter(task => {
            const chapter = task.chapter || {};
            return task.action === 'draw_frame'
                && String(chapter.author) === String(identity.author)
                && String(chapter.title) === String(identity.title)
                && String(chapter.chapter_number) === String(identity.chapter_number)
                && String(chapter.language) === String(identity.language)
                && chapter.aspect === aspect;
        }).map(task => {
            const match = String(task.label || '').match(/^Frame\s+(\d+)\s+of\s+/i);
            return match ? Number(match[1]) - 2 : -1;
        }).filter(index => index >= 0);
        if (!frames.length) return;
        // The lowest frame still running means every earlier frame in this
        // FIFO batch has finished saving. The normal completion event below
        // handles newly deployed workers; queue progress also covers workers
        // that were already drawing when this page opened.
        const completed = Math.min(...frames);
        if (completed <= latestCompletedFrame) return;
        latestCompletedFrame = completed;
        reveal({
            state: 'chapter_frame_complete', ...identity, aspect,
            frame_index: completed,
            url: `/library/${encodeURIComponent(identity.author)}/${encodeURIComponent(identity.title)}/${identity.chapter_number}/${encodeURIComponent(identity.language)}/frames/${encodeURIComponent(aspect)}/${completed}.png?v=${Date.now()}`,
        });
    }

    document.addEventListener('chapter:frame-event', ({detail}) => reveal(detail));
    document.addEventListener('cpu:queue-plan', ({detail}) => revealFromQueue(detail));

    const endpoint = stage.dataset.frameWebsocket;
    if (!endpoint) return; // The Text tab already has the chapter WebSocket.
    function connect() {
        if (stopped) return;
        socket = new WebSocket(endpoint);
        socket.addEventListener('open', () => socket.send(JSON.stringify({
            ...identity, aspect, action: 'watch_chapter_frames',
        })));
        socket.addEventListener('message', ({data}) => {
            try {
                const event = JSON.parse(data);
                if (event.state === 'cpu_queue_plan') revealFromQueue(event);
                else reveal(event);
            } catch (_) { /* Ignore unrelated events. */ }
        });
        socket.addEventListener('close', () => {
            if (!stopped) window.setTimeout(connect, 2_000);
        });
    }
    window.addEventListener('pagehide', () => {
        stopped = true;
        socket?.close();
    }, {once: true});
    connect();
})();
