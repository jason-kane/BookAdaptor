(() => {
    const panel = document.getElementById('cpu-job-queue');
    if (!panel) return;
    const toggle = document.getElementById('cpu-queue-toggle');
    toggle.addEventListener('click', () => {
        const minimized = panel.classList.toggle('is-minimized');
        const label = `${minimized ? 'Expand' : 'Minimize'} CPU queue`;
        toggle.setAttribute('aria-expanded', String(!minimized));
        toggle.setAttribute('aria-label', label);
        toggle.title = label;
    });
    function row(task, state) {
        const item = document.createElement('div');
        item.className = 'cpu-queue-row';
        item.dataset.jobId = task.job_id;
        item.dataset.state = task.state || state;
        const label = document.createElement('p');
        label.textContent = task.label || [task.title, task.chapter_number && `Chapter ${task.chapter_number}`].filter(Boolean).join(' · ') || 'Chapter render';
        label.title = label.textContent;
        item.append(label);
        const message = document.createElement('p');
        message.textContent = window.TaskTiming?.cleanMessage(task.message) || (task.worker_id ? `Worker ${task.worker_id}` : state);
        message.title = message.textContent;
        item.append(message);
        if (item.dataset.state === 'failed') {
            const copy = document.createElement('wa-button');
            copy.setAttribute('size', 'small');
            copy.setAttribute('appearance', 'plain');
            copy.className = 'cpu-queue-copy';
            copy.setAttribute('aria-label', `Copy debug information for ${label.textContent}`);
            copy.title = 'Copy debug information';
            copy.innerHTML = '<wa-icon name="copy" aria-hidden="true"></wa-icon>';
            copy.addEventListener('click', async () => {
                const chapter = task.chapter || {};
                const chapterLabel = [chapter.author, chapter.title,
                    chapter.chapter_number && `Chapter ${chapter.chapter_number}`, chapter.language]
                    .filter(Boolean).join(' / ');
                const details = [
                    ['Task', task.label || label.textContent],
                    ['State', task.state || state],
                    ['Action', task.action],
                    ['Job ID', task.job_id],
                    ['Batch ID', task.batch_id],
                    ['Parent Job ID', task.parent_job_id],
                    ['Worker ID', task.worker_id],
                    ['Chapter', chapterLabel || undefined],
                    ['Started at', task.started_at ? new Date(Number(task.started_at) * 1000).toISOString() : undefined],
                    ['Duration seconds', task.duration_seconds],
                    ['Timing', task.timing ? JSON.stringify(task.timing) : undefined],
                    ['Timing key', task.timing_key],
                    ['Error', task.message],
                    ['Debug details', task.debug_details],
                ].filter(([, value]) => value !== undefined && value !== null && value !== '');
                const text = `CPU task debug information\n${details.map(([key, value]) => `${key}: ${value}`).join('\n')}`;
                try {
                    if (navigator.clipboard?.writeText) {
                        await navigator.clipboard.writeText(text);
                    } else {
                        const field = document.createElement('textarea');
                        field.value = text;
                        field.setAttribute('readonly', '');
                        field.style.position = 'fixed';
                        field.style.opacity = '0';
                        document.body.append(field);
                        field.select();
                        const copied = document.execCommand('copy');
                        field.remove();
                        if (!copied) throw new Error('Clipboard copy was rejected');
                    }
                    copy.title = 'Debug information copied';
                } catch (_) {
                    copy.title = 'Copy failed';
                }
                setTimeout(() => { copy.title = 'Copy debug information'; }, 1800);
            });
            item.append(copy);
        }
        if (task.timing || task.percent !== undefined || task.state === 'running') {
            const progress = document.createElement('wa-progress-bar');
            progress.setAttribute('aria-label', `${label.textContent} progress`);
            const eta = document.createElement('span');
            eta.className = 'task-timing-label';
            const stats = document.createElement('small');
            stats.className = 'task-timing-stats';
            item.append(progress, eta, stats);
            window.TaskTiming?.bind(progress, task, eta, {statsElement: stats});
        }
        return item;
    }
    document.addEventListener('cpu:queue-plan', ({detail: plan}) => {
        const renders = Number(plan.render_count) || 0;
        const count = document.getElementById('cpu-queue-count');
        count.textContent = plan.running_count + plan.queued_count + renders || '';
        count.title = `${count.textContent || 0} active or queued tasks`;
        const summary = document.getElementById('cpu-queue-summary');
        summary.textContent = plan.workers
            ? `${plan.running_count}/${plan.workers} workers active · ${plan.queued_count} queued${renders ? ` · ${renders} video ${renders === 1 ? 'task' : 'tasks'}` : ''}`
            : 'CPU workers are stopped';
        summary.title = summary.textContent;
        for (const [name, tasks, state] of [
            ['renders', plan.renders, 'Preparing video'], ['running', plan.running, 'running'],
            ['pending', plan.jobs, 'queued'], ['failed', plan.failed, 'failed'],
            ['completed', plan.completed, 'complete'],
        ]) {
            document.getElementById(`cpu-queue-${name}`).replaceChildren(...(tasks || []).map(task => row(task, state)));
        }
    });
})();
