(() => {
    const jobs = new Map();
    const bindings = new Map();
    const terminal = new Set(['complete', 'loaded', 'failed', 'cancelled']);
    const numeric = value => value === null || value === undefined || value === '' ? null
        : Number.isFinite(Number(value)) ? Number(value) : null;

    function duration(value) {
        if (numeric(value) === null) return 'unknown';
        if (Number(value) > 0 && Number(value) < 1) return `${Math.max(1, Math.round(Number(value) * 1000))}ms`;
        const seconds = Math.max(0, Math.ceil(Number(value)));
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m${seconds % 60 ? ` ${seconds % 60}s` : ''}`;
        return `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ''}`;
    }

    function cleanMessage(message) {
        return String(message || '').replace(/\(?\b\d+(?:\.\d+)?\s*%\)?\s*[—·-]?\s*/g, '').trim();
    }

    function remember(job) {
        if (!job?.job_id) return job;
        const previous = jobs.get(job.job_id) || {};
        const restarted = (job.queue_started_at && previous.queue_started_at && job.queue_started_at !== previous.queue_started_at)
            || (terminal.has(previous.state) && job.state && !terminal.has(job.state));
        const merged = {...previous, ...job, timing: job.timing || (restarted ? null : previous.timing)};
        merged._timingAt = job.timing && job.timing.as_of !== previous.timing?.as_of
            ? Date.now() : (restarted ? Date.now() : previous._timingAt || Date.now());
        jobs.set(job.job_id, merged);
        if (jobs.size > 2048) jobs.delete(jobs.keys().next().value);
        return merged;
    }

    function describe(job, age = 0, compact = false, dependency = true) {
        const timing = job.timing || {};
        const state = job.state || 'running';
        const stopped = terminal.has(state);
        const elapsed = numeric(timing.elapsed_seconds);
        const seconds = elapsed === null ? null : elapsed + (stopped || state === 'queued' ? 0 : age);
        const average = numeric(timing.avg_seconds);
        const samples = (timing.samples_seconds || []).filter(value => numeric(value) !== null);
        let remaining = null;
        let value = null;
        let label;
        if (stopped) {
            label = state === 'cancelled' ? 'Cancelled' : state === 'failed' ? 'Failed' : 'Done';
            const measured = numeric(job.duration_seconds) ?? seconds;
            if (measured !== null) label += ` · ${duration(measured)}`;
            value = ['failed', 'cancelled'].includes(state) ? null : 100;
        } else if (state === 'queued') {
            const finish = numeric(timing.eta_seconds);
            remaining = finish === null ? null : finish - age;
            label = remaining !== null && remaining > 0
                ? (compact ? `~${duration(remaining)} ETA` : `Queued · ~${duration(remaining)} to finish`)
                : compact ? 'Queued' : `Queued · ${average === null ? 'learning duration' : `~${duration(average)} run time`}`;
        } else if (average !== null && seconds !== null) {
            if (seconds < average) remaining = average - seconds;
            else {
                const survivors = samples.filter(value => value > seconds);
                if (survivors.length) remaining = survivors.reduce((sum, value) => sum + value, 0) / survivors.length - seconds;
            }
            value = Math.min(98, seconds / Math.max(average, 0.001) * 100);
            label = remaining !== null ? `~${duration(remaining)} left`
                : compact ? 'Over time' : `Longer than observed · ${duration(seconds)} elapsed`;
        } else {
            label = compact ? 'Learning' : `Learning duration${seconds === null ? '' : ` · ${duration(seconds)} elapsed`}`;
        }
        const frameProgress = String(job.message || '').match(/Rendering frames\s*\((\d+)\/(\d+)\)/i);
        if (!stopped && state !== 'queued' && frameProgress) {
            const completed = Number(frameProgress[1]);
            const total = Number(frameProgress[2]);
            if (total > 0) value = Math.min(100, completed / total * 100);
            if (total > 0 && completed > 0 && seconds !== null) {
                remaining = seconds * (total - completed) / completed;
                label = `Best Guess: ~${duration(remaining)} remaining`;
            } else {
                label = 'Best Guess: estimating from completed frames';
            }
        }
        const message = String(job.message || '');
        const audioProgress = message.match(/Generated audio for\s+(\d+)\s+of\s+(\d+)\s+phrases?/i)
            || message.match(/Generating audio:\s*phrase\s+(\d+)\s+of\s+(\d+)/i);
        if (!stopped && state !== 'queued' && audioProgress) {
            const reportedPercent = numeric(job.percent);
            const completed = Number(audioProgress[1]);
            const total = Number(audioProgress[2]);
            value = reportedPercent !== null
                ? Math.max(0, Math.min(100, reportedPercent))
                : total > 0 ? Math.max(0, Math.min(100, completed / total * 100)) : null;
        }
        if (!stopped && state !== 'queued' && average === null && dependency && job.job_id) {
            const child = [...jobs.values()].find(item => item.parent_job_id === job.job_id && !terminal.has(item.state));
            if (child) {
                const childInfo = describe(child, Math.max(0, (Date.now() - child._timingAt) / 1000), true, false);
                label = `GPU · ${childInfo.label}`;
            }
        }
        const count = Number(timing.count) || 0;
        const stats = count ? `Min ${duration(timing.min_seconds)} · Avg ${duration(average)} · Max ${duration(timing.max_seconds)} · ${count} runs`
            : 'No comparable completed tasks yet';
        const explanation = [label, seconds === null ? '' : `Elapsed ${duration(seconds)}`, stats,
            count && timing.unit !== 'task' ? 'Durations scaled to this workload' : ''].filter(Boolean).join('\n');
        return {label, title: explanation, stats, value, remaining};
    }

    function paint(binding) {
        const {bar, label, job, options, receivedAt} = binding;
        const info = describe(job, Math.max(0, (Date.now() - receivedAt) / 1000), Boolean(options.compact));
        if (info.value === null) {
            bar?.removeAttribute('value');
            bar?.setAttribute('indeterminate', '');
        } else {
            bar?.removeAttribute('indeterminate');
            bar?.setAttribute('value', String(info.value));
        }
        if (bar) {
            bar.setAttribute('aria-valuetext', info.label);
            bar.title = info.title;
        }
        if (label) {
            label.textContent = `${options.prefix ? cleanMessage(options.prefix) + ' · ' : ''}${info.label}`;
            label.setAttribute('aria-label', label.textContent);
            label.title = info.title;
        }
        if (options.statsElement) options.statsElement.textContent = info.stats;
    }

    function bind(bar, job, label = null, options = {}) {
        job = remember(job) || {};
        const owner = bar || label;
        if (!owner || owner.closest?.('[data-task-timing-ignore]')) return;
        if (bar && label) bindings.delete(label);
        if (bar) bar.dataset.timedProgress = '';
        const binding = {bar, label, job, options, receivedAt: job._timingAt || Date.now()};
        bindings.set(owner, binding);
        paint(binding);
    }

    function scan(root = document) {
        const bars = [...(root.matches?.('wa-progress-bar, wa-progress-ring') ? [root] : []),
            ...(root.querySelectorAll?.('wa-progress-bar, wa-progress-ring') || [])];
        for (const bar of bars) {
            if (bar.closest('[data-task-timing-ignore]') || bindings.has(bar)) continue;
            let job = {};
            try { job = JSON.parse(bar.dataset.taskTiming || '{}'); } catch (_) { /* uninitialized progress */ }
            const parent = bar.closest('[data-task-progress], [data-video-job], [id^="video-progress-"]');
            if (!job.job_id && parent) job = {job_id: parent.dataset.videoJob || parent.id.replace('video-progress-', ''),
                state: parent.dataset.videoState || 'queued'};
            let label = parent?.querySelector('[data-task-eta]');
            if (!label && bar.nextElementSibling?.matches('.task-timing-label')) label = bar.nextElementSibling;
            if (!label) {
                label = document.createElement('span');
                label.className = 'task-timing-label';
                label.dataset.taskEta = '';
                bar.after(label);
            }
            bind(bar, job, label, {statsElement: parent?.querySelector('[data-task-timing-stats]')});
        }
    }

    window.TaskTiming = {bind, describe, duration, cleanMessage, remember, enrich: job => remember(job)};
    const start = () => {
        scan();
        new MutationObserver(records => {
            for (const record of records) {
                if (record.type === 'attributes') {
                    bindings.delete(record.target);
                    scan(record.target);
                }
                for (const node of record.addedNodes || []) if (node.nodeType === 1) scan(node);
            }
        }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['data-task-timing']});
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
    window.setInterval(() => {
        for (const [owner, binding] of bindings) {
            if (!owner.isConnected) bindings.delete(owner);
            else paint(binding);
        }
    }, 1000);
})();
