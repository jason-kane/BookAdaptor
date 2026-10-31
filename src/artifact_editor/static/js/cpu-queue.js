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
    function animationThumbnail(task, message = task.message || '') {
        if (!['chapter_missing_animations', 'textoviva_regenerate_animation',
              'textoviva_missing_animation'].includes(task.action)) return '';
        if (task.thumbnail_url) return task.thumbnail_url;
        const index = task.image_index
            ?? message.match(/(?:image|shot)\s+(\d+)/i)?.[1]
            ?? task.thumbnail_image_index;
        if (index === undefined || index === null || !/^\d+$/.test(String(index))) return '';
        const chapter = task.chapter || {};
        const author = task.author || chapter.author;
        const title = task.title || chapter.title;
        const number = task.chapter_number || chapter.chapter_number;
        const language = task.language || chapter.language;
        if (!author || !title || !number || !language) return '';
        return `/library/${encodeURIComponent(author)}/${encodeURIComponent(title)}/${encodeURIComponent(number)}/${encodeURIComponent(language)}/images/128/${index}.png`;
    }
    function row(task, state) {
        const item = document.createElement('div');
        item.className = 'cpu-queue-row';
        item.dataset.jobId = task.job_id;
        item.dataset.state = task.state || state;
        item.dataset.retryable = String(Boolean(task.retryable));
        item.dataset.cancellable = String(Boolean(task.cancellable));
        const thumbnailUrl = animationThumbnail(task);
        if (thumbnailUrl) {
            item.classList.add('has-animation-thumbnail');
            const thumbnail = document.createElement('img');
            thumbnail.className = 'cpu-queue-thumbnail';
            thumbnail.src = thumbnailUrl;
            thumbnail.alt = `Image ${task.image_index ?? task.message?.match(/(?:image|shot)\s+(\d+)/i)?.[1] ?? task.thumbnail_image_index ?? ''}`;
            thumbnail.loading = 'lazy';
            item.append(thumbnail);
        }
        const label = document.createElement('p');
        label.className = 'cpu-queue-label';
        label.textContent = task.label || [task.title, task.chapter_number && `Chapter ${task.chapter_number}`].filter(Boolean).join(' · ') || 'Chapter render';
        label.title = label.textContent;
        item.append(label);
        const message = document.createElement('p');
        message.className = 'cpu-queue-message';
        message.textContent = window.TaskTiming?.cleanMessage(task.message) || (task.worker_id ? `Worker ${task.worker_id}` : state);
        message.title = message.textContent;
        item.append(message);
        if (task.cancellable) {
            const cancel = document.createElement('wa-button');
            cancel.setAttribute('size', 'small');
            cancel.setAttribute('appearance', 'plain');
            cancel.setAttribute('variant', 'danger');
            cancel.className = 'cpu-queue-cancel';
            cancel.dataset.cpuCancel = '';
            cancel.innerHTML = '<wa-icon name="trash" aria-hidden="true"></wa-icon>';
            cancel.setAttribute('aria-label', `Cancel ${label.textContent}`);
            cancel.title = 'Cancel this task';
            cancel.addEventListener('click', () => {
                if (cancel.disabled) return;
                const running = item.dataset.state !== 'queued';
                if (running && !window.confirm(
                    `Cancel running task “${label.textContent}”?\n\nThis stops it immediately and cancels queued child work. Partial output files may remain. A GPU request already running may finish.`
                )) return;
                cancel.disabled = true;
                cancel.title = 'Cancelling task';
                document.dispatchEvent(new CustomEvent('cpu:cancel-send', {
                    detail: {job_id: task.job_id, allow_running: running},
                }));
            });
            item.append(cancel);
        }
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
        if (task.retryable && ['failed', 'complete', 'cancelled'].includes(item.dataset.state)) {
            const retry = document.createElement('wa-button');
            retry.setAttribute('size', 'small');
            retry.setAttribute('appearance', 'plain');
            retry.className = 'cpu-queue-retry';
            retry.dataset.cpuRetry = '';
            retry.setAttribute('aria-label', `Retry ${label.textContent}`);
            retry.title = 'Run this task again with the same inputs';
            retry.textContent = 'Retry';
            retry.addEventListener('click', () => {
                if (retry.disabled) return;
                retry.disabled = true;
                retry.title = 'Queueing a retry…';
                document.dispatchEvent(new CustomEvent('cpu:retry-request', {
                    detail: {job_id: task.job_id},
                }));
            });
            item.append(retry);
        }
        if (!['failed', 'cancelled', 'cancelling'].includes(item.dataset.state)
                && (task.timing || task.percent !== undefined || task.state === 'running')) {
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
    function updateRow(existing, task, state) {
        const currentState = task.state || state;
        const retryable = String(Boolean(task.retryable));
        if (existing.dataset.state !== currentState || existing.dataset.retryable !== retryable
                || existing.dataset.cancellable !== String(Boolean(task.cancellable))) {
            return row(task, state);
        }
        const label = task.label || [task.title, task.chapter_number && `Chapter ${task.chapter_number}`]
            .filter(Boolean).join(' · ') || 'Chapter render';
        const message = window.TaskTiming?.cleanMessage(task.message)
            || (task.worker_id ? `Worker ${task.worker_id}` : state);
        const labelElement = existing.querySelector('.cpu-queue-label');
        const messageElement = existing.querySelector('.cpu-queue-message');
        const thumbnailUrl = animationThumbnail(task);
        let thumbnail = existing.querySelector('.cpu-queue-thumbnail');
        if (thumbnailUrl && !thumbnail) {
            thumbnail = document.createElement('img');
            thumbnail.className = 'cpu-queue-thumbnail';
            thumbnail.loading = 'lazy';
            existing.prepend(thumbnail);
        } else if (!thumbnailUrl && thumbnail) {
            thumbnail.remove();
            existing.classList.remove('has-animation-thumbnail');
        }
        if (thumbnailUrl && thumbnail) {
            if (thumbnail.getAttribute('src') !== thumbnailUrl) thumbnail.src = thumbnailUrl;
            thumbnail.alt = `Image ${task.image_index ?? task.message?.match(/(?:image|shot)\s+(\d+)/i)?.[1] ?? task.thumbnail_image_index ?? ''}`;
            existing.classList.add('has-animation-thumbnail');
        }
        if (labelElement) {
            labelElement.textContent = label;
            labelElement.title = label;
        }
        if (messageElement) {
            messageElement.textContent = message;
            messageElement.title = message;
        }
        const progress = existing.querySelector('wa-progress-bar');
        if (progress && window.TaskTiming) {
            window.TaskTiming.bind(progress, task, existing.querySelector('.task-timing-label'), {
                statsElement: existing.querySelector('.task-timing-stats'),
            });
        }
        return existing;
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
            const container = document.getElementById(`cpu-queue-${name}`);
            const previous = new Map([...container.children]
                .filter(child => child.dataset.jobId).map(child => [child.dataset.jobId, child]));
            const incoming = tasks || [];
            const currentIds = new Set(incoming.map(task => task.job_id));
            const rows = incoming.map(task => {
                const existing = previous.get(task.job_id);
                return existing && !existing.classList.contains('cpu-queue-row-fading')
                    ? updateRow(existing, task, state) : row(task, state);
            });
            if (name === 'failed') {
                for (const [jobId, existing] of previous) {
                    if (currentIds.has(jobId)) continue;
                    rows.push(existing);
                    if (existing.classList.contains('cpu-queue-row-fading')) continue;
                    existing.classList.add('cpu-queue-row-fading');
                    existing.setAttribute('aria-label', `${existing.getAttribute('aria-label') || 'Failed task'} — dismissed`);
                    setTimeout(() => existing.remove(), 1200);
                }
            }
            container.replaceChildren(...rows);
        }
    });
    document.addEventListener('cpu:retry-result', ({detail: result}) => {
        const row = [...panel.querySelectorAll('.cpu-queue-row[data-job-id]')]
            .find(item => item.dataset.jobId === result.job_id);
        const button = row?.querySelector('[data-cpu-retry]');
        if (!button) return;
        if (result.state === 'cpu_retry_queued') {
            button.textContent = 'Queued';
            button.title = 'Retry queued with the same inputs';
            return;
        }
        button.disabled = false;
        button.title = result.message || 'Retry this task';
    });
    document.addEventListener('cpu:retry-request', ({detail}) => {
        document.dispatchEvent(new CustomEvent('cpu:retry-send', {detail}));
    });
    document.addEventListener('cpu:cancel-result', ({detail: result}) => {
        const item = [...panel.querySelectorAll('.cpu-queue-row[data-job-id]')]
            .find(row => row.dataset.jobId === result.job_id);
        const button = item?.querySelector('[data-cpu-cancel]');
        if (!button) return;
        if (result.state === 'cpu_cancel_rejected') {
            button.disabled = false;
            button.title = result.message || 'Cancellation failed';
            const message = item.querySelector('.cpu-queue-message');
            if (message) message.textContent = button.title;
        } else {
            button.title = result.state === 'cpu_cancelled' ? 'Task cancelled' : 'Cancelling task';
        }
    });
})();
