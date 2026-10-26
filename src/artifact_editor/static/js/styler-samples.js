(() => {
    /*
     * # Styler sample page client
     *
     * This script keeps the style sample page live. It requests missing sample
     * images, reflects asynchronous generation and tagging, renders the job
     * queue, updates scan/retag controls, and refills paged cards after changes.
     * Server-rendered templates own initial content; this client owns live
     * updates and user actions while the page remains open.
     *
     * ## Consumers and contracts
     *
     * - `src/styler/templates/styles.html` and
     *   `src/styler/templates/styles-tabgroups.html` load this classic script
     *   and provide its control IDs, data attributes, and websocket URL.
     * - `src/websocket/server.py` exchanges JSON events and commands. Keep the
     *   server's snake_case field names at this boundary.
     * - `styler-image-viewer.js` consumes `styler:sample-status` and
     *   `styler:sample-complete`.
     * - `styler-categories.js` consumes category events and `stockpile-refresh`.
     *
     * ## Why this client exists
     *
     * Generation, tagging, and scans can outlive an individual request. This
     * client turns those asynchronous server events into incremental page
     * updates, so users can follow progress and act on results without losing
     * their place or refreshing the whole page. The server remains authoritative
     * for job execution and queue state.
     *
     * ## Design guide
     *
     * This remains a classic script because the templates pass configuration
     * through `document.currentScript`. State and helpers are organized by
     * domain, while the websocket listener acts as the boundary between server
     * events and those domains. Keep DOM writes close to their renderer and
     * preserve the events above when changing internal details.
     *
     * The page uses browser globals (`document`, `window`, `WebSocket`, `fetch`,
     * `MutationObserver`, `customElements`) and optionally integrates with
     * htmx and Web Awesome. It deliberately creates no global names.
     *
     * `TODO(MONITOR)` marks production distributions that are currently unknown.
     * They describe future aggregate instrumentation; no telemetry is emitted
     * here. Never collect raw prompts, IDs, filenames, style names, or tag text.
     */

    // Configuration and policy
    // The websocket endpoint belongs to the template so this file can serve
    // both style-page variants without embedding deployment paths.
    const endpoint = document.currentScript.dataset.stylerWebsocket;

    // These values are product policy rather than protocol facts. Centralizing
    // them makes each timing and display tradeoff explicit and easy to tune.
    const settings = Object.freeze({
        maintenanceModes: Object.freeze(['off', 'walk', 'run']), // Slider values accepted by the server.
        maintenanceLabels: Object.freeze(['Off', 'Walk', 'Run']), // User-facing labels in matching slider order.
        initialMaintenanceMode: 'walk', // Start with gentle background work until the server reports its mode.
        queue: Object.freeze({
            successRetentionMs: 30_000, // Briefly show successful work without letting history dominate.
            failureRetentionMs: 300_000, // Keep failures available longer so users can inspect or retry them.
            successHistoryLimit: 5, // Bound successful records; failed records have a separate expiry.
            measuredHistoryLimit: 5, // Limit history measured when calculating the visible queue layout.
            visibleHistoryLimit: 3, // Reserve at most three slots for recent history.
            initialLoadingMs: 700, // Keep the loading affordance visible through first layout.
            rowAnimationMs: 350, // Short positional animation for queue reorderings.
            minimumCapacityRows: 10, // Avoid hiding small queues when the panel is short or minimized.
            fallbackRowHeight: 36, // Estimate capacity before a queue row can be measured.
            expiringRowCleanupMs: 1_600, // Remove animation ghosts if CSS animationend never arrives.
        }),
        navigation: Object.freeze({
            cardWaitMs: 10_000, // Bound waiting for htmx to load a requested style card.
            focusHighlightMs: 1_500, // Keep the destination highlight long enough to find it.
            expandedCardWaitMs: 5_000, // Bound waiting for a newly expanded sample panel.
        }),
        stylePage: Object.freeze({
            refillDebounceMs: 80, // Coalesce bursts of card mutations into one refill.
            retryBaseMs: 500, // Initial delay for a failed page refill.
            retryMaximumMs: 10_000, // Cap exponential refill retry delay.
            retryExponentLimit: 5, // Bound exponent growth before applying the retry cap.
            lostEntryDissolveMs: 750, // Match the LOST card removal transition.
            defaultPageSize: 5, // Fallback when a LOST summary lacks template metadata.
        }),
        samples: Object.freeze({
            displayedTagLimit: 8, // Keep generated tag clouds compact while preserving all tags in server state.
            reconnectDelayMs: 2_000, // Retry a closed websocket after a short pause.
        }),
        workerReadyNoticeMs: 3_000, // Let the worker-ready confirmation be seen before clearing it.
    });

    // Shared client state. These maps preserve server identities and transient
    // UI state; DOM nodes are weakly held whenever they should not prevent GC.
    let sent = new WeakSet();
    const targets = new Map();
    const sampleProgressMax = new Map();
    const queueJobs = new Map();
    const queueAnimations = new WeakMap();
    const queueProgressMax = new Map();
    let queueOrder = [];
    let runningJob = null;
    let serverOverflowCount = 0;
    let serverQueueCount = 0;
    let queueInitialLoad = true;
    let queueInitialTimer = null;
    let queueHasInitialPlan = false;
    const completedQueueJobs = new Map();
    const queueHistoryTimers = new Map();
    const failedQueueJobs = new Map();
    const queueFailureStates = new Set([
        'failed',
        'tag_failed',
        'tag_abyss',
        'stockpile_failed',
        'styles_scan_image_failed',
        'style_description_failed',
        'style_duplicate_scan_failed',
    ]);
    const queueCompletionStates = new Set([
        'complete',
        'prompt_tagged',
        'stockpile_complete',
        'styles_scan_image_complete',
        'style_description_complete',
        'style_duplicate_scan_complete',
    ]);
    // Regeneration scroll restoration
    const regenerateScrollPositions = new WeakMap();
    // Capture the current viewport position before a regeneration request.
    function scrollPosition() {
        return {left: window.scrollX, top: window.scrollY};
    }

    document.addEventListener('pointerdown', event => {
        const button = event.target.closest?.('[data-regenerate]');
        if (button) regenerateScrollPositions.set(button, scrollPosition());
    }, true);
    document.addEventListener('htmx:beforeRequest', event => {
        const button = event.detail?.elt;
        if (button?.matches?.('[data-regenerate]') && !regenerateScrollPositions.has(button)) {
            regenerateScrollPositions.set(button, scrollPosition());
        }
    });
    document.addEventListener('htmx:afterSettle', event => {
        const button = event.detail?.elt;
        const position = button && regenerateScrollPositions.get(button);
        if (!position) return;
        regenerateScrollPositions.delete(button);
        requestAnimationFrame(() => window.scrollTo({left: position.left, top: position.top, behavior: 'instant'}));
    });
    // Background maintenance controls
    // TODO(MONITOR): observe mode frequency and rejected mode values; this set
    // is the currently assumed production domain for background maintenance.
    const maintenanceModes = settings.maintenanceModes;
    const maintenanceLabels = settings.maintenanceLabels;
    let maintenanceMode = settings.initialMaintenanceMode;
    let pendingMaintenanceMode = null;
    // Render the maintenance-mode slider and its connection state.
    function showMaintenanceMode(connected = socket?.readyState === WebSocket.OPEN) {
        const slider = document.getElementById('styler-maintenance-rate');
        if (!slider) return;
        const mode = pendingMaintenanceMode || maintenanceMode;
        const value = maintenanceModes.indexOf(mode);
        if (Number(slider.value) !== value) slider.value = value;
        slider.setAttribute('value', String(value));
        slider.setAttribute('aria-valuetext', maintenanceLabels[value]);
        slider.disabled = !connected;
        slider.toggleAttribute('disabled', !connected);
        slider.title = mode === 'off'
            ? 'Automatic background additions are off'
            : mode === 'walk'
                ? 'Add background work after 30 idle seconds'
                : 'Add up to two background tasks after 5 idle seconds';
        const control = slider.closest('.styler-queue-rate');
        if (control) control.dataset.mode = mode;
    }

    // Accept a server-confirmed maintenance mode and refresh the control.
    function receiveMaintenanceMode(mode) {
        // TODO(MONITOR): count observed maintenance modes, including values rejected here.
        if (!maintenanceModes.includes(mode)) return;
        maintenanceMode = mode;
        if (pendingMaintenanceMode === mode) pendingMaintenanceMode = null;
        showMaintenanceMode();
    }

    // Send a maintenance-mode change selected by the user.
    function changeMaintenanceMode(event) {
        const slider = event.target.closest?.('#styler-maintenance-rate');
        if (!slider) return;
        if (socket?.readyState !== WebSocket.OPEN) {
            showMaintenanceMode(false);
            return;
        }
        const mode = maintenanceModes[Number(slider.value)];
        if (!mode || mode === (pendingMaintenanceMode || maintenanceMode)) return;
        pendingMaintenanceMode = mode;
        showMaintenanceMode();
        socket.send(JSON.stringify({action: 'maintenance_mode', mode}));
    }

    document.addEventListener('input', changeMaintenanceMode);
    document.addEventListener('change', changeMaintenanceMode);
    customElements.whenDefined('wa-slider').then(() => {
        const slider = document.getElementById('styler-maintenance-rate');
        if (!slider) return;
        slider.valueFormatter = value => maintenanceLabels[Math.round(Number(value))] || '';
        showMaintenanceMode();
    });
    // Queue labels and progress
    // Convert an optional queue progress value to a number.
    function queuePercent(value) {
        // TODO(MONITOR): record numeric validity and min/max of raw progress before UI clamping.
        return value === null || value === undefined || value === '' ? NaN : Number(value);
    }

    // Keep reported progress from moving backward for a job.
    function monotonicQueuePercent(jobId, value) {
        const incoming = queuePercent(value);
        const previous = queueProgressMax.get(jobId);
        if (!Number.isFinite(incoming)) return previous ?? NaN;
        const highest = Math.max(Number.isFinite(previous) ? previous : 0, incoming);
        queueProgressMax.set(jobId, highest);
        return highest;
    }

    // Map internal queue states to short user-facing labels.
    function queueStatusLabel(job) {
        return job.state === 'clearing_vram'
            ? 'Cleaning'
            : job.state === 'preparing'
                ? 'Preparing'
                : job.state === 'running'
                    ? 'Running'
                    : job.state === 'complete'
                        ? 'Done'
                        : job.state;
    }

    // Show the initial queue loading indicator.
    function showQueueLoading() {
        queueInitialLoad = true;
        clearTimeout(queueInitialTimer);
        const spinner = document.getElementById('styler-queue-loading');
        if (spinner) spinner.hidden = false;
    }

    document.addEventListener('click', event => {
        const button = event.target.closest('#styler-queue-toggle');
        if (!button) return;
        const queue = document.getElementById('styler-job-queue');
        const minimized = queue.classList.toggle('is-minimized');
        const label = minimized ? 'Expand task queue' : 'Minimize task queue';
        button.setAttribute('aria-expanded', String(!minimized));
        button.setAttribute('aria-label', label);
        button.setAttribute('title', label);
    });
    // Build a readable label for an incoming job event.
    function jobLabel(event) {
        // TODO(MONITOR): count distinct action, job_type, and engine categories; avoid filenames and tags.
        const type = event.label || event.job_type || (event.action === 'styler_tag_sample' ? 'Image tags'
            : event.action === 'styler_sample' ? 'Sample image'
            : event.action === 'styler_scan_image_tags' ? 'Tag normalization'
            : event.action === 'styler_style_description' ? 'Historical description'
            : event.action === 'styler_style_duplicate_scan' ? 'Style duplicate scan'
            : event.action === 'stockpile_image_metadata' ? 'Stockpile tags'
            : event.action?.replaceAll('_', ' ') || event.message || 'GPU job');
        const subject = event.style_tag?.replaceAll('_', ' ') || event.filename?.split('/').pop();
        const engine = ({zit: 'Z-Image', krea2: 'Krea 2', qwen: 'Qwen 2.1'})[event.engine];
        return [type, subject, engine].filter(Boolean).join(' · ');
    }

    // Resolve the human-readable type of a queue task.
    function queueTaskType(job) {
        return job.job_type || ({
            styler_tag_sample: 'Image tags',
            styler_sample: 'Sample image',
            styler_scan_image_tags: 'Tag normalization',
            styler_style_description: 'Description',
            styler_style_duplicate_scan: 'Duplicate scan',
            stockpile_image_metadata: 'Stockpile tags',
        })[job.action] || job.action?.replaceAll('_', ' ') || 'Task';
    }

    // Return the icon asset path for a queue task type.
    function queueTaskTypeIcon(type) {
        const icon = ({
            'Image tags': 'tags',
            'Sample image': 'image',
            'Tag normalization': 'wand-magic-sparkles',
            'Historical description': 'book-open',
            'Description': 'book-open',
            'Style duplicate scan': 'copy',
            'Duplicate scan': 'copy',
            'Stockpile tags': 'layer-group',
            'Category grouping': 'diagram-project',
            'Category taxonomy rebuild': 'diagram-project',
        })[type] || 'gears';
        return `/static/fontawesome7/svgs/solid/${icon}.svg`;
    }

    // Build the concise subject and engine name shown in a queue row.
    function queueTaskName(job) {
        const subject = job.style_tag?.replaceAll('_', ' ') || job.filename?.split('/').pop();
        const engine = ({zit: 'Z-Image', krea2: 'Krea 2', qwen: 'Qwen 2.1'})[job.engine];
        return [subject, engine].filter(Boolean).join(' · ') || job.label || job.message || queueTaskType(job);
    }

    // Queue task labels and rows
    // Update a style card with generated history text or its failure.
    function updateStyleHistoryDescription(event) {
        if (event.action !== 'styler_style_description'
                && !['style_description_complete', 'style_description_failed'].includes(event.state)) return;
        const target = [...document.querySelectorAll('[data-style-history-description]')]
            .find(node => node.dataset.styleTag === event.style_tag);
        if (!target) return;
        if (event.description) {
            target.textContent = event.description;
            target.removeAttribute('role');
            target.removeAttribute('aria-live');
        } else if (event.state === 'style_description_failed'
                || (event.action === 'styler_style_description' && event.state === 'failed')) {
            target.textContent = `Historical description failed: ${event.message || 'Generation failed.'}`;
            target.setAttribute('role', 'status');
            target.setAttribute('aria-live', 'polite');
        }
    }

    // Create the DOM structure for one queue task.
    function queueTaskRow(event) {
        const row = document.createElement('div');
        row.className = 'styler-queue-task';
        row.dataset.state = event.state;
        row.dataset.next = String(Boolean(event.isNext));
        row.dataset.jobId = event.job_id;
        row.setAttribute('role', 'group');
        row.setAttribute('aria-label', event.label);
        const type = document.createElement('span');
        type.className = 'styler-queue-task-type';
        const typeName = queueTaskType(event);
        type.title = typeName;
        type.setAttribute('aria-label', typeName);
        const typeIcon = document.createElement('wa-icon');
        typeIcon.setAttribute('src', queueTaskTypeIcon(typeName));
        typeIcon.setAttribute('aria-hidden', 'true');
        type.append(typeIcon);
        const content = document.createElement('div');
        content.className = 'styler-queue-task-content';
        const label = document.createElement('span');
        label.className = 'styler-queue-task-label';
        label.textContent = queueTaskName(event);
        label.title = event.label || label.textContent;
        content.append(label);
        if (['preparing', 'clearing_vram', 'running', 'complete'].includes(event.state)) {
            const progress = document.createElement('wa-progress-bar');
            progress.setAttribute('aria-label', `${event.label} progress`);
            const percent = queuePercent(event.percent);
            if (Number.isFinite(percent)) {
                progress.setAttribute('value', Math.max(0, Math.min(100, percent)));
            } else {
                progress.setAttribute('indeterminate', '');
            }
            content.append(progress);
        }
        const state = document.createElement('span');
        state.className = 'styler-queue-task-state';
        state.textContent = event.isNext ? 'next'
            : event.state === 'running' && Number.isFinite(queuePercent(event.percent))
                ? `${Math.max(0, Math.min(100, queuePercent(event.percent)))}%` : queueStatusLabel(event);
        const targetSlot = document.createElement('span');
        targetSlot.className = 'styler-queue-target-slot';
        const otherSlot = document.createElement('span');
        otherSlot.className = 'styler-queue-other-slot';
        row.append(type, content, state, targetSlot, otherSlot);
        return row;
    }

    // Queue target navigation
    // Find the style card associated with a job.
    function styleCardForJob(job) {
        if (!job.style_tag) return null;
        return document.getElementById(`card_${job.style_tag}`)
            || [...document.querySelectorAll('[data-style-sample]')]
                .find(item => item.dataset.styleSample === job.style_tag && item.dataset.engine === job.engine)
                ?.closest('.card-overview')
            || null;
    }

    // Wait until a style card appears or the timeout expires.
    function waitForStyleCard(styleTag, timeout = settings.navigation.cardWaitMs) {
        const existing = document.getElementById(`card_${styleTag}`);
        if (existing) return Promise.resolve(existing);
        return new Promise(resolve => {
            let observer;
            const finish = card => {
                observer?.disconnect();
                clearTimeout(timer);
                resolve(card || null);
            };
            observer = new MutationObserver(() => {
                const card = document.getElementById(`card_${styleTag}`);
                if (card) finish(card);
            });
            observer.observe(document.body, {childList: true, subtree: true});
            const timer = window.setTimeout(() => finish(null), timeout);
        });
    }

    // Select the category containing a job target and return its card.
    async function showStyleCard(job, card) {
        const group = document.querySelector('[data-category-tab-group]');
        if (!group) return card;
        let tags = [];
        try {
            if (card?.dataset.styleTags) {
                tags = JSON.parse(card.dataset.styleTags);
            } else {
                const response = await fetch(`/styles/style_categories?style_tag=${encodeURIComponent(job.style_tag)}`);
                if (response.ok) tags = (await response.json()).tags || [];
            }
        } catch (error) {
            console.warn('Unable to find style categories for queue target', error);
        }
        const panel = card?.closest('wa-tab-panel');
        const panelTag = panel?.getAttribute('name')?.replace(/^tag_/, '');
        const tabs = [...group.querySelectorAll('wa-tab[data-style-category]')];
        const validTag = (tags || []).find(tag => tabs.some(tab => tab.dataset.styleCategory === tag));
        const tag = validTag || (panelTag && tabs.some(tab => tab.dataset.styleCategory === panelTag)
            ? panelTag : tabs.some(tab => tab.dataset.styleCategory === 'LOST') ? 'LOST' : null);
        if (!tag) return card;
        const tab = tabs.find(item => item.dataset.styleCategory === tag);
        if (!tab) return card;
        const panelName = tab.getAttribute('panel');
        if (group.active !== panelName) {
            tab.click();
        } else if (!card && window.htmx) {
            htmx.trigger(tab, 'styler-category-load');
        }
        card = document.getElementById(`card_${job.style_tag}`)
            || await waitForStyleCard(job.style_tag);
        return card;
    }

    // Find the page element that corresponds to a queued job.
    function queueDestination(job, card) {
        if (card && job.style_tag && job.engine) {
            const sampleGeneration = job.action === 'styler_sample'
                || job.job_type === 'Sample image' || job.label?.startsWith('Sample image');
            if (sampleGeneration) {
                const panel = [...card.querySelectorAll('[data-sample-panel]')].find(item =>
                    [...item.querySelectorAll('[data-style-sample], [data-image-tags]')]
                        .some(target => target.dataset.engine === job.engine));
                if (panel) return panel;
            } else {
                const tagArea = [...card.querySelectorAll('[data-image-tags][data-style-tag]')]
                    .find(item => item.dataset.styleTag === job.style_tag && item.dataset.engine === job.engine);
                if (tagArea) return tagArea;
            }
        }
        if (job.filename) {
            const image = [...document.querySelectorAll('[data-stockpile-filename]')]
                .find(item => item.dataset.stockpileFilename === job.filename);
            if (image) return image.closest('.wa-frame\\:square') || image;
        }
        return null;
    }

    // Navigate to and highlight the target of a queue row.
    async function focusQueueDestination(row) {
        const job = queueJobs.get(row.dataset.jobId)
            || completedQueueJobs.get(row.dataset.jobId)
            || failedQueueJobs.get(row.dataset.jobId);
        if (!job) return;
        let card = styleCardForJob(job);
        if (job.style_tag && job.engine) {
            card = await showStyleCard(job, card);
        }
        const details = card?.querySelector('wa-details');
        let observer;
        const focusTarget = target => {
            observer?.disconnect();
            target.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'nearest'});
            target.classList.remove('styler-queue-destination-focus');
            void target.offsetWidth;
            target.classList.add('styler-queue-destination-focus');
            window.setTimeout(
                () => target.classList.remove('styler-queue-destination-focus'),
                settings.navigation.focusHighlightMs,
            );
        };
        const target = queueDestination(job, card);
        if (target) {
            if (details && !details.open) details.open = true;
            requestAnimationFrame(() => focusTarget(target));
            return;
        }
        if (details) {
            observer = new MutationObserver(() => {
                const expandedTarget = queueDestination(job, card);
                if (expandedTarget) focusTarget(expandedTarget);
            });
            observer.observe(details, {childList: true, subtree: true});
            if (!details.open) details.open = true;
            window.setTimeout(() => observer?.disconnect(), settings.navigation.expandedCardWaitMs);
            return;
        }
        const fallback = queueDestination(job, null);
        if (fallback) requestAnimationFrame(() => focusTarget(fallback));
    }

    // Promote or demote a queued job when its row is activated.
    function activateQueueRow(row) {
        const job = queueJobs.get(row.dataset.jobId);
        if (job?.state === 'queued') {
            if (socket?.readyState !== WebSocket.OPEN) return;
            const priority = !(job.priority || job.promoted);
            job.promoted = priority;
            job.priority = priority;
            if (!priority) job.isNext = false;
            updateQueueRow(row, job);
            socket.send(JSON.stringify({action: 'queue_promote', job_id: job.job_id, priority}));
            return;
        }
    }

    document.addEventListener('click', event => {
        const row = event.target.closest('.styler-queue-task[data-job-id]');
        if (!row) return;
        const job = queueJobs.get(row.dataset.jobId)
            || completedQueueJobs.get(row.dataset.jobId)
            || failedQueueJobs.get(row.dataset.jobId);
        if (event.target.closest('[data-queue-retry]')) {
            if (socket?.readyState !== WebSocket.OPEN) return;
            const button = row.querySelector('[data-queue-retry]');
            if (button?.disabled) return;
            button.disabled = true;
            button.title = 'Retrying task…';
            socket.send(JSON.stringify({action: 'retry_job', job_id: row.dataset.jobId}));
            return;
        }
        if (event.target.closest('[data-queue-find]')) {
            if (job?.style_tag && job.engine) {
            const pagePrompt = document.querySelector('[data-category-tab-group]')?.dataset.baseStylePrompt
                    || new URLSearchParams(window.location.search).get('base_style_prompt')
                    || 'classic';
                const baseStylePrompt = job.base_style_prompt || pagePrompt;
                const query = new URLSearchParams({
                    style: job.style_tag,
                    engine: job.engine,
                    action: job.action || '',
                    base_style_prompt: baseStylePrompt,
                });
                window.location.assign(`/styles/find_target?${query}`);
                return;
            }
            focusQueueDestination(row);
            return;
        }
        activateQueueRow(row);
    });
    document.addEventListener('keydown', event => {
        if (event.target.closest?.('[data-queue-find]')) return;
        const row = event.target.closest?.('.styler-queue-task[data-job-id]');
        if (row && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            activateQueueRow(row);
        }
    });
    // Queue row actions and updates
    // Refresh the queue count badge from server state.
    function updateQueueCount() {
        const count = document.getElementById('styler-queue-count');
        if (!count) return;
        count.textContent = serverQueueCount > 0 ? String(serverQueueCount) : '';
        count.hidden = serverQueueCount === 0;
    }

    // Create, update, or remove a row’s find-target button.
    function updateQueueFindButton(row, job) {
        const supported = Boolean(job.style_tag && job.engine)
            || Boolean(queueDestination(job, null));
        row.dataset.canFind = String(supported);
        let button = row.querySelector('[data-queue-find]');
        if (!supported) {
            button?.remove();
            return;
        }
        if (!button) {
            button = document.createElement('wa-button');
            button.dataset.queueFind = '';
            button.className = 'styler-queue-find';
            button.setAttribute('type', 'button');
            button.setAttribute('size', 'small');
            button.setAttribute('appearance', 'plain');
            const icon = document.createElement('wa-icon');
            icon.setAttribute('src', '/static/fontawesome7/svgs/solid/location-crosshairs.svg');
            icon.setAttribute('aria-hidden', 'true');
            button.append(icon);
            row.querySelector('.styler-queue-target-slot').append(button);
        }
        button.setAttribute('aria-label', `Find and display ${job.label}`);
        button.title = 'Find and display task';
    }

    // Create, update, or remove a row’s retry button.
    function updateQueueRetryButton(row, job) {
        const supported = ['failed', 'complete'].includes(job.state) && Boolean(job.job_id);
        row.dataset.canRetry = String(supported);
        let button = row.querySelector('[data-queue-retry]');
        if (!supported) {
            button?.remove();
            return;
        }
        if (!button) {
            button = document.createElement('wa-button');
            button.dataset.queueRetry = '';
            button.className = 'styler-queue-retry';
            button.setAttribute('type', 'button');
            button.setAttribute('size', 'small');
            button.setAttribute('appearance', 'plain');
            const icon = document.createElement('wa-icon');
            icon.setAttribute('src', '/static/fontawesome7/svgs/solid/rotate-right.svg');
            icon.setAttribute('aria-hidden', 'true');
            button.append(icon);
            row.querySelector('.styler-queue-other-slot').append(button);
        }
        button.setAttribute('aria-label', `Retry ${job.label}`);
        button.title = `Retry ${job.state === 'complete' ? 'completed' : 'failed'} task`;
    }

    // Create, update, or remove a queued job’s delete button.
    function updateQueueDeleteButton(row, job) {
        const supported = job.state === 'queued' && Boolean(job.job_id);
        row.dataset.canDelete = String(supported);
        let button = row.querySelector('[data-queue-delete]');
        if (!supported) {
            button?.remove();
            return;
        }
        if (!button) {
            button = document.createElement('wa-button');
            button.dataset.queueDelete = '';
            button.className = 'styler-queue-delete';
            button.setAttribute('type', 'button');
            button.setAttribute('size', 'small');
            button.setAttribute('appearance', 'plain');
            const icon = document.createElement('wa-icon');
            icon.setAttribute('src', '/static/fontawesome7/svgs/solid/trash-can.svg');
            icon.setAttribute('aria-hidden', 'true');
            button.append(icon);
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                if (button.disabled || socket?.readyState !== WebSocket.OPEN) return;
                button.disabled = true;
                button.title = 'Removing task…';
                socket.send(JSON.stringify({action: 'queue_delete', job_id: row.dataset.jobId}));
            });
            row.querySelector('.styler-queue-other-slot').append(button);
        }
        button.setAttribute('aria-label', `Delete queued ${job.label}`);
        button.title = 'Delete queued task';
    }

    // Synchronize a queue row’s content and controls with its job.
    function updateQueueRow(row, job) {
        row.dataset.state = job.state;
        row.dataset.next = String(Boolean(job.isNext));
        row.dataset.promoted = String(Boolean(job.promoted));
        row.title = job.state === 'failed' ? job.message || 'Task failed' : '';
        row.setAttribute('aria-label', job.label);
        row.tabIndex = job.state === 'queued' ? 0 : -1;
        updateQueueFindButton(row, job);
        updateQueueDeleteButton(row, job);
        updateQueueRetryButton(row, job);
        const label = row.querySelector('.styler-queue-task-label');
        const name = queueTaskName(job);
        if (label.textContent !== name) label.textContent = name;
        label.title = job.label || name;
        const type = row.querySelector('.styler-queue-task-type');
        const typeName = queueTaskType(job);
        type.title = typeName;
        type.setAttribute('aria-label', typeName);
        type.querySelector('wa-icon')?.setAttribute('src', queueTaskTypeIcon(typeName));
        let progress = row.querySelector('wa-progress-bar');
        if (['preparing', 'clearing_vram', 'running', 'complete'].includes(job.state)) {
            if (!progress) {
                progress = document.createElement('wa-progress-bar');
                row.querySelector('.styler-queue-task-content').append(progress);
            }
            progress.setAttribute('label', `${job.label} progress`);
            const percent = queuePercent(job.percent);
            if (Number.isFinite(percent)) {
                progress.removeAttribute('indeterminate');
                progress.setAttribute('value', Math.max(0, Math.min(100, percent)));
            } else {
                progress.setAttribute('indeterminate', '');
            }
        } else {
            progress?.remove();
        }
        const state = row.querySelector('.styler-queue-task-state');
        const text = job.isNext ? 'next' : job.state === 'running' && Number.isFinite(queuePercent(job.percent))
            ? `${Math.max(0, Math.min(100, queuePercent(job.percent)))}%` : queueStatusLabel(job);
        if (state.textContent !== text) state.textContent = text;
    }

    // Queue layout and animation
    // Allocate visible queue rows across history and pending jobs.
    function queueLayoutCounts(height, rowHeight, summaryHeight, historyCount, runningCount, pendingCount, overflowCount) {
        // TODO(MONITOR): observe panel heights, row heights, and resulting slot
        // counts in aggregate; three history rows are a UX choice without field data.
        // Reserve running + next first, then three completions, then pending work.
        // Calculate capacity with or without a reserved overflow summary.
        const allocate = reserveSummary => {
            const slots = Math.max(0, Math.floor((height - (reserveSummary ? summaryHeight : 0)) / rowHeight));
            const history = Math.min(historyCount, settings.queue.visibleHistoryLimit,
                Math.max(0, slots - runningCount - Math.min(1, pendingCount)));
            const pending = Math.min(pendingCount, Math.max(0, slots - runningCount - history));
            const extraHistory = Math.min(historyCount - history, Math.max(0, slots - runningCount - history - pending));
            return {history: history + extraHistory, pending};
        };
        let layout = allocate(overflowCount > 0);
        if (overflowCount > 0 || layout.pending < pendingCount) layout = allocate(true);
        return {...layout, overflow: overflowCount + pendingCount - layout.pending};
    }

    // Reconcile queue jobs with visible rows and animate layout changes.
    function renderQueue(animate = true, duration = settings.queue.rowAnimationMs) {
        const currentHost = document.getElementById('styler-queue-current');
        const nearHost = document.getElementById('styler-queue-near');
        const overflowHost = document.getElementById('styler-queue-overflow');
        if (!currentHost || !nearHost || !overflowHost) return;
        updateQueueCount();
        const rows = new Map([...document.querySelectorAll('.styler-queue-task[data-job-id]')]
            .map(row => [row.dataset.jobId, row]));
        const previousRects = new Map([...rows].filter(([, row]) => !row.hidden)
            .map(([id, row]) => [id, row.getBoundingClientRect()]));
        for (const row of rows.values()) queueAnimations.get(row)?.cancel();
        const current = runningJob && queueJobs.get(runningJob);
        const running = current && !completedQueueJobs.has(current.job_id)
            && !failedQueueJobs.has(current.job_id) ? current : null;
        const pending = queueOrder.map(id => queueJobs.get(id))
            .filter(job => job && job.state === 'queued'
                && !completedQueueJobs.has(job.job_id)
                && !failedQueueJobs.has(job.job_id));
        const completions = [...completedQueueJobs.values()].sort((a, b) => a.completed_at - b.completed_at);
        const failures = [...failedQueueJobs.values()].sort((a, b) => a.completed_at - b.completed_at);
        const history = [...completions, ...failures].sort((a, b) => a.completed_at - b.completed_at);
        // Reuse rows where possible to preserve component state.
        const getRow = job => {
            let row = rows.get(job.job_id);
            if (!row) {
                row = queueTaskRow(job);
                rows.set(job.job_id, row);
            }
            updateQueueRow(row, job);
            return row;
        };
        // Keep the summary measurable even before it is needed. It never contributes to row height.
        let summary = overflowHost.firstElementChild;
        if (!summary) {
            summary = document.createElement('div');
            summary.className = 'styler-queue-overflow-block';
            overflowHost.append(summary);
        }
        summary.textContent = 'More tasks queued';
        overflowHost.hidden = false;
        const summaryHeight = overflowHost.getBoundingClientRect().height;
        const probe = getRow(running || pending[0] || history.at(-1) || {job_id: '__measure', label: '', state: 'queued'});
        if (!probe.isConnected) nearHost.append(probe);
        probe.hidden = false;
        const rowHeight = probe.getBoundingClientRect().height + (parseFloat(getComputedStyle(probe).marginBottom) || 0);
        const height = currentHost.parentElement.clientHeight;
        // TODO(MONITOR): measure history length and queue panel geometry to check
        // whether the five-item layout measurement cap matches real queue sizes.
        const measuredHistoryCount = Math.min(history.length, settings.queue.measuredHistoryLimit);
        const layout = height > 0 && rowHeight > 0
            ? queueLayoutCounts(
                height,
                rowHeight,
                summaryHeight,
                measuredHistoryCount,
                running ? 1 : 0,
                pending.length,
                serverOverflowCount,
            )
            : {history: measuredHistoryCount, pending: pending.length, overflow: serverOverflowCount};
        // Failures keep a history slot until retry or expiry; successes cannot evict them.
        const failureSlots = Math.min(failures.length, layout.history);
        const visibleFailures = failureSlots ? failures.slice(-failureSlots) : [];
        const completionSlots = Math.max(0, layout.history - visibleFailures.length);
        const visibleHistory = [...visibleFailures, ...(completionSlots ? completions.slice(-completionSlots) : [])]
            .sort((a, b) => a.completed_at - b.completed_at);
        const currentRows = [...visibleHistory, ...(running ? [running] : [])].map(getRow);
        const pendingRows = pending.slice(0, layout.pending).map((job, index) => getRow({...job, isNext: index === 0}));
        const desiredRows = new Set([...currentRows, ...pendingRows]);
        // Move only rows whose order or host changed; preserve upgraded progress components and focus.
        // Put desired rows in order without rebuilding their elements.
        const reconcile = (host, desired) => {
            let before = host.firstElementChild;
            for (const row of desired) {
                row.hidden = false;
                if (row !== before) host.insertBefore(row, before);
                before = row.nextElementSibling;
            }
        };
        reconcile(currentHost, currentRows);
        reconcile(nearHost, pendingRows);
        for (const row of rows.values()) {
            if (!desiredRows.has(row)) row.remove();
        }
        summary.textContent = `${layout.overflow} more task${layout.overflow === 1 ? '' : 's'} queued`;
        overflowHost.hidden = layout.overflow === 0;
        reportQueueCapacity();
        if (!animate || queueInitialLoad || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        // All rows have the same stable height; animate position only and never block queue updates.
        for (const row of desiredRows) {
            if (!row.animate) continue;
            const previous = previousRects.get(row.dataset.jobId);
            const rect = row.getBoundingClientRect();
            const x = previous ? previous.left - rect.left : 0;
            const y = previous ? previous.top - rect.top : -6;
            if (previous && Math.abs(x) < 0.5 && Math.abs(y) < 0.5) continue;
            queueAnimations.set(row, row.animate([
                {transform: `translate(${x}px, ${y}px)`, opacity: previous ? 1 : 0},
                {transform: 'translate(0, 0)', opacity: 1}
            ], {duration, easing: 'cubic-bezier(0.2, 0.75, 0.25, 1)'}));
        }
    }

    // Queue completion history
    // Remove a job from retained completion and failure history.
    function clearQueueHistory(jobId) {
        clearTimeout(queueHistoryTimers.get(jobId));
        queueHistoryTimers.delete(jobId);
        completedQueueJobs.delete(jobId);
        failedQueueJobs.delete(jobId);
        queueProgressMax.delete(jobId);
    }

    // Record a completed or failed job and schedule its history expiry.
    function finishQueueJob(jobId, event = null) {
        if (!event && failedQueueJobs.has(jobId)) return;
        const failed = queueFailureStates.has(event?.state) || event?.queue_failed === true;
        const state = failed ? 'failed' : 'complete';
        // TODO(MONITOR): observe completion age and retained-history pressure to
        // validate these 5-minute failure / 30-second success retention windows.
        const retention = failed ? settings.queue.failureRetentionMs : settings.queue.successRetentionMs;
        const completedAt = Number(event?.completed_at) || Date.now() / 1000;
        if (completedAt * 1000 + retention <= Date.now()) return;
        const previous = completedQueueJobs.get(jobId) || failedQueueJobs.get(jobId);
        if (previous && previous.completed_at >= completedAt) {
            if (event?.promoted) previous.promoted = true;
            return;
        }
        if (previous && previous.state === state && !event?.completed_at) return;
        const job = queueJobs.get(jobId) || previous || event;
        if (!job) return;
        clearQueueHistory(jobId);
        const record = {...job, ...event, job_id: jobId, label: job.label || jobLabel(job),
            state, percent: failed ? null : 100, isNext: false, completed_at: completedAt};
        (failed ? failedQueueJobs : completedQueueJobs).set(jobId, record);
        queueHistoryTimers.set(jobId, window.setTimeout(() => retireQueueJob(jobId),
            Math.max(0, completedAt * 1000 + retention - Date.now())));
        // TODO(MONITOR): observe successful-history eviction frequency; this
        // bound intentionally applies only to successes, not retained failures.
        while (completedQueueJobs.size > settings.queue.successHistoryLimit) {
            const oldest = [...completedQueueJobs.values()].sort((a, b) => a.completed_at - b.completed_at)[0];
            retireQueueJob(oldest.job_id);
        }
    }

    // Remove an expired job from history and rerender the queue.
    function retireQueueJob(jobId) {
        if (!completedQueueJobs.has(jobId) && !failedQueueJobs.has(jobId)) return;
        clearQueueHistory(jobId);
        const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
            .find(item => item.dataset.jobId === jobId);
        poofQueueRow(row);
        row?.remove();
        renderQueue();
    }

    // Animate a departing queue row when motion is allowed.
    function poofQueueRow(row) {
        if (!row || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        queueAnimations.get(row)?.cancel();
        const rect = row.getBoundingClientRect();
        const ghost = row.cloneNode(true);
        ghost.removeAttribute('data-job-id');
        ghost.removeAttribute('role');
        ghost.inert = true;
        ghost.tabIndex = -1;
        ghost.setAttribute('aria-hidden', 'true');
        Object.assign(ghost.style, {position: 'fixed', left: `${rect.left}px`, top: `${rect.top}px`,
            width: `${rect.width}px`, height: `${rect.height}px`, margin: '0', zIndex: '10001', pointerEvents: 'none'});
        ghost.classList.add('styler-queue-task-expiring');
        document.body.append(ghost);
        const cleanup = () => ghost.remove();
        ghost.addEventListener('animationend', event => {
            if (event.target === ghost && event.animationName === 'styler-queue-dissolve') cleanup();
        });
        window.setTimeout(cleanup, settings.queue.expiringRowCleanupMs);
    }

    // Serialize queue state fields that affect the rendered layout.
    function queueStructure() {
        return JSON.stringify([
            runningJob,
            queueOrder.map(id => [
                id,
                queueJobs.get(id)?.label,
                queueJobs.get(id)?.promoted,
                queueJobs.get(id)?.priority,
            ]),
            queueJobs.get(runningJob)?.label,
            queueJobs.get(runningJob)?.promoted,
            serverOverflowCount,
            serverQueueCount,
            [...completedQueueJobs.values(), ...failedQueueJobs.values()].map(job => [
                job.job_id,
                job.state,
                job.completed_at,
                job.promoted,
            ]),
        ]);
    }

    // Queue state synchronization
    // Apply a queue snapshot or plan received from the server.
    function updateQueue(event) {
        if (event.state !== 'queue_snapshot' && event.state !== 'queue_plan') return;
        // TODO(MONITOR): track raw queue_count and overflow_count ranges before coercion/clamping below.
        const previousStructure = queueStructure();
        receiveMaintenanceMode(event.maintenance_mode);
        updateWorkerResetControl(Boolean(event.worker_reset_pending));
        if (event.completed_job_id) finishQueueJob(event.completed_job_id);
        for (const job of event.completed || []) {
            finishQueueJob(job.job_id, job);
            updateStyleHistoryDescription(job);
        }
        if (Array.isArray(event.failed)) {
            const retained = new Set(event.failed.map(job => job.job_id));
            for (const jobId of failedQueueJobs.keys()) if (!retained.has(jobId)) clearQueueHistory(jobId);
        }
        for (const job of event.failed || []) {
            const failed = {...job, state: 'failed'};
            finishQueueJob(job.job_id, failed);
            updateStyleHistoryDescription(failed);
        }
        if (!queueHasInitialPlan || event.state === 'queue_snapshot') {
            showQueueLoading();
            queueHasInitialPlan = true;
            clearTimeout(queueInitialTimer);
            queueInitialTimer = window.setTimeout(() => {
                queueInitialLoad = false;
                const spinner = document.getElementById('styler-queue-loading');
                if (spinner) spinner.hidden = true;
                queueInitialTimer = null;
            }, settings.queue.initialLoadingMs);
        }
        queueJobs.clear();
        queueOrder = [];
        runningJob = event.running?.job_id || null;
        serverOverflowCount = Math.max(0, Number(event.overflow_count) || 0);
        serverQueueCount = Math.max(0, Number(event.queue_count) || 0);
        if (event.running?.job_id) {
            const running = {...event.running, state: event.running.state || 'running'};
            const completed = completedQueueJobs.get(running.job_id) || failedQueueJobs.get(running.job_id);
            if (completed && Number(running.queue_started_at) > completed.completed_at) clearQueueHistory(running.job_id);
            const percent = monotonicQueuePercent(running.job_id, running.percent);
            if (Number.isFinite(percent)) running.percent = percent;
            queueJobs.set(running.job_id, running);
        }
        for (const item of event.jobs || []) {
            if (!item.job_id || item.job_id === runningJob || queueJobs.has(item.job_id)) continue;
            // A stable job ID can be queued again for regeneration.
            if (completedQueueJobs.has(item.job_id) || failedQueueJobs.has(item.job_id)) clearQueueHistory(item.job_id);
            queueJobs.set(item.job_id, {...item, state: 'queued'});
            queueOrder.push(item.job_id);
        }
        for (const jobId of queueProgressMax.keys()) {
            if (!queueJobs.has(jobId) && !completedQueueJobs.has(jobId) && !failedQueueJobs.has(jobId)) queueProgressMax.delete(jobId);
        }
        if (event.state === 'queue_snapshot' || queueStructure() !== previousStructure) {
            renderQueue(event.state !== 'queue_snapshot');
        } else {
            updateQueueCount();
            if (runningJob && !completedQueueJobs.has(runningJob) && !failedQueueJobs.has(runningJob)) updateRunningProgress(queueJobs.get(runningJob));
        }
    }

    // Synchronize the worker-reset button and status message.
    function updateWorkerResetControl(pending, message) {
        const button = document.getElementById('styler-worker-reset');
        const status = document.getElementById('styler-worker-reset-status');
        if (button) button.disabled = pending;
        if (!status) return;
        if (message) status.textContent = message;
        else if (pending) status.textContent = 'Reset queued';
        else if (/^(Queueing reset|Reset queued|Restarting worker|Disconnected)/.test(status.textContent)) status.textContent = '';
    }

    document.getElementById('styler-worker-reset')?.addEventListener('click', () => {
        const button = document.getElementById('styler-worker-reset');
        if (!button || button.disabled) return;
        if (socket?.readyState !== WebSocket.OPEN) {
            updateWorkerResetControl(false, 'Disconnected');
            return;
        }
        button.disabled = true;
        updateWorkerResetControl(true, 'Queueing reset…');
        socket.send(JSON.stringify({action: 'worker_reset'}));
    });
    // Update the visible progress bar for the running job.
    function updateRunningProgress(job) {
        const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
            .find(item => item.dataset.jobId === job.job_id);
        if (!row) return;
        const progress = row.querySelector('wa-progress-bar');
        const state = row.querySelector('.styler-queue-task-state');
        if (!progress || !state) return;
        const percent = monotonicQueuePercent(job.job_id, job.percent);
        if (Number.isFinite(percent)) {
            progress.removeAttribute('indeterminate');
            progress.setAttribute('value', Math.max(0, Math.min(100, percent)));
            state.textContent = `${Math.max(0, Math.min(100, percent))}%`;
        } else {
            progress.setAttribute('indeterminate', '');
            state.textContent = queueStatusLabel(job);
        }
    }

    // Tell the server how many queue rows fit in the panel.
    function reportQueueCapacity() {
        if (socket?.readyState !== WebSocket.OPEN) return;
        const nearHost = document.getElementById('styler-queue-near');
        if (!nearHost) return;
        const row = nearHost.querySelector('.styler-queue-task')
            || document.querySelector('#styler-queue-current .styler-queue-task');
        const rowHeight = row
            ? row.getBoundingClientRect().height + (parseFloat(getComputedStyle(row).marginBottom) || 0)
            : settings.queue.fallbackRowHeight;
        // Keep small queues complete, including during initial layout and minimization.
        // TODO(MONITOR): compare visible capacity against actual queue lengths;
        // ten rows is a minimum chosen to avoid hiding small queues on short panels.
        const rows = Math.max(settings.queue.minimumCapacityRows, Math.floor(nearHost.clientHeight / rowHeight));
        if (rows === lastReportedQueueCapacity) return;
        lastReportedQueueCapacity = rows;
        socket.send(JSON.stringify({action: 'queue_capacity', rows}));
    }

    let lastReportedQueueCapacity = -1;
    const queueLayout = document.querySelector('.styler-queue-body-inner');
    if (queueLayout && 'ResizeObserver' in window) {
        const capacityObserver = new ResizeObserver(() => { renderQueue(false); reportQueueCapacity(); });
        capacityObserver.observe(queueLayout);
    }
    // Style tags and paged card refresh
    // Mark image tags that also appear in the style tag cloud.
    function highlightImageTags(card) {
        const cloud = card.querySelector('[id^="tag_cloud_"][data-style-tag]');
        if (!cloud) return;
        const styleTags = new Set([...cloud.querySelectorAll('wa-tag')].map(tag => tag.textContent.trim()));
        for (const tag of card.querySelectorAll('[data-image-tags] wa-tag')) {
            const included = styleTags.has(tag.textContent.trim());
            tag.setAttribute('aria-pressed', String(included));
            if (included) tag.setAttribute('variant', 'success');
            else tag.removeAttribute('variant');
        }
    }

    // Apply added and removed tags to matching style cards.
    function showStyleTags(style, tags, removed = []) {
        for (const cloud of document.querySelectorAll('[id^="tag_cloud_"][data-style-tag]')) {
            if (cloud.dataset.styleTag !== style) continue;
            for (const tag of cloud.querySelectorAll('wa-tag')) {
                if (removed.includes(tag.textContent.trim())) tag.remove();
            }
            for (const text of tags) {
                if ([...cloud.querySelectorAll('wa-tag')].some(tag => tag.textContent.trim() === text)) continue;
                const tag = document.createElement('wa-tag');
                tag.setAttribute('size', 'm');
                tag.setAttribute('with-remove', '');
                tag.setAttribute('pill', '');
                tag.textContent = text;
                cloud.insertBefore(tag, cloud.querySelector('.style-tag-picker-label'));
            }
        }
    }

    const stylePageStates = new WeakMap();
    const managedPageRemovals = new WeakSet();
    // Get or initialize the refill bookkeeping for a style page.
    function stylePageState(page) {
        if (!stylePageStates.has(page)) stylePageStates.set(page, {revision: 0, pending: 0, timer: null, inFlight: false, dirty: false, failures: 0});
        return stylePageStates.get(page);
    }

    // Mark a style-page refill response as stale.
    function invalidateStylePage(page) {
        if (!page) return;
        const state = stylePageState(page);
        state.revision++;
        state.dirty = true;
    }

    // Schedule a style-page refill when no refill is active.
    // TODO(MONITOR): observe refill coalescing delay and retry counts/delays,
    // using aggregate timings only; tune the debounce and exponential backoff.
    function refillStylePageSoon(page, delay = settings.stylePage.refillDebounceMs) {
        if (!page?.isConnected) return;
        const state = stylePageState(page);
        state.dirty = true;
        if (state.timer || state.inFlight || state.pending) return;
        state.timer = window.setTimeout(() => {
            state.timer = null;
            refillStylePage(page);
        }, delay);
    }

    // Fetch and reconcile the current page of style cards.
    async function refillStylePage(page) {
        if (!page.isConnected) return;
        const state = stylePageState(page);
        if (state.pending || state.inFlight) return;
        const revision = state.revision;
        state.inFlight = true;
        state.dirty = false;
        const keyOf = entry => entry.dataset.styleKey || entry.querySelector('.card-overview[id]')?.id.replace(/^card_/, '');
        const current = new Map([...page.children].filter(entry => entry.matches('[data-style-entry]'))
            .map(entry => [keyOf(entry), entry]));
        try {
            const url = new URL(page.dataset.pageUrl, window.location.href);
            url.searchParams.set('tag', page.dataset.pageCategory);
            url.searchParams.set('page', page.dataset.page || '0');
            for (const key of current.keys()) if (key) url.searchParams.append('present', key);
            const response = await fetch(url, {headers: {Accept: 'application/json'}});
            if (!response.ok) throw new Error(`Style page refill failed (${response.status})`);
            const data = await response.json();
            if (!page.isConnected) return;
            if (revision !== state.revision || state.pending) {
                state.dirty = true;
                return;
            }
            const desired = data.keys.map(key => {
                if (current.has(key)) return current.get(key);
                const template = document.createElement('template');
                template.innerHTML = data.entries[key] || '';
                const entry = template.content.firstElementChild;
                if (!entry?.matches('[data-style-entry]')) throw new Error('Style page refill has no replacement card');
                return entry;
            });
            let before = [...page.children].find(entry => entry.matches('[data-style-entry]')) || null;
            const added = [];
            for (const entry of desired) {
                if (!entry.isConnected) added.push(entry);
                if (entry !== before) {
                    if (entry.parentElement === page) managedPageRemovals.add(entry);
                    page.insertBefore(entry, before);
                }
                before = entry.nextElementSibling;
            }
            const keep = new Set(desired);
            for (const entry of current.values()) if (!keep.has(entry)) {
                managedPageRemovals.add(entry);
                entry.remove();
            }
            // TODO(MONITOR): observe page/page_size/total ranges and response
            // key counts to validate pagination assumptions, without style names.
            page.dataset.page = data.page;
            page.dataset.pageSize = data.page_size;
            page.dataset.pageTotal = data.total;
            const summary = page.querySelector('[data-style-page-summary], [data-lost-summary]');
            if (summary) {
                summary.dataset.page = data.page;
                summary.dataset.pageSize = data.page_size;
                const first = data.total ? data.page * data.page_size + 1 : 0;
                const last = Math.min(data.total, data.page * data.page_size + data.keys.length);
                summary.textContent = `Showing ${first}–${last} of ${data.total} ${data.tag} styles`;
            }
            for (const button of page.querySelectorAll('[data-style-page-nav]')) {
                const previous = button.dataset.stylePageNav === 'previous';
                const disabled = previous ? data.page === 0 : (data.page + 1) * data.page_size >= data.total;
                button.disabled = disabled;
                button.toggleAttribute('disabled', disabled);
                button.setAttribute('hx-vals', JSON.stringify({tag: data.tag, page: data.page + (previous ? -1 : 1)}));
            }
            for (const entry of added) window.htmx?.process(entry);
            state.failures = 0;
        } catch (error) {
            console.warn('Unable to refill style page', error);
            state.failures++;
            state.dirty = true;
        } finally {
            state.inFlight = false;
            if (state.dirty && page.isConnected) refillStylePageSoon(page, state.failures
                ? Math.min(settings.stylePage.retryMaximumMs,
                    settings.stylePage.retryBaseMs * 2 ** Math.min(state.failures, settings.stylePage.retryExponentLimit))
                : settings.stylePage.refillDebounceMs);
        }
    }

    // LOST style cleanup
    const dissolvingLostEntries = new WeakSet();
    // Remove a resolved LOST style and refill its page.
    function dissolveLostEntry(entry) {
        if (!entry?.isConnected || dissolvingLostEntries.has(entry)) return;
        dissolvingLostEntries.add(entry);
        const page = entry.closest('[data-style-page]');
        if (page) {
            invalidateStylePage(page);
            stylePageState(page).pending++;
        }
        const finish = () => {
            managedPageRemovals.add(entry);
            entry.remove();
            if (page) {
                stylePageState(page).pending--;
                refillStylePageSoon(page);
            }
        };
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            finish();
            return;
        }
        const style = getComputedStyle(entry);
        entry.style.height = `${entry.getBoundingClientRect().height}px`;
        entry.style.overflow = 'hidden';
        entry.style.marginBlockStart = style.marginBlockStart;
        entry.style.marginBlockEnd = style.marginBlockEnd;
        entry.style.paddingBlockStart = style.paddingBlockStart;
        entry.style.paddingBlockEnd = style.paddingBlockEnd;
        requestAnimationFrame(() => {
            if (!entry.isConnected) return;
            entry.classList.add('lost-style-dissolving');
            entry.style.height = '0px';
            entry.style.marginBlock = '0px';
            entry.style.paddingBlock = '0px';
        });
        window.setTimeout(finish, settings.stylePage.lostEntryDissolveMs);
    }

    let socket;
    // Remove a resolved style from LOST and update its page summary.
    function resolveLostStyle(event) {
        const panel = document.querySelector('wa-tab-panel[name="tag_LOST"]');
        const page = panel?.querySelector('[data-style-page]');
        const changed = page && Number(page.dataset.pageTotal) !== Number(event.lost_count);
        if (page && (changed || stylePageState(page).inFlight)) invalidateStylePage(page);
        const entries = new Set();
        for (const card of panel?.querySelectorAll('.card-overview') || []) {
            const matches = card.id === `card_${event.style_tag}`
                || [...card.querySelectorAll('[data-style-sample], .tags-removable[data-style-tag]')]
                    .some(item => (item.dataset.styleSample || item.dataset.styleTag) === event.style_tag);
            if (matches) entries.add(card.closest('[data-style-entry]') || card);
        }
        for (const entry of entries) dissolveLostEntry(entry);
        document.dispatchEvent(new CustomEvent(event.lost_count
            ? 'styler:category-added' : 'styler:category-empty',
        {detail: {tag: 'LOST', count: event.lost_count || 0}}));
        updateLostSummary(event.lost_count);
        if (page && (changed || stylePageState(page).dirty)) refillStylePageSoon(page);
    }

    // Apply tag and category changes reported by the server.
    function applyStyleTagUpdate(event) {
        if (Array.isArray(event.tags)) showStyleTags(event.style_tag, event.tags, event.removed || []);
        for (const count of Array.isArray(event.counts) ? event.counts : []) {
            document.dispatchEvent(new CustomEvent(count.count ? 'styler:category-added' : 'styler:category-empty', {detail: count}));
        }
        if (event.state === 'lost_resolved' || event.lost === false) resolveLostStyle(event);
    }

    // Refresh the visible count and range for LOST styles.
    function updateLostSummary(count) {
        for (const summary of document.querySelectorAll('[data-lost-summary]')) {
            const total = Math.max(0, Number(count) || 0);
            const page = Number(summary.dataset.page) || 0;
            const pageSize = Number(summary.dataset.pageSize) || settings.stylePage.defaultPageSize;
            const start = page * pageSize;
            const first = total ? Math.min(start + 1, total) : 0;
            const last = Math.min(start + pageSize, total);
            summary.textContent = `Showing ${first}–${last} of ${total} LOST styles`;
            const pageRoot = summary.closest('[data-style-page]');
            if (pageRoot) pageRoot.dataset.pageTotal = total;
        }
    }

    // Scan and retag controls
    let stopped = false;
    let stockpile = null;
    // Update stockpile scan buttons and status text.
    function stockpileStatus(text, busy) {
        for (const button of document.querySelectorAll('[data-stockpile-scan]')) button.disabled = busy;
        for (const status of document.querySelectorAll('[data-stockpile-status]')) status.textContent = text;
    }

    document.addEventListener('click', event => {
        const deleteButton = event.target.closest('.delete-style-button');
        if (deleteButton) {
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            const card = deleteButton.closest('.card-overview');
            if (!card || deleteButton.disabled) return;
            const style_tag = deleteButton.getAttribute('hx-vals')
                ? JSON.parse(deleteButton.getAttribute('hx-vals')).style_tag
                : card.id.replace(/^card_/, '');
            deleteButton.disabled = true;
            htmx.ajax('DELETE', '/styles/style', {
                values: {style_tag},
                swap: 'none',
                handler: (_element, response) => {
                    if (response.xhr.status < 200 || response.xhr.status >= 300) {
                        deleteButton.disabled = false;
                        return;
                    }
                    const result = JSON.parse(response.xhr.responseText);
                    const lost = card.closest('wa-tab-panel')?.getAttribute('name') === 'tag_LOST';
                    dissolveLostEntry(card.closest('[data-style-entry]') || card);
                    if (lost) {
                        updateLostSummary(result.lost_count);
                        document.dispatchEvent(new CustomEvent(result.lost_count
                            ? 'styler:category-added' : 'styler:category-empty',
                        {detail: {tag: 'LOST', count: result.lost_count || 0}}));
                    }
                }
            }).catch(() => { deleteButton.disabled = false; });
            return;
        }
        const button = event.target.closest('[data-stockpile-scan]');
        if (!button || button.disabled) return;
        if (socket?.readyState !== WebSocket.OPEN) {
            stockpileStatus('Disconnected. Try again when connected.', false);
            return;
        }
        stockpileStatus('Scanning stockpile…', true);
        socket.send(JSON.stringify({action: 'stockpile_scan'}));
    }, true);
    let stylesScan = null;
    // Update style-scan buttons and status text.
    function stylesStatus(text, busy) {
        for (const button of document.querySelectorAll('[data-styles-scan]')) button.disabled = busy;
        for (const status of document.querySelectorAll('[data-styles-status]')) status.textContent = text;
    }

    document.addEventListener('click', event => {
        const button = event.target.closest('[data-styles-scan]');
        if (!button || button.disabled) return;
        if (socket?.readyState !== WebSocket.OPEN) {
            stylesStatus('Disconnected. Try again when connected.', false);
            return;
        }
        stylesStatus('Scanning styles…', true);
        socket.send(JSON.stringify({action: 'styles_scan'}));
    });
    // Update duplicate-scan buttons and status text.
    function styleDuplicateStatus(text, busy) {
        for (const button of document.querySelectorAll('[data-style-duplicate-scan]')) button.disabled = busy;
        for (const status of document.querySelectorAll('[data-style-duplicate-status]')) status.textContent = text;
    }

    document.addEventListener('click', event => {
        const button = event.target.closest('[data-style-duplicate-scan]');
        if (!button || button.disabled) return;
        if (socket?.readyState !== WebSocket.OPEN) {
            styleDuplicateStatus('Disconnected. Try again when connected.', false);
            return;
        }
        styleDuplicateStatus('Style duplicate scan queued…', true);
        socket.send(JSON.stringify({action: 'style_duplicate_scan'}));
    });
    // Retagging and image assessment
    const retagging = new Map();
    // Update the retag button and status for one style engine.
    function retagStatus(style, engine, text, busy) {
        for (const button of document.querySelectorAll('[data-style-retag]')) {
            if (button.dataset.styleRetag !== style || button.dataset.engine !== engine) continue;
            button.disabled = busy;
            const status = button.parentElement.querySelector('.retag-status');
            if (status) status.textContent = text;
        }
    }

    // Queue a retag request unless one is already pending.
    function requestRetag(style_tag, engine, base_style_prompt = 'classic') {
        const id = key(style_tag, engine);
        if (retagging.has(id)) return false;
        if (socket?.readyState !== WebSocket.OPEN) {
            retagStatus(style_tag, engine, 'Disconnected. Try again when connected.', false);
            return false;
        }
        retagging.set(id, {style_tag, engine, failed: false});
        retagStatus(style_tag, engine, 'Retag queued', true);
        socket.send(JSON.stringify({action: 'retag', style_tag, engine, base_style_prompt}));
        return true;
    }

    // Render generated tag strings as accessible, compact Web Awesome tags.
    function renderImageTags(boxes, imageTags) {
        for (const box of boxes) {
            const tags = imageTags.slice(0, settings.samples.displayedTagLimit).map(text => {
                const tag = document.createElement('wa-tag');
                tag.setAttribute('size', 's');
                tag.setAttribute('role', 'button');
                tag.setAttribute('tabindex', '0');
                tag.textContent = text;
                return tag;
            });
            box.replaceChildren(...tags);
        }
    }

    // Apply character-placement and image-tier assessment data.
    function updateImageAssessment(event) {
        // TODO(MONITOR): collect character_placement categories and image_tier min/max before filtering.
        const labels = {in_image: 'Character in image', outside_image: 'Character outside image',
            not_visible: 'No character visible', uncertain: 'Character placement unclear'};
        if (labels[event.character_placement]) {
            for (const element of document.querySelectorAll('[data-character-placement]')) {
                if (element.dataset.styleTag === event.style_tag && element.dataset.engine === event.engine
                    && element.dataset.baseStylePrompt === (event.base_style_prompt || 'classic')) {
                    element.textContent = labels[event.character_placement];
                }
            }
        }
        if (!Number.isInteger(event.image_tier)) return;
        for (const slider of document.querySelectorAll('.style-tier-slider[data-style-tag][data-engine]')) {
            if (slider.dataset.styleTag === event.style_tag && slider.dataset.engine === event.engine) {
                slider.value = event.image_tier;
            }
        }
        if (typeof event.tier_badge_html === 'string') {
            const badge = [...document.querySelectorAll('wa-badge[data-style-tag][data-engine]')]
                .find(item => item.dataset.styleTag === event.style_tag && item.dataset.engine === event.engine);
            if (badge) {
                const template = document.createElement('template');
                template.innerHTML = event.tier_badge_html;
                const replacement = template.content.firstElementChild;
                if (replacement) badge.replaceWith(replacement);
            }
        }
    }

    document.addEventListener('click', event => {
        const button = event.target.closest('[data-style-retag]');
        if (!button || button.disabled) return;
        const style_tag = button.dataset.styleRetag;
        const engine = button.dataset.engine;
        requestRetag(style_tag, engine, button.dataset.baseStylePrompt || 'classic');
    });
    // Sample tracking and websocket lifecycle
    // Build the shared key used to track work for a style and engine.
    const key = (tag, engine) => JSON.stringify([tag, engine]);
    // Include the prompt variant when identifying an individual sample.
    const sampleKey = (tag, engine, baseStylePrompt = 'classic') =>
        JSON.stringify([tag, engine, baseStylePrompt || 'classic']);
    document.addEventListener('wa-show', event => {
        if (event.target.localName !== 'wa-details') return;
        for (const details of document.querySelectorAll('.card-overview wa-details[open]')) {
            if (details !== event.target) details.open = false;
        }
        const card = event.target.closest('.card-overview');
        const style_tag = card?.querySelector('[data-style-retag]')?.dataset.styleRetag;
        if (style_tag && socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({action: 'missing_tags', style_tag}));
        }
    });
    // Register unseen sample images and request their generation.
    function requestMissing(root = document) {
        if (socket?.readyState !== WebSocket.OPEN) return;
        const samples = [...root.querySelectorAll('[data-style-sample]')];
        if (root.matches?.('[data-style-sample]')) samples.push(root);
        samples.forEach(img => {
            const {styleSample: style_tag, engine, baseStylePrompt: base_style_prompt = 'classic'} = img.dataset;
            const id = sampleKey(style_tag, engine, base_style_prompt);
            if (!targets.has(id)) targets.set(id, new Set());
            targets.get(id).add(img);
            if (sent.has(img)) return;
            sent.add(img);
            img.alt = 'Sample queued';
            socket.send(JSON.stringify({style_tag, engine, base_style_prompt}));
        });
    }

    // Apply queue commands, job lifecycle events, and queue snapshots.
    function handleQueueEvent(event) {
        if (event.state === 'queue_task_removed' && event.job_id) {
            queueJobs.delete(event.job_id);
            queueOrder = queueOrder.filter(id => id !== event.job_id);
            const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
                .find(item => item.dataset.jobId === event.job_id);
            poofQueueRow(row);
            row?.remove();
            renderQueue();
            return true;
        }
        if (event.state === 'queue_delete_rejected' && event.job_id) {
            const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
                .find(item => item.dataset.jobId === event.job_id);
            const button = row?.querySelector('[data-queue-delete]');
            if (button) {
                button.disabled = false;
                button.title = event.message || 'Delete queued task';
            }
            return true;
        }
        if (event.state === 'retry_queued' && event.job_id) {
            clearQueueHistory(event.job_id);
            if (event.job) {
                const job = {...event.job, state: 'queued'};
                queueJobs.set(job.job_id, job);
                queueOrder = [job.job_id, ...queueOrder.filter(id => id !== job.job_id)];
                renderQueue();
            }
            return true;
        }
        if (event.state === 'retry_rejected' && event.job_id) {
            const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
                .find(item => item.dataset.jobId === event.job_id);
            const button = row?.querySelector('[data-queue-retry]');
            if (button) {
                button.disabled = false;
                button.title = event.message || 'Retry failed task';
            }
            return true;
        }
        if (event.state === 'maintenance_mode') {
            receiveMaintenanceMode(event.mode);
            return true;
        }
        if (event.state === 'job_promoted' || event.state === 'job_demoted' || event.state === 'job_promote_failed') {
            const job = queueJobs.get(event.job_id) || completedQueueJobs.get(event.job_id) || failedQueueJobs.get(event.job_id);
            if (job) {
                job.promoted = event.state === 'job_promoted';
                job.priority = event.priority ?? job.promoted;
                const row = [...document.querySelectorAll('.styler-queue-task[data-job-id]')]
                    .find(item => item.dataset.jobId === event.job_id);
                if (row) {
                    updateQueueRow(row, job);
                    if (event.message) row.title = event.message;
                }
            }
            return true;
        }
        const jobIsComplete = queueCompletionStates.has(event.state) || queueFailureStates.has(event.state);
        const queueOwnsJob = event.queue_completed || event.queue_failed || queueJobs.has(event.job_id);
        if (event.job_id && jobIsComplete && queueOwnsJob) {
            finishQueueJob(event.job_id, event);
            renderQueue();
        }
        updateQueue(event);
        return event.state === 'queue_snapshot' || event.state === 'queue_plan';
    }

    // Update scan, worker, LOST, and duplicate-style operations from server events.
    function handleOperationsEvent(event) {
        if (['style_description_complete', 'style_description_failed'].includes(event.state)) {
            updateStyleHistoryDescription(event);
            return true;
        }
        if (event.state === 'style_duplicate_scan_queued') {
            styleDuplicateStatus(event.already_queued ? 'Style duplicate scan already queued.' : 'Style duplicate scan queued…', true);
            return true;
        }
        if (event.action === 'styler_style_duplicate_scan' && event.state === 'running') {
            styleDuplicateStatus(event.message || 'Scanning duplicate styles…', true);
            return true;
        }
        if (event.state === 'style_duplicate_scan_complete') {
            for (const merged of event.merged || []) {
                for (const alias of merged.aliases || []) {
                    document.getElementById(`card_${alias}`)?.remove();
                }
                const canonical = document.getElementById(`card_${merged.canonical_key}`);
                const heading = canonical?.querySelector('[data-style-name-text]');
                if (heading) heading.textContent = merged.name;
            }
            styleDuplicateStatus(event.message || 'Style duplicate scan complete.', false);
            return true;
        }
        if (event.state === 'style_duplicate_scan_failed') {
            styleDuplicateStatus(event.message || 'Style duplicate scan failed.', false);
            return true;
        }
        if (event.state === 'worker_reset_queued') {
            updateWorkerResetControl(true, 'Reset queued; after current task');
            return true;
        }
        if (event.action === 'worker_reset' && event.state === 'complete') {
            updateWorkerResetControl(true, 'Restarting worker…');
            return true;
        }
        if (event.action === 'worker_reset' && event.state === 'worker_ready') {
            updateWorkerResetControl(false, 'Worker ready');
            window.setTimeout(() => updateWorkerResetControl(false), settings.workerReadyNoticeMs);
            return true;
        }
        if (['lost_resolved', 'style_tags_reconciled', 'styles_scan_image_complete', 'prompt_tagged'].includes(event.state)) {
            applyStyleTagUpdate(event);
        }
        if (event.state === 'lost_resolved' || event.state === 'style_tags_reconciled') return true;
        if (event.state === 'stockpile_queued') {
            stockpile = {pending: new Set(event.jobs), total: event.jobs.length, failed: event.errors.length};
            const summary = `${event.scanned} images scanned, ${event.complete} already complete`;
            stockpileStatus(`${summary}, ${event.jobs.length} queued${event.errors.length ? `, ${event.errors.length} unreadable` : ''}.`, event.jobs.length > 0);
            if (!event.jobs.length) document.body.dispatchEvent(new CustomEvent('stockpile-refresh'));
            return true;
        }
        if (event.state === 'stockpile_scan_failed') {
            stockpileStatus(event.message, false);
            return true;
        }
        if (event.state === 'styles_scan_queued') {
            stylesScan = {version: event.version, pending: new Set(event.jobs), total: event.jobs.length, failed: 0};
            stylesStatus(event.jobs.length
                ? `Scan version ${event.version}: ${event.jobs.length} images queued.`
                : `Scan version ${event.version} complete.`, event.jobs.length > 0);
            return true;
        }
        if (event.state === 'styles_scan_complete') {
            const version = event.scan_version ?? event.version;
            stylesStatus(`Scan version ${version} complete.`, false);
            if (stylesScan?.version === version) stylesScan = null;
            return true;
        }
        if (event.state === 'styles_scan_failed') {
            stylesStatus(event.message, false);
            return true;
        }
        if (['styles_scan_image_complete', 'styles_scan_image_failed'].includes(event.state)) {
            if (stylesScan?.pending.delete(event.job_id)) {
                if (event.state === 'styles_scan_image_failed') stylesScan.failed++;
                const done = stylesScan.total - stylesScan.pending.size;
                stylesStatus(`Scan version ${stylesScan.version}: ${done}/${stylesScan.total} processed${stylesScan.failed ? `, ${stylesScan.failed} failed` : ''}.`, stylesScan.pending.size > 0);
            }
            if (event.state === 'styles_scan_image_complete') {
                // TODO(MONITOR): observe image_tags lengths and how often
                // this eight-tag display cap truncates results; never retain tag text.
                const boxes = [];
                for (const box of document.querySelectorAll('[data-image-tags]')) {
                    if (box.dataset.styleTag !== event.style_tag || box.dataset.engine !== event.engine) continue;
                    boxes.push(box);
                }
                renderImageTags(boxes, event.image_tags || []);
            }
            return true;
        }
        if (['stockpile_complete', 'stockpile_failed'].includes(event.state)) {
            if (!stockpile?.pending.delete(event.job_id)) return true;
            if (event.state === 'stockpile_failed') stockpile.failed++;
            const done = stockpile.total - stockpile.pending.size;
            stockpileStatus(`Stockpile: ${done}/${stockpile.total} processed${stockpile.failed ? `, ${stockpile.failed} failed` : ''}.`, stockpile.pending.size > 0);
            if (!stockpile.pending.size) document.body.dispatchEvent(new CustomEvent('stockpile-refresh'));
            return true;
        }
        return false;
    }

    // Update retag controls, sample status, progress, and generated image targets.
    function handleSampleEvent(event) {
        const id = sampleKey(event.style_tag, event.engine, event.base_style_prompt);
        updateImageAssessment(event);
        if (['complete', 'loaded'].includes(event.state) && event.filename && event.style_tag && event.engine) {
            const card = document.getElementById(`card_${event.style_tag}`);
            const details = card?.querySelector('wa-details');
            const tagBox = [...(card?.querySelectorAll('[data-image-tags]') || [])]
                .find(box => box.dataset.styleTag === event.style_tag && box.dataset.engine === event.engine);
            const imageTags = Array.isArray(event.image_tags) ? event.image_tags
                : [...(tagBox?.querySelectorAll('wa-tag') || [])].map(tag => tag.textContent.trim());
            if (details?.open && imageTags.length === 0) requestRetag(event.style_tag, event.engine);
        }
        if (event.state === 'retag_queued') {
            for (const engine of event.engines || []) {
                retagging.set(key(event.style_tag, engine), {style_tag: event.style_tag, engine, failed: false});
                retagStatus(event.style_tag, engine, 'Retag queued', true);
            }
            return;
        }
        if (event.state === 'retag_failed') {
            // TODO(MONITOR): observe the engines omitted from this legacy
            // fallback list before broadening it; collect category counts only.
            const engines = event.engine ? [event.engine] : ['zit', 'krea2', 'qwen'];
            for (const engine of engines) {
                retagging.delete(key(event.style_tag, engine));
                retagStatus(event.style_tag, engine, event.message, false);
            }
            return;
        }
        if (['prompt_tagged', 'tag_failed', 'tag_abyss'].includes(event.state)) {
            const retagId = key(event.style_tag, event.engine);
            const pending = retagging.get(retagId);
            if (pending) {
                pending.failed ||= event.state === 'tag_failed';
                retagStatus(event.style_tag, event.engine, event.state === 'tag_abyss'
                    ? 'Style needs manual tag repair' : pending.failed ? 'Retag failed' : 'Retag complete', false);
                retagging.delete(retagId);
            }
        }
        if (Array.isArray(event.image_tags)) {
            // TODO(MONITOR): observe image_tags lengths and truncation above
            // the eight-tag cap; do not record tag values.
            const boxes = [];
            for (const box of document.querySelectorAll('[data-image-tags]')) {
                if (box.dataset.styleTag !== event.style_tag || box.dataset.engine !== event.engine
                    || box.dataset.baseStylePrompt !== (event.base_style_prompt || 'classic')) continue;
                boxes.push(box);
            }
            renderImageTags(boxes, event.image_tags);
        }
        if (Array.isArray(event.prompt_tags)) showStyleTags(event.style_tag, event.prompt_tags);
        if (event.state === 'prompt_tagged') return;
        const matches = targets.get(id);
        const progressKey = event.job_id || id;
        if (['running', 'complete', 'loaded', 'failed'].includes(event.state)) {
            const sample = matches && [...matches].find(node => node.isConnected);
            const tagCloud = [...document.querySelectorAll('.tags-removable[data-style-tag]')]
                .find(cloud => cloud.dataset.styleTag === event.style_tag);
            const styleTags = Array.isArray(event.tags) ? event.tags
                : [...(tagCloud?.querySelectorAll('wa-tag') || [])].map(tag => tag.textContent.trim());
            document.dispatchEvent(new CustomEvent('styler:sample-status', {
                detail: {...event, tags: styleTags,
                    style_name: sample?.dataset.styleName || event.style_tag?.replaceAll('_', ' ')}
            }));
        }
        if (!matches) {
            if (['complete', 'loaded', 'failed'].includes(event.state)) sampleProgressMax.delete(progressKey);
            return;
        }
        [...matches].forEach(img => {
            if (!img.isConnected) { matches.delete(img); return; }
            const replace = replacement => {
                sent.add(replacement);
                matches.delete(img);
                matches.add(replacement);
                img.replaceWith(replacement);
            };
            // Queued cards stay idle: no spinner, replacement, or layout work.
            if (event.state === 'queued') {
                return;
            } else if (event.state === 'running') {
                let container = img;
                if (img.tagName === 'IMG') {
                    container = document.createElement('span');
                    container.className = 'styler-sample-placeholder wa-stack';
                    Object.assign(container.dataset, img.dataset);
                    replace(container);
                }
                let bar = container.querySelector('wa-progress-bar');
                if (!bar) {
                    container.style.width = '100%';
                    container.innerHTML = '<span data-sample-progress-message></span><wa-progress-bar label="Sample generation progress"></wa-progress-bar>';
                    bar = container.querySelector('wa-progress-bar');
                }
                const previous = sampleProgressMax.get(progressKey) || 0;
                const displayed = queuePercent(bar.getAttribute('value'));
                const reported = queuePercent(event.percent);
                const percent = Math.min(100, Math.max(previous,
                    Number.isFinite(displayed) ? displayed : 0,
                    Number.isFinite(reported) ? reported : 0));
                sampleProgressMax.set(progressKey, percent);
                bar.setAttribute('value', percent);
                container.querySelector('[data-sample-progress-message]').textContent = `${event.message || 'Generating sample'} (${percent}%)`;
            } else if (['complete', 'loaded'].includes(event.state) && event.filename) {
                sampleProgressMax.delete(progressKey);
                const image = document.createElement('img');
                image.className = 'image_sample';
                image.alt = `${event.style_tag.replaceAll('_', ' ')} — ${event.engine}`;
                image.loading = 'lazy';
                image.decoding = 'async';
                image.src = event.filename;
                matches.delete(img);
                const opener = document.createElement('button');
                opener.type = 'button';
                opener.className = 'styler-image-open';
                opener.dataset.styleViewer = '';
                opener.setAttribute('aria-label', image.alt);
                opener.append(image);
                img.replaceWith(opener);
                opener.dispatchEvent(new CustomEvent('styler:sample-complete', {bubbles: true}));
            } else if (event.state === 'failed') {
                sampleProgressMax.delete(progressKey);
                img.textContent = 'Sample failed';
                img.title = event.message || 'Generation failed';
            }
        });

    }

    // Decode one server frame, then apply its protocol event to the relevant page state.
    function handleSocketMessage(data) {
        let event;
        try {
            event = JSON.parse(data);
        } catch (error) {
            console.warn('Ignoring invalid styler websocket message', error);
            return;
        }
        if (!event || typeof event !== 'object' || Array.isArray(event)) return;
        // TODO(MONITOR): sample state/action/engine/job_type values at this ingress point to find
        // protocol variants the UI currently ignores. Measure base_style_prompt length/cardinality,
        // not its raw text; do not collect job IDs, filenames, style tags, or tag contents.
        if (handleQueueEvent(event)) return;
        if (handleOperationsEvent(event)) return;
        handleSampleEvent(event);
    }

    // Reset connection-scoped caches and request the work visible in the DOM.
    function handleSocketOpen() {
        sent = new WeakSet();
        lastReportedQueueCapacity = -1;
        targets.clear();
        updateWorkerResetControl(false);
        reportQueueCapacity();
        requestMissing();
        for (const details of document.querySelectorAll('.card-overview wa-details[open]')) {
            const style_tag = details.closest('.card-overview')?.querySelector('[data-style-retag]')?.dataset.styleRetag;
            if (style_tag) socket.send(JSON.stringify({action: 'missing_tags', style_tag}));
        }
    }

    // Reset transient controls and schedule a reconnect after the socket closes.
    function handleSocketClose() {
        pendingMaintenanceMode = null;
        showMaintenanceMode(false);
        showQueueLoading();
        stockpile = null;
        stockpileStatus('Disconnected. Scan again after reconnecting to check progress.', false);
        for (const pending of retagging.values()) {
            retagStatus(pending.style_tag, pending.engine, 'Disconnected. Retag may still be processing.', false);
        }
        retagging.clear();
        if (!stopped) setTimeout(connect, settings.samples.reconnectDelayMs);
    }

    // Create the websocket and connect its lifecycle to named handlers.
    function connect() {
        socket = new WebSocket(endpoint);
        socket.addEventListener('open', handleSocketOpen);
        socket.addEventListener('message', ({data}) => handleSocketMessage(data));
        socket.addEventListener('close', handleSocketClose);
    }

    // Keep dynamic cards and queue destinations in sync with the DOM.
    new MutationObserver(records => {
        for (const record of records) {
            for (const node of record.removedNodes) {
                if (node.nodeType !== 1 || !node.matches('[data-style-entry]')) continue;
                if (managedPageRemovals.has(node)) {
                    managedPageRemovals.delete(node);
                    continue;
                }
                const page = record.target.closest?.('[data-style-page]');
                if (page?.isConnected) {
                    invalidateStylePage(page);
                    refillStylePageSoon(page);
                }
            }
        }
        const stockpileChanged = records.some(record => [...record.addedNodes, ...record.removedNodes].some(node =>
            node.nodeType === 1 && (node.matches('[data-stockpile-filename]') || node.querySelector('[data-stockpile-filename]'))));
        if (stockpileChanged) {
            for (const row of document.querySelectorAll('.styler-queue-task[data-job-id]')) {
                const job = queueJobs.get(row.dataset.jobId) || completedQueueJobs.get(row.dataset.jobId) || failedQueueJobs.get(row.dataset.jobId);
                if (job) updateQueueFindButton(row, job);
            }
        }
        const cards = new Set();
        for (const record of records) {
            const card = record.target.closest?.('.card-overview');
            if (card) cards.add(card);
            for (const node of record.addedNodes) {
                if (node.nodeType !== 1) continue;
                if (!sent.has(node)) requestMissing(node);
                if (node.matches('.card-overview')) cards.add(node);
                for (const card of node.querySelectorAll('.card-overview')) cards.add(card);
            }
        }
        for (const card of cards) highlightImageTags(card);
    }).observe(document.body, {childList: true, subtree: true});
    window.addEventListener('pagehide', () => { stopped = true; socket.close(); });
    connect();
})();
