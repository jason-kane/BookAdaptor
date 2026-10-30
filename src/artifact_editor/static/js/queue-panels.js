(() => {
    const controls = 'button, a, input, select, textarea, [contenteditable], wa-button, wa-slider, [role="button"], [role="slider"]';
    for (const panel of document.querySelectorAll('[data-queue-panel]')) {
        const stateKey = `bookadaptor:queue-panel-state:${panel.id}`;
        const toggle = panel.querySelector('[id$="-queue-toggle"]');
        if (toggle) {
            const queueName = panel.getAttribute('aria-label')?.toLowerCase().includes('gpu') ? 'GPU queue' : 'CPU queue';
            const labelFor = expanded => `${expanded ? 'Minimize' : 'Expand'} ${queueName}`;
            const applyExpanded = expanded => {
                panel.classList.toggle('is-minimized', !expanded);
                toggle.setAttribute('aria-expanded', String(expanded));
                toggle.setAttribute('aria-label', labelFor(expanded));
                toggle.title = labelFor(expanded);
            };
            try {
                const saved = localStorage.getItem(stateKey);
                if (saved === 'expanded' || saved === 'minimized') applyExpanded(saved === 'expanded');
            } catch (_) { /* optional storage */ }
            toggle.addEventListener('click', () => {
                const expanded = !panel.classList.contains('is-minimized');
                try { localStorage.setItem(stateKey, expanded ? 'minimized' : 'expanded'); } catch (_) { /* optional storage */ }
            });
        }
        const handle = panel.querySelector('[data-queue-drag-handle]');
        if (!handle) continue;
        const key = `bookadaptor:queue-panel:${panel.id}`;
        let position = null;
        let drag = null;

        function place(x, y) {
            const rect = panel.getBoundingClientRect();
            const margin = 8;
            const left = Math.max(margin, Math.min(x, window.innerWidth - rect.width - margin));
            const top = Math.max(margin, Math.min(y, window.innerHeight - rect.height - margin));
            Object.assign(panel.style, {left: `${left}px`, top: `${top}px`, right: 'auto', bottom: 'auto'});
            return {x: left, y: top};
        }
        function save() {
            try { localStorage.setItem(key, JSON.stringify(position)); } catch (_) { /* optional storage */ }
        }
        function isControl(event) {
            return event.composedPath().some(node => node !== handle && node.matches?.(controls));
        }
        function finish(cancelled = false) {
            if (!drag) return;
            const previous = drag;
            drag = null;
            panel.classList.remove('is-dragging');
            document.documentElement.classList.remove('queue-panel-dragging');
            if (cancelled) {
                position = previous.position;
                Object.assign(panel.style, previous.styles);
            } else if (previous.moved) save();
            if (handle.hasPointerCapture(previous.id)) handle.releasePointerCapture(previous.id);
        }
        try {
            const saved = JSON.parse(localStorage.getItem(key));
            if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
                position = {x: saved.x, y: saved.y};
                place(position.x, position.y);
            }
        } catch (_) { /* absent, malformed, or disabled storage uses the default layout */ }

        handle.addEventListener('pointerdown', event => {
            if (event.button !== 0 || event.isPrimary === false || drag || isControl(event)) return;
            const rect = panel.getBoundingClientRect();
            drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top,
                position, moved: false, styles: Object.fromEntries(['left', 'top', 'right', 'bottom']
                    .map(name => [name, panel.style[name]]))};
            handle.setPointerCapture(event.pointerId);
            handle.focus({preventScroll: true});
            event.preventDefault();
        });
        handle.addEventListener('pointermove', event => {
            if (!drag || drag.id !== event.pointerId) return;
            const dx = event.clientX - drag.x;
            const dy = event.clientY - drag.y;
            if (!drag.moved && Math.hypot(dx, dy) < 4) return;
            drag.moved = true;
            panel.classList.add('is-dragging');
            document.documentElement.classList.add('queue-panel-dragging');
            position = place(drag.left + dx, drag.top + dy);
            event.preventDefault();
        });
        handle.addEventListener('pointerup', event => { if (drag?.id === event.pointerId) finish(); });
        handle.addEventListener('pointercancel', event => { if (drag?.id === event.pointerId) finish(true); });
        handle.addEventListener('lostpointercapture', () => finish());
        handle.addEventListener('keydown', event => {
            if (isControl(event) || event.altKey || event.ctrlKey || event.metaKey) return;
            const direction = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
            if (!direction || drag) return;
            const rect = panel.getBoundingClientRect();
            const step = event.shiftKey ? 40 : 10;
            position = place(rect.left + direction[0] * step, rect.top + direction[1] * step);
            save();
            event.preventDefault();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && drag) { finish(true); event.preventDefault(); }
        });
        function keepVisible() { if (position && !drag) place(position.x, position.y); }
        window.addEventListener('resize', keepVisible);
        if ('ResizeObserver' in window) new ResizeObserver(keepVisible).observe(panel);
    }
})();
