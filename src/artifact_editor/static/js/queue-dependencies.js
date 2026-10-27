(() => {
    const cpu = document.getElementById('cpu-job-queue');
    const gpu = document.getElementById('styler-job-queue');
    const overlay = document.getElementById('queue-dependency-lines');
    if (!cpu || !gpu || !overlay) return;

    const namespace = 'http://www.w3.org/2000/svg';
    const activeStates = new Set(['queued', 'running', 'preparing', 'clearing_vram', 'waiting']);
    const connections = new Map();
    let scheduled = false;

    function visibleAnchor(row, edge) {
        if (!row.getClientRects().length) return null;
        const rect = row.getBoundingClientRect();
        const point = {x: edge === 'right' ? rect.right : rect.left,
            y: rect.top + rect.height / 2};
        if (rect.width <= 0 || rect.height <= 0 || point.x < 0 || point.x > window.innerWidth
                || point.y < 0 || point.y > window.innerHeight) return null;
        if (getComputedStyle(row).visibility !== 'visible') return null;
        // A row can have a layout rectangle while being clipped by a queue's
        // scrolling or overflow container. Never connect to an invisible row.
        for (let ancestor = row.parentElement; ancestor; ancestor = ancestor.parentElement) {
            const style = getComputedStyle(ancestor);
            const bounds = ancestor.getBoundingClientRect();
            if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX)
                    && (point.x < bounds.left || point.x > bounds.right)) return null;
            if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowY)
                    && (point.y < bounds.top || point.y > bounds.bottom)) return null;
        }
        return point;
    }

    function createConnection() {
        const group = document.createElementNS(namespace, 'g');
        const path = document.createElementNS(namespace, 'path');
        path.classList.add('queue-dependency-path');
        const start = document.createElementNS(namespace, 'circle');
        const end = document.createElementNS(namespace, 'circle');
        start.setAttribute('r', '3');
        end.setAttribute('r', '3');
        group.append(path, start, end);
        overlay.append(group);
        return {group, path, start, end, geometry: null};
    }

    function draw() {
        scheduled = false;
        const viewport = `0 0 ${window.innerWidth} ${window.innerHeight}`;
        if (overlay.getAttribute('viewBox') !== viewport) overlay.setAttribute('viewBox', viewport);
        const visible = new Set();
        if (!cpu.classList.contains('is-minimized') && !gpu.classList.contains('is-minimized')) {
            const parents = new Map([...cpu.querySelectorAll('.cpu-queue-row[data-job-id]')]
                .filter(row => activeStates.has(row.dataset.state) && row.dataset.state !== 'queued')
                .map(row => [row.dataset.jobId, row]));
            for (const target of gpu.querySelectorAll('.styler-queue-task[data-parent-job-id]')) {
                if (!activeStates.has(target.dataset.state)) continue;
                const source = parents.get(target.dataset.parentJobId);
                if (!source) continue;
                const from = visibleAnchor(source, 'right');
                const to = visibleAnchor(target, 'left');
                if (!from || !to) continue;
                const key = `${source.dataset.jobId}:${target.dataset.jobId}`;
                visible.add(key);
                let connection = connections.get(key);
                if (!connection) {
                    connection = createConnection();
                    connection.group.dataset.cpuJobId = source.dataset.jobId;
                    connection.group.dataset.gpuJobId = target.dataset.jobId;
                    connections.set(key, connection);
                }
                const geometry = `${from.x},${from.y},${to.x},${to.y}`;
                if (geometry === connection.geometry) continue;
                connection.geometry = geometry;
                const middle = (from.x + to.x) / 2;
                connection.path.setAttribute('d',
                    `M ${from.x} ${from.y} C ${middle} ${from.y}, ${middle} ${to.y}, ${to.x} ${to.y}`);
                connection.start.setAttribute('cx', String(from.x));
                connection.start.setAttribute('cy', String(from.y));
                connection.end.setAttribute('cx', String(to.x));
                connection.end.setAttribute('cy', String(to.y));
            }
        }
        for (const [key, connection] of connections) {
            if (visible.has(key)) continue;
            connection.group.remove();
            connections.delete(key);
        }
        // Web Animations move GPU rows without DOM mutations. Follow their
        // actual screen positions while a dependency is visible; sleep otherwise.
        if (connections.size) schedule();
    }

    function schedule() {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(draw);
    }

    const observer = new MutationObserver(schedule);
    for (const panel of [cpu, gpu]) {
        observer.observe(panel, {childList: true, subtree: true, attributes: true,
            attributeFilter: ['class', 'style', 'hidden', 'data-job-id', 'data-parent-job-id', 'data-state']});
    }
    if ('ResizeObserver' in window) {
        const resizeObserver = new ResizeObserver(schedule);
        resizeObserver.observe(cpu);
        resizeObserver.observe(gpu);
    }
    window.addEventListener('resize', schedule);
    document.addEventListener('scroll', schedule, true);
    schedule();
})();
