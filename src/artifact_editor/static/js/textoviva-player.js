(() => {
    function initializePlayers() {
        if (!window.videojs) return;
        document.querySelectorAll('video.textoviva-video').forEach(video => {
            if (videojs.getPlayer(video.id)) return;
            const pauseUrl = video.dataset.pauseUrl;
            const timelineUrl = video.dataset.timelineUrl;
            const player = videojs(video, {
                controls: true,
                fluid: false,
                responsive: true,
                preload: 'metadata',
                loop: false,
            });
            const stack = player.el().closest('#player-stack');
            const renderControls = stack && stack.querySelector('#video-generation-notice');
            function fitPlayer() {
                if (!stack || player.isDisposed() || player.isFullscreen()) return;
                const bounds = stack.getBoundingClientRect();
                if (bounds.width <= 0) return;
                const controlsHeight = renderControls ? renderControls.getBoundingClientRect().height : 0;
                const gap = parseFloat(getComputedStyle(stack).rowGap) || 0;
                const availableHeight = Math.max(0,
                    window.innerHeight - Math.max(0, bounds.top) - controlsHeight - gap - 16);
                if (!availableHeight) return;
                const ratio = player.videoWidth() && player.videoHeight()
                    ? player.videoWidth() / player.videoHeight() : 16 / 9;
                const width = Math.min(bounds.width, availableHeight * ratio);
                player.dimensions(width, width / ratio);
                player.el().style.marginInline = 'auto';
            }
            const sizeObserver = new ResizeObserver(fitPlayer);
            if (stack) sizeObserver.observe(stack);
            if (renderControls) sizeObserver.observe(renderControls);
            window.addEventListener('resize', fitPlayer);
            document.addEventListener('column-expanded', fitPlayer);
            ['loadedmetadata', 'resize', 'fullscreenchange'].forEach(name => player.on(name, fitPlayer));
            player.ready(fitPlayer);
            player.on('dispose', () => {
                sizeObserver.disconnect();
                window.removeEventListener('resize', fitPlayer);
                document.removeEventListener('column-expanded', fitPlayer);
            });
            let timeline = null;
            let centeredPhrase = null;
            let pauseRequested = false;
            function syncTimeline() {
                if (!timeline || player.isDisposed()) return;
                const frame = Math.floor(player.currentTime() * timeline.fps);
                const phrase = timeline.phrases.find(item => item.start <= frame && frame <= item.end);
                // Images begin at their XML positions and persist until the next image.
                const image = timeline.images.filter(item => item.start <= frame).at(-1);
                document.documentElement.dataset.playbackFrame = String(frame);
                document.documentElement.dataset.playbackImageIndex = image ? String(image.index) : '';
                let activePhrase = null;
                document.querySelectorAll('[data-timeline-type]').forEach(item => {
                    const current = item.dataset.timelineType === 'phrase' ? phrase : image;
                    const active = Boolean(current && item.dataset.timelineIndex === String(current.index));
                    item.classList.toggle('playback-current', active);
                    if (active) item.setAttribute('aria-current', 'true');
                    else item.removeAttribute('aria-current');
                    if (active && item.dataset.timelineType === 'phrase') activePhrase = item;
                });
                const scroller = activePhrase && activePhrase.closest('#chapter-timeline-scroll');
                if (scroller && scroller.getClientRects().length) {
                    if (activePhrase !== centeredPhrase) {
                        const bounds = scroller.getBoundingClientRect();
                        const phraseBounds = activePhrase.getBoundingClientRect();
                        scroller.scrollTo({
                            top: scroller.scrollTop + phraseBounds.top - bounds.top - scroller.clientTop
                                + phraseBounds.height / 2 - scroller.clientHeight / 2,
                            behavior: 'instant',
                        });
                        centeredPhrase = activePhrase;
                    }
                } else {
                    centeredPhrase = null;
                }
                document.querySelectorAll('#phrase_accordion wa-accordion-item').forEach(item => {
                    const active = Boolean(phrase && item.id === `phrase_${phrase.index}`);
                    item.classList.toggle('playback-current', active);
                });
            }
            let animationFrame = null;
            function followPlayback() {
                syncTimeline();
                if (!player.isDisposed() && !player.paused() && !player.ended()) {
                    animationFrame = requestAnimationFrame(followPlayback);
                }
            }
            function stopFollowing() {
                if (animationFrame !== null) cancelAnimationFrame(animationFrame);
                animationFrame = null;
            }
            function loadTimeline() {
                if (!timelineUrl) return;
                return fetch(timelineUrl, { cache: 'no-store' }).then(response => {
                    if (!response.ok) throw new Error('Could not load playback timeline');
                    return response.json();
                }).then(data => {
                    timeline = data;
                    syncTimeline();
                    if (pauseRequested) reportPausedFrame();
                })
                    .catch(error => console.error(error));
            }
            function refreshVideo() {
                if (player.isDisposed()) return;
                stopFollowing();
                timeline = null;
                centeredPhrase = null;
                // A completed render can replace the first frame while keeping
                // the poster URL unchanged. Bust its cache alongside the video
                // so Video.js doesn't keep a stale (or missing) poster.
                const poster = player.poster();
                if (poster) {
                    const posterUrl = new URL(poster, window.location.href);
                    posterUrl.searchParams.set('render', Date.now());
                    player.poster(posterUrl.href);
                }
                const source = player.currentSource();
                const url = new URL(source.src, window.location.href);
                url.searchParams.set('render', Date.now());
                player.src({ src: url.href, type: source.type || 'video/mp4' });
                player.load();
                loadTimeline();
            }
            document.addEventListener('textoviva:render-complete', refreshVideo);
            player.on('dispose', () => {
                document.removeEventListener('textoviva:render-complete', refreshVideo);
            });
            if (timelineUrl) {
                loadTimeline();
                ['timeupdate', 'seeked', 'loadedmetadata', 'play', 'pause', 'ended'].forEach(name => {
                    player.on(name, syncTimeline);
                });
                player.on('play', () => { stopFollowing(); followPlayback(); });
                function reportPausedFrame() {
                    stopFollowing();
                    pauseRequested = true;
                    if (!timeline) return;
                    syncTimeline();
                    const frameIndex = Math.floor(player.currentTime() * timeline.fps);
                    const image = timeline.images.filter(item => item.start <= frameIndex).at(-1);
                    if (image) {
                        pauseRequested = false;
                        document.dispatchEvent(new CustomEvent('textoviva:paused', {
                            detail: {frameIndex, imageIndex: image.index}
                        }));
                    }
                }
                player.on('pause', reportPausedFrame);
                player.on('seeked', () => {
                    if (player.paused()) reportPausedFrame();
                });
                player.on('ended', stopFollowing);
                document.addEventListener('htmx:afterSettle', syncTimeline);
                document.addEventListener('column-expanded', syncTimeline);
                player.on('dispose', () => {
                    stopFollowing();
                    document.removeEventListener('htmx:afterSettle', syncTimeline);
                    document.removeEventListener('column-expanded', syncTimeline);
                    document.querySelectorAll('.playback-current').forEach(item => {
                        item.classList.remove('playback-current');
                        item.removeAttribute('aria-current');
                    });
                });
            }
            // The full Textoviva page synchronizes its workshop on pause.
            // The chapter column contains only the player.
            if (pauseUrl) {
                player.on('pause', () => {
                    if (player.isFullscreen()) player.exitFullscreen();
                    htmx.ajax('GET', pauseUrl + '?pauseTime=' + encodeURIComponent(player.currentTime()), {
                        swap: 'none',
                    });
                });
                player.on('play', () => {
                    Promise.resolve(player.requestFullscreen()).catch(() => {});
                });
            }
        });
    }

    const completedJobs = new Set();
    document.addEventListener('htmx:wsAfterSend', event => {
        const button = event.detail.elt?.closest('#video-generation-notice')
            ?.querySelector('wa-button');
        if (button) button.disabled = true;
    });
    document.addEventListener('htmx:wsAfterMessage', () => {
        let refresh = false;
        document.querySelectorAll('[data-video-job][data-video-state]').forEach(status => {
            const job = status.dataset.videoJob;
            if (status.closest('#video-generation-notice') &&
                ['complete', 'failed'].includes(status.dataset.videoState)) {
                const button = status.closest('#video-generation-notice').querySelector('wa-button');
                if (button) button.disabled = false;
            }
            if (status.dataset.videoState !== 'complete') {
                completedJobs.delete(job);
            } else if (!completedJobs.has(job)) {
                completedJobs.add(job);
                refresh = true;
            }
        });
        if (refresh) document.dispatchEvent(new Event('textoviva:render-complete'));
    });

    document.addEventListener('DOMContentLoaded', initializePlayers);
    document.addEventListener('htmx:afterSwap', initializePlayers);
    document.addEventListener('htmx:beforeCleanupElement', event => {
        if (!window.videojs) return;
        const player = videojs.getPlayer('chapter-video');
        if (!player || player.isDisposed()) return;
        const element = event.detail.elt;
        if (element === player.el() || element.contains(player.el())) player.dispose();
    });
})();
