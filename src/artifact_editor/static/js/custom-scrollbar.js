(() => {
    const instances = new WeakMap();

    function initialize(host) {
        if (instances.has(host)) return;
        const viewport = host.querySelector(':scope > [data-scroll-viewport]');
        if (!viewport?.id) return;

        const rail = document.createElement('div');
        rail.className = 'custom-scrollbar-rail';
        const thumb = document.createElement('div');
        thumb.className = 'custom-scrollbar-thumb';
        thumb.tabIndex = 0;
        thumb.setAttribute('role', 'scrollbar');
        thumb.setAttribute('aria-controls', viewport.id);
        thumb.setAttribute('aria-orientation', 'vertical');
        thumb.setAttribute('aria-label', host.dataset.scrollbarLabel || 'Scroll content');
        thumb.setAttribute('aria-valuemin', '0');
        rail.append(thumb);
        host.append(rail);

        let animationFrame = 0;
        let drag = null;
        const geometry = () => {
            const maximum = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
            const track = rail.clientHeight;
            const minimumThumb = Number(host.dataset.scrollbarMinThumb) || 48;
            const height = Math.min(track, Math.max(minimumThumb,
                track * viewport.clientHeight / Math.max(1, viewport.scrollHeight)));
            return { maximum, height, travel: Math.max(0, track - height) };
        };
        const update = () => {
            animationFrame = 0;
            const overflowing = viewport.scrollHeight > viewport.clientHeight + 1;
            host.classList.toggle('has-scrollbar-overflow', overflowing);
            rail.hidden = !overflowing;
            const { maximum, height, travel } = geometry();
            const position = Math.max(0, Math.min(maximum, viewport.scrollTop));
            thumb.style.height = `${height}px`;
            thumb.style.transform = `translateY(${maximum ? position / maximum * travel : 0}px)`;
            thumb.setAttribute('aria-valuemax', String(Math.round(maximum)));
            thumb.setAttribute('aria-valuenow', String(Math.round(position)));
        };
        const schedule = () => {
            if (!animationFrame) animationFrame = requestAnimationFrame(update);
        };
        const listeners = new AbortController();
        const options = { signal: listeners.signal };
        viewport.addEventListener('scroll', schedule, { ...options, passive: true });
        viewport.addEventListener('load', schedule, { ...options, capture: true });
        viewport.addEventListener('toggle', schedule, { ...options, capture: true });
        thumb.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            drag = { pointer: event.pointerId, y: event.clientY, scrollTop: viewport.scrollTop };
            thumb.setPointerCapture(event.pointerId);
            thumb.classList.add('is-dragging');
            thumb.focus({ preventScroll: true });
            event.preventDefault();
        }, options);
        thumb.addEventListener('pointermove', event => {
            if (!drag || drag.pointer !== event.pointerId) return;
            const { maximum, travel } = geometry();
            if (travel) viewport.scrollTop = drag.scrollTop + (event.clientY - drag.y) * maximum / travel;
            schedule();
        }, options);
        const endDrag = () => {
            drag = null;
            thumb.classList.remove('is-dragging');
        };
        thumb.addEventListener('pointerup', endDrag, options);
        thumb.addEventListener('pointercancel', endDrag, options);
        thumb.addEventListener('lostpointercapture', endDrag, options);
        rail.addEventListener('pointerdown', event => {
            if (event.target !== rail || event.button !== 0) return;
            const direction = event.clientY < thumb.getBoundingClientRect().top ? -1 : 1;
            viewport.scrollTop += direction * viewport.clientHeight * 0.9;
            thumb.focus({ preventScroll: true });
            event.preventDefault();
            schedule();
        }, options);
        rail.addEventListener('wheel', event => {
            const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
            viewport.scrollTop += event.deltaY * unit;
            event.preventDefault();
            schedule();
        }, { ...options, passive: false });
        thumb.addEventListener('keydown', event => {
            const { maximum } = geometry();
            const positions = {
                ArrowUp: viewport.scrollTop - 40,
                ArrowDown: viewport.scrollTop + 40,
                PageUp: viewport.scrollTop - viewport.clientHeight * 0.9,
                PageDown: viewport.scrollTop + viewport.clientHeight * 0.9,
                Home: 0,
                End: maximum,
            };
            if (!(event.key in positions)) return;
            event.preventDefault();
            viewport.scrollTop = positions[event.key];
            schedule();
        }, options);

        const sizes = new ResizeObserver(schedule);
        const observeContent = () => {
            sizes.disconnect();
            sizes.observe(viewport);
            Array.from(viewport.children).forEach(child => sizes.observe(child));
        };
        const changes = new MutationObserver(() => { observeContent(); schedule(); });
        changes.observe(viewport, { childList: true, subtree: true, characterData: true });
        observeContent();
        instances.set(host, () => {
            listeners.abort();
            sizes.disconnect();
            changes.disconnect();
            cancelAnimationFrame(animationFrame);
            rail.remove();
            host.classList.remove('custom-scrollbar-ready', 'has-scrollbar-overflow');
            instances.delete(host);
        });
        host.classList.add('custom-scrollbar-ready');
        update();
    }

    function initializeAll() {
        document.querySelectorAll('[data-custom-scrollbar]').forEach(initialize);
    }
    document.addEventListener('DOMContentLoaded', initializeAll);
    document.addEventListener('htmx:load', initializeAll);
    document.addEventListener('htmx:afterSettle', initializeAll);
    document.addEventListener('htmx:beforeCleanupElement', event => {
        const element = event.detail.elt;
        if (element.matches?.('[data-custom-scrollbar]')) instances.get(element)?.();
        element.querySelectorAll?.('[data-custom-scrollbar]').forEach(host => instances.get(host)?.());
    });
    if (document.readyState !== 'loading') initializeAll();
})();
