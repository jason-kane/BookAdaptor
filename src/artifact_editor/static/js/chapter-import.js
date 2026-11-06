(() => {
    const oneShotJobKey = 'textoviva-one-shot-job';

    function restoreOneShotProgressTarget() {
        const container = document.getElementById('textoviva-one-shot');
        if (!container) return;
        const activeStatus = document.querySelector(
            '#textoviva-one-shot [data-video-job][data-video-action="textoviva_one_shot"][data-video-state="running"], ' +
            '#textoviva-one-shot [data-video-job][data-video-action="textoviva_one_shot"][data-video-state="queued"], ' +
            '#chapter-job-progress [data-video-job][data-video-action="textoviva_one_shot"][data-video-state="running"], ' +
            '#chapter-job-progress [data-video-job][data-video-action="textoviva_one_shot"][data-video-state="queued"]'
        );
        if (activeStatus) {
            sessionStorage.setItem(oneShotJobKey, activeStatus.dataset.videoJob);
            container.querySelector('wa-button')?.after(activeStatus);
            return;
        }
        const jobId = sessionStorage.getItem(oneShotJobKey);
        if (!jobId || document.getElementById(`video-progress-${jobId}`)) return;
        const target = document.createElement('div');
        target.id = `video-progress-${jobId}`;
        target.setAttribute('role', 'status');
        target.setAttribute('aria-live', 'polite');
        container.querySelector('wa-button')?.after(target);
    }

    restoreOneShotProgressTarget();
    document.addEventListener('DOMContentLoaded', restoreOneShotProgressTarget);
    document.addEventListener('htmx:afterSwap', restoreOneShotProgressTarget);

    document.addEventListener('htmx:confirm', event => {
        const button = event.detail.elt?.closest('[data-regenerate-phrase]');
        const pending = button && window.chapterPhraseSaves?.[button.dataset.regeneratePhrase];
        if (!pending) return;
        event.preventDefault();
        pending.then(() => event.detail.issueRequest(true)).catch(error => console.error('Phrase save failed; audio not regenerated', error));
    });
    const workflowUrl = document.currentScript?.dataset.workflowStatusUrl;
    let workflowRequest = null;
    let lastWorkflowRefresh = 0;

    function refreshPrerequisites() {
        if (!workflowUrl || workflowRequest || Date.now() - lastWorkflowRefresh < 1000) return;
        lastWorkflowRefresh = Date.now();
        workflowRequest = fetch(workflowUrl, {cache: 'no-store'})
            .then(response => {
                if (!response.ok) throw new Error(`Workflow status failed: ${response.status}`);
                return response.json();
            }).then(({prerequisites}) => {
                document.querySelectorAll('[data-workflow-action]').forEach(button => {
                    const action = button.dataset.workflowAction;
                    if (!Object.hasOwn(prerequisites, action)) return;
                    const reason = prerequisites[action];
                    button.disabled = Boolean(reason) || button.dataset.workflowBusy === 'true';
                    for (const target of [button, button.closest('[data-workflow-control]')]) {
                        if (!target) continue;
                        if (reason) target.setAttribute('title', reason);
                        else target.removeAttribute('title');
                    }
                });
            }).catch(error => console.error(error))
            .finally(() => { workflowRequest = null; });
    }
    if (workflowUrl) {
        document.addEventListener('DOMContentLoaded', refreshPrerequisites);
        window.setInterval(() => {
            if (document.visibilityState !== 'hidden') refreshPrerequisites();
        }, 2000);
    }

    // Report the click even when HTMX is waiting for a disconnected socket.
    document.addEventListener('click', event => {
        const button = event.target.closest?.('#import_btn');
        if (!button) return;
        const container = button.closest('[data-chapter-import]');
        const status = document.getElementById(`video-progress-${container.dataset.chapterImport}`);
        if (!status) return;
        status.dataset.videoState = 'connecting';
        status.textContent = 'Connecting to import service…';
        window.setTimeout(() => {
            const current = document.getElementById(status.id);
            if (current?.dataset.videoState === 'connecting') {
                current.textContent = 'Import service is unavailable. Waiting to reconnect; your request has not been acknowledged.';
            }
        }, 5000);
    }, true);

    document.addEventListener('htmx:wsConfigSend', event => {
        const button = event.detail.elt;
        const group = button?.closest('[data-chapter-assets]');
        if (group) {
            // Use this button's command, including its status target's job ID.
            // Form-associated custom elements can contribute unrelated values.
            let task;
            try { task = JSON.parse(button.getAttribute('hx-vals')); } catch (_) { return; }
            event.detail.parameters = task;
            event.detail.messageBody = JSON.stringify(task);
            return;
        }
        if (!['chapter_import', 'textoviva_one_shot'].includes(event.detail.parameters.action)) return;
        if (typeof window.getChapterSectionsPayload === 'function') {
            event.detail.parameters.sections = JSON.stringify(window.getChapterSectionsPayload());
            event.detail.messageBody = JSON.stringify(event.detail.parameters);
        }
        if (event.detail.parameters.action === 'textoviva_one_shot') {
            const jobId = event.detail.parameters.job_id;
            const progress = document.getElementById(`video-progress-${jobId}`);
            if (jobId) sessionStorage.setItem(oneShotJobKey, jobId);
            const button = document.querySelector('#textoviva-one-shot [data-workflow-action="textoviva_one_shot"]');
            if (progress && button) button.after(progress);
        }
    });

    document.addEventListener('htmx:wsAfterSend', event => {
        if (event.detail.elt?.id !== 'import_btn' &&
            !event.detail.elt?.hasAttribute('data-metadata-button') &&
            !event.detail.elt?.hasAttribute('data-workflow-action')) return;
        event.detail.elt.disabled = true;
        if (event.detail.elt.dataset) event.detail.elt.dataset.workflowBusy = 'true';
        const group = event.detail.elt.closest?.('[data-chapter-assets], #video-generation-notice');
        const previousStatus = group?.querySelector?.('[data-video-state]');
        if (previousStatus) previousStatus.dataset.videoState = 'submitting';
        if (event.detail.elt.id === 'import_btn') {
            const container = event.detail.elt.closest('[data-chapter-import]');
            const status = document.getElementById(`video-progress-${container.dataset.chapterImport}`);
            status.dataset.videoState = 'submitting';
            status.textContent = 'Submitting chapter import…';
            window.setTimeout(() => {
                const current = document.getElementById(status.id);
                if (current?.dataset.videoState === 'submitting') {
                    current.textContent = 'Import sent, but confirmation is delayed. Check the job queue before retrying.';
                }
            }, 5000);
        }
    });

    document.addEventListener('htmx:wsAfterMessage', () => {
        document.querySelectorAll('#textoviva-one-shot [data-video-job][data-video-action="textoviva_one_shot"]')
            .forEach(status => {
                if (['complete', 'failed', 'cancelled'].includes(status.dataset.videoState) &&
                    sessionStorage.getItem(oneShotJobKey) === status.dataset.videoJob) {
                    sessionStorage.removeItem(oneShotJobKey);
                }
            });
        document.querySelectorAll('#video-generation-notice [data-video-state]').forEach(status => {
            if (!['complete', 'failed', 'cancelled'].includes(status.dataset.videoState)) return;
            const button = status.closest('#video-generation-notice')
                .querySelector(`[data-workflow-action="${CSS.escape(status.dataset.videoAction || '')}"]`);
            if (button) {
                button.dataset.workflowBusy = 'false';
                if (status.dataset.videoAction === 'textoviva_one_shot') button.disabled = false;
            }
        });
        refreshPrerequisites();
        document.querySelectorAll('[data-chapter-import], [data-chapter-metadata], [data-chapter-assets]').forEach(container => {
            const job = container.dataset.chapterImport || container.dataset.chapterMetadata || container.dataset.chapterAssets;
            const status = document.getElementById(`video-progress-${job}`);
            const state = status?.dataset.videoState;
            if (container.hasAttribute('data-chapter-assets')) {
                const completedImage = status?.textContent.match(/Image\s+(\d+):\s+Animation complete\b/);
                if (completedImage) {
                    const imageIndex = completedImage[1];
                    const refreshed = new Set((container.dataset.refreshedAnimationImages || '').split(',').filter(Boolean));
                    if (!refreshed.has(imageIndex)) {
                        const summary = document.querySelector(`#timeline-image-${imageIndex} > summary`);
                        const editor = document.getElementById(`timeline-image-editor-${imageIndex}`);
                        const url = summary?.getAttribute('hx-get');
                        if (url && editor && window.htmx) {
                            refreshed.add(imageIndex);
                            container.dataset.refreshedAnimationImages = [...refreshed].join(',');
                            window.htmx.ajax('GET', url, {target: editor, swap: 'innerHTML'});
                        }
                    }
                }
            }
            if (state === 'complete') {
                // Reload the typeset text, phrase audio, and image timeline together.
                window.location.reload();
            } else if (state === 'failed' || state === 'cancelled') {
                const button = container.querySelector('wa-button');
                button.dataset.workflowBusy = 'false';
                if (!button.hasAttribute('data-workflow-action')) button.disabled = false;
            }
        });
    });
})();
