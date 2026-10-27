const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');

const sourcePath = path.resolve('src/artifact_editor/static/js/styler-samples.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const functionNames = [...source.matchAll(/^    (?:async )?function (\w+)\s*\(/gm)].map(match => match[1]);
const stateNames = [...source.matchAll(/^    (?:const|let) (\w+)(?:\s*=|;)/gm)].map(match => match[1]);
const hookNames = [...new Set([...functionNames, ...stateNames, 'key', 'sampleKey'])];
const instrumentedSource = source.replace(/\}\)\(\);\s*$/, `globalThis.__testHooks = {${hookNames.join(',')}, setQueueDestination: next => (queueDestination = next)};\n})();`);

function setup(markup = '') {
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', () => {});
    const dom = new JSDOM(`<script data-styler-websocket="ws://example/styler-ws"></script>${markup}`, {
        runScripts: 'outside-only',
        url: 'http://example.test/styles/',
        virtualConsole,
    });
    const {window} = dom;
    Object.defineProperty(window.document, 'currentScript', {value: window.document.querySelector('script')});
    window.matchMedia = () => ({matches: true});
    window.requestAnimationFrame = callback => callback();
    window.scrollTo = () => {};
    window.HTMLElement.prototype.scrollIntoView = function () {};
    window.scrollX = 12;
    window.scrollY = 34;

    const timers = new Map();
    let timerId = 0;
    window.setTimeout = (callback, delay = 0) => {
        timers.set(++timerId, {callback, delay});
        return timerId;
    };
    window.clearTimeout = id => timers.delete(id);

    const requests = [];
    window.fetch = async (url, options) => {
        requests.push({url, options});
        return {ok: true, json: async () => ({tags: []})};
    };
    window.htmx = {
        ajax: (_method, _url, options) => {
            window.lastHtmxOptions = options;
            return {catch: callback => {window.htmxCatch = callback;}};
        },
        process() {},
        trigger() {},
    };
    window.customElements.define('wa-slider', class extends window.HTMLElement {});
    window.customElements.define('wa-details', class extends window.HTMLElement {
        get open() { return this.hasAttribute('open'); }
        set open(value) { this.toggleAttribute('open', Boolean(value)); }
    });

    const sockets = [];
    class Socket extends window.EventTarget {
        static OPEN = 1;
        constructor(url) {
            super();
            this.url = url;
            this.readyState = Socket.OPEN;
            this.messages = [];
            sockets.push(this);
        }
        send(data) { this.messages.push(JSON.parse(data)); }
        close() { this.readyState = 3; }
    }
    window.WebSocket = Socket;

    new vm.Script(instrumentedSource, {filename: sourcePath}).runInContext(dom.getInternalVMContext());
    const hooks = window.__testHooks;
    const socket = sockets[0];
    const emit = event => socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify(event)}));
    const runTimer = (delay, index = 0) => {
        const entry = [...timers.entries()].filter(([, timer]) => timer.delay === delay)[index];
        if (!entry) return false;
        timers.delete(entry[0]);
        entry[1].callback();
        return true;
    };
    return {dom, window, hooks, socket, sockets, emit, timers, requests, runTimer};
}

(async () => {
    {
        const h = setup('<wa-button data-regenerate></wa-button><span id="styler-queue-loading" hidden></span><div id="styler-job-queue"></div><button id="styler-queue-toggle"></button><button data-style-retag="artist" data-engine="zit"></button><span class="retag-status"></span>');
        const {window, hooks} = h;
        const warnings = [];
        window.console.warn = (...message) => warnings.push(message);
        h.socket.dispatchEvent(new window.MessageEvent('message', {data: '{invalid json'}));
        h.socket.dispatchEvent(new window.MessageEvent('message', {data: '[]'}));
        assert.equal(warnings.length, 1, 'Malformed websocket frames are discarded at the protocol boundary');
        assert.equal(hooks.requestRetag('artist', 'zit'), true);
        h.socket.dispatchEvent(new window.Event('close'));
        assert.equal(hooks.retagging.size, 0, 'Closing the socket clears transient retag state');
        assert.match(window.document.querySelector('.retag-status').textContent, /Disconnected/);
        assert.equal(h.runTimer(2_000), true, 'A live page reconnects after the configured delay');
        assert.equal(h.sockets.length, 2);
        window.dispatchEvent(new window.Event('pagehide'));
        h.sockets[1].dispatchEvent(new window.Event('close'));
        assert.equal(h.runTimer(2_000), false, 'A pagehide stops reconnect attempts');
        assert.deepEqual({...hooks.scrollPosition()}, {left: 12, top: 34});
        const regenerate = window.document.querySelector('[data-regenerate]');
        regenerate.dispatchEvent(new window.Event('pointerdown', {bubbles: true}));
        assert.equal(hooks.regenerateScrollPositions.has(regenerate), true, 'pointerdown stores scroll position');
        regenerate.dispatchEvent(new window.CustomEvent('htmx:beforeRequest', {bubbles: true, detail: {elt: regenerate}}));
        const scrolled = [];
        window.scrollTo = position => scrolled.push(position);
        regenerate.dispatchEvent(new window.CustomEvent('htmx:afterSettle', {bubbles: true, detail: {elt: regenerate}}));
        assert.equal(hooks.regenerateScrollPositions.has(regenerate), false, 'afterSettle consumes saved position');
        assert.deepEqual({...scrolled[0]}, {left: 12, top: 34, behavior: 'instant'});
        window.document.querySelector('#styler-queue-toggle').click();
        assert.equal(window.document.querySelector('#styler-job-queue').classList.contains('is-minimized'), true);
        assert.equal(window.document.querySelector('#styler-queue-toggle').getAttribute('aria-expanded'), 'false');
        hooks.showQueueLoading();
        assert.equal(window.document.querySelector('#styler-queue-loading').hidden, false);
        hooks.showMaintenanceMode(false);
        assert.equal(hooks.queuePercent(undefined), NaN);
        assert.equal(hooks.queuePercent('42'), 42);
        assert.equal(hooks.monotonicQueuePercent('p', '55'), 55);
        assert.equal(hooks.monotonicQueuePercent('p', '40'), 55);
        assert.equal(hooks.monotonicQueuePercent('p', null), 55);
        for (const [state, label] of [['clearing_vram', 'Cleaning'], ['preparing', 'Preparing'], ['running', 'Running'], ['complete', 'Done'], ['queued', 'queued']]) {
            assert.equal(hooks.queueStatusLabel({state}), label);
        }
        assert.equal(hooks.jobLabel({action: 'styler_tag_sample', style_tag: 'a_b', engine: 'zit'}), 'Image tags · a b · Z-Image');
        assert.equal(hooks.jobLabel({action: 'stockpile_image_metadata', filename: '/tmp/p.png'}), 'Stockpile tags · p.png');
        assert.equal(hooks.jobLabel({message: 'custom'}), 'custom');
        assert.equal(hooks.queueTaskType({action: 'styler_sample'}), 'Sample image');
        assert.equal(hooks.queueTaskType({action: 'unknown_kind'}), 'unknown kind');
        assert.match(hooks.queueTaskTypeIcon('Category grouping'), /diagram-project\.svg$/);
        assert.match(hooks.queueTaskTypeIcon('unknown'), /gears\.svg$/);
        assert.equal(hooks.queueTaskName({style_tag: 'a_b', engine: 'qwen'}), 'a b · Qwen 2.1');
        assert.equal(hooks.queueTaskName({label: 'fallback'}), 'fallback');
        assert.equal(hooks.key('a', 'b'), '["a","b"]');
        assert.equal(hooks.sampleKey('a', 'b', ''), '["a","b","classic"]');
        const missing = hooks.waitForStyleCard('missing', 10);
        h.runTimer(10);
        assert.equal(await missing, null);
        const waiting = hooks.waitForStyleCard('later', 100);
        const later = window.document.createElement('div');
        later.id = 'card_later';
        window.document.body.append(later);
        assert.equal(await waiting, later);
        window.matchMedia = () => ({matches: false});
        const expiring = hooks.queueTaskRow({job_id: 'expiring', label: 'Expiring', state: 'complete'});
        window.document.body.append(expiring);
        hooks.poofQueueRow(expiring);
        const ghost = window.document.querySelector('.styler-queue-task-expiring');
        assert.ok(ghost);
        const animationEnd = new window.Event('animationend');
        Object.defineProperty(animationEnd, 'animationName', {value: 'styler-queue-dissolve'});
        ghost.dispatchEvent(animationEnd);
        assert.equal(window.document.querySelector('.styler-queue-task-expiring'), null);
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    {
        const h = setup('<div data-style-history-description data-style-tag="artist"></div><wa-slider id="styler-maintenance-rate" value="1"></wa-slider><div class="styler-queue-rate"></div>');
        const {window, hooks, socket} = h;
        const description = window.document.querySelector('[data-style-history-description]');
        hooks.updateStyleHistoryDescription({state: 'ignored', style_tag: 'artist', description: 'no'});
        assert.equal(description.textContent, '');
        hooks.updateStyleHistoryDescription({action: 'styler_style_description', state: 'complete', style_tag: 'artist', description: 'A description'});
        assert.equal(description.textContent, 'A description');
        hooks.updateStyleHistoryDescription({state: 'style_description_failed', style_tag: 'artist'});
        assert.match(description.textContent, /Generation failed/);
        hooks.updateStyleHistoryDescription({action: 'styler_style_description', state: 'failed', style_tag: 'artist', message: 'offline'});
        assert.match(description.textContent, /offline/);
        hooks.receiveMaintenanceMode('invalid');
        hooks.receiveMaintenanceMode('run');
        assert.equal(window.document.querySelector('#styler-maintenance-rate').getAttribute('aria-valuetext'), 'Run');
        const slider = window.document.querySelector('#styler-maintenance-rate');
        await Promise.resolve();
        assert.equal(slider.valueFormatter(1), 'Walk');
        slider.value = '0';
        slider.dispatchEvent(new window.Event('input', {bubbles: true}));
        assert.deepEqual(socket.messages.at(-1), {action: 'maintenance_mode', mode: 'off'});
        slider.value = '99';
        slider.dispatchEvent(new window.Event('change', {bubbles: true}));
        socket.readyState = 3;
        slider.value = '1';
        slider.dispatchEvent(new window.Event('change', {bubbles: true}));
        assert.equal(slider.disabled, true);
        hooks.updateQueueCount();
        hooks.updateQueueFindButton(window.document.createElement('div'), {});
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    {
        const h = setup('<div id="styler-queue-current"></div><div id="styler-queue-near"></div><div id="styler-queue-overflow" hidden></div><div class="styler-queue-body-inner"><div id="styler-queue-current"></div></div><span id="styler-queue-count"></span><button id="styler-worker-reset"></button><span id="styler-worker-reset-status"></span>');
        const {window, hooks, socket} = h;
        const row = hooks.queueTaskRow({job_id: 'job', label: 'Job label', state: 'preparing', isNext: true, percent: null, action: 'styler_sample'});
        assert.equal(row.dataset.next, 'true');
        assert.ok(row.querySelector('wa-progress-bar').hasAttribute('indeterminate'));
        hooks.updateQueueRow(row, {job_id: 'job', label: 'Job label', state: 'queued', action: 'styler_sample'});
        assert.equal(row.querySelector('wa-progress-bar'), null);
        hooks.updateQueueRow(row, {job_id: 'job', label: 'Job label', state: 'running', percent: 27, action: 'styler_sample'});
        assert.equal(row.querySelector('.styler-queue-task-state').textContent, '27%');
        const indeterminate = hooks.queueTaskRow({job_id: 'indeterminate', label: 'Indeterminate', state: 'running', action: 'styler_sample'});
        window.document.body.append(indeterminate);
        hooks.updateRunningProgress({job_id: 'indeterminate', percent: null, state: 'running'});
        assert.equal(indeterminate.querySelector('wa-progress-bar')?.hasAttribute('indeterminate'), true);
        hooks.updateQueueFindButton(row, {filename: 'missing.png'});
        assert.equal(row.querySelector('[data-queue-find]'), null);
        hooks.updateQueueRetryButton(row, {state: 'queued', job_id: 'job'});
        assert.equal(row.querySelector('[data-queue-retry]'), null);
        hooks.updateQueueDeleteButton(row, {state: 'queued', job_id: 'job', label: 'Job'});
        assert.ok(row.querySelector('[data-queue-delete]'));
        hooks.updateQueueDeleteButton(row, {state: 'complete', job_id: 'job'});
        assert.equal(row.querySelector('[data-queue-delete]'), null);
        hooks.updateQueueRetryButton(row, {state: 'failed', job_id: 'job', label: 'Job'});
        assert.ok(row.querySelector('[data-queue-retry]'));
        hooks.updateQueueRetryButton(row, {state: 'running', job_id: 'job'});
        hooks.updateQueueFindButton(row, {style_tag: 'artist', engine: 'zit', label: 'Job'});
        assert.ok(row.querySelector('[data-queue-find]'));
        hooks.updateQueueCount();
        hooks.updateWorkerResetControl(true, 'Reset queued');
        assert.equal(window.document.querySelector('#styler-worker-reset').disabled, true);
        hooks.updateWorkerResetControl(false, 'Worker ready');
        window.document.querySelector('#styler-worker-reset').click();
        assert.equal(socket.messages.at(-1).action, 'worker_reset');
        socket.readyState = 3;
        window.document.querySelector('#styler-worker-reset').disabled = false;
        window.document.querySelector('#styler-worker-reset').click();
        assert.equal(window.document.querySelector('#styler-worker-reset-status').textContent, 'Disconnected');
        hooks.updateRunningProgress({job_id: 'absent', percent: 10});
        assert.equal(hooks.queueLayoutCounts(400, 40, 32, 2, 1, 3, 0).pending, 3);
        hooks.renderQueue();
        hooks.clearQueueHistory('absent');
        hooks.finishQueueJob('done', {state: 'complete', completed_at: Date.now() / 1000});
        hooks.finishQueueJob('failed', {state: 'failed', completed_at: Date.now() / 1000});
        hooks.finishQueueJob('old', {state: 'complete', completed_at: 1});
        hooks.queueStructure();
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    {
        const h = setup('<wa-tab-group data-category-tab-group data-base-style-prompt="classic"><wa-tab data-style-category="ART" panel="art-panel"></wa-tab><wa-tab data-style-category="LOST" panel="lost-panel"></wa-tab><wa-tab-panel name="art-panel"></wa-tab-panel></wa-tab-group><div id="card_artist" class="card-overview" data-style-tags="[&quot;ART&quot;]"><wa-details><div data-sample-panel><img data-style-sample="artist" data-engine="zit"></div></wa-details><div data-image-tags data-style-tag="artist" data-engine="zit"></div></div><div class="card-overview"><img data-style-sample="lookup" data-engine="zit"></div><wa-tab-panel name="tag_LOST"><div id="card_bad" class="card-overview" data-style-tags="invalid-json"></div></wa-tab-panel><div data-character-placement data-style-tag="artist" data-engine="zit" data-base-style-prompt="classic"></div><input class="style-tier-slider" data-style-tag="artist" data-engine="zit"><wa-badge data-style-tag="artist" data-engine="zit"></wa-badge><div class="wa-frame:square"><img data-stockpile-filename="square.png"></div>');
        const {window, hooks} = h;
        const card = window.document.getElementById('card_artist');
        assert.equal(hooks.styleCardForJob({style_tag: 'artist'}), card);
        assert.equal(hooks.styleCardForJob({style_tag: 'lookup', engine: 'zit'}), window.document.querySelector('[data-style-sample=lookup]').closest('.card-overview'));
        assert.equal(hooks.styleCardForJob({}), null);
                assert.equal(await hooks.showStyleCard({style_tag: 'artist'}, card), card);
        assert.equal(hooks.queueDestination({style_tag: 'artist', engine: 'zit', action: 'styler_sample'}, card), card.querySelector('[data-sample-panel]'));
        assert.equal(hooks.queueDestination({style_tag: 'artist', engine: 'zit', action: 'styler_tag_sample'}, card), card.querySelector('[data-image-tags]'));
        assert.equal(hooks.queueDestination({filename: 'square.png'}, null), window.document.querySelector('.wa-frame\\:square'));
        window.console.warn = () => {};
                assert.equal(await hooks.showStyleCard({style_tag: 'bad'}, window.document.getElementById('card_bad')), window.document.getElementById('card_bad'));
        hooks.highlightImageTags(card);
        hooks.showStyleTags('artist', ['new tag'], []);
        assert.equal(card.querySelector('#tag_cloud_artist'), null);
        hooks.updateImageAssessment({style_tag: 'artist', engine: 'zit', character_placement: 'outside_image', image_tier: 3, tier_badge_html: '<wa-badge data-style-tag="artist" data-engine="zit">High</wa-badge>'});
        assert.match(card.parentElement.textContent, /High/);
        assert.equal(window.document.querySelector('.style-tier-slider').value, '3');

        const group = window.document.querySelector('[data-category-tab-group]');
        const tab = window.document.querySelector('wa-tab[data-style-category="ART"]');
        tab.click = () => {
            group.active = 'art-panel';
            window.document.body.insertAdjacentHTML('beforeend', '<div id="card_new" class="card-overview"></div>');
        };
        window.fetch = async () => ({ok: true, json: async () => ({tags: ['ART']})});
                assert.equal(await hooks.showStyleCard({style_tag: 'new'}, null), window.document.getElementById('card_new'));
        assert.equal(group.active, 'art-panel');

        group.active = 'art-panel';
        window.htmx.trigger = () => window.document.body.insertAdjacentHTML('beforeend', '<div id="card_htmx" class="card-overview"></div>');
                assert.equal(await hooks.showStyleCard({style_tag: 'htmx'}, null), window.document.getElementById('card_htmx'));

        group.remove();
        hooks.queueJobs.set('focus-fallback', {job_id: 'focus-fallback', style_tag: 'not-loaded', engine: 'zit', filename: 'square.png'});
        window.fetch = async () => ({ok: true, json: async () => ({tags: []})});
        const fallbackRow = hooks.queueTaskRow({job_id: 'focus-fallback', label: 'Fallback', state: 'queued'});
        await hooks.focusQueueDestination(fallbackRow);
        assert.ok(window.document.querySelector('[data-stockpile-filename="square.png"]').parentElement.classList.contains('styler-queue-destination-focus'));

        let destinationCalls = 0;
        const originalQueueDestination = hooks.queueDestination;
        const syntheticTarget = window.document.createElement('div');
        hooks.setQueueDestination(() => (++destinationCalls === 1 ? null : syntheticTarget));
        await hooks.focusQueueDestination(fallbackRow);
        assert.equal(destinationCalls, 2, 'The fallback destination is checked when the card target is absent');
        assert.ok(syntheticTarget.classList.contains('styler-queue-destination-focus'));
        hooks.setQueueDestination(originalQueueDestination);

        hooks.queueJobs.set('focus-artist', {job_id: 'focus-artist', style_tag: 'artist', engine: 'zit', action: 'styler_sample'});
        const focusRow = hooks.queueTaskRow({job_id: 'focus-artist', label: 'Sample image', state: 'queued'});
        await hooks.focusQueueDestination(focusRow);
        assert.equal(window.document.querySelector('#card_artist wa-details').open, true);
        assert.ok(window.document.querySelector('#card_artist [data-sample-panel]').classList.contains('styler-queue-destination-focus'));

        window.document.body.insertAdjacentHTML('beforeend', '<div id="card_lazy" class="card-overview"><wa-details></wa-details></div>');
        hooks.queueJobs.set('focus-lazy', {job_id: 'focus-lazy', style_tag: 'lazy', engine: 'zit', action: 'styler_sample'});
        const lazyRow = hooks.queueTaskRow({job_id: 'focus-lazy', label: 'Sample image', state: 'queued'});
        await hooks.focusQueueDestination(lazyRow);
        const lazyDetails = window.document.querySelector('#card_lazy wa-details');
        lazyDetails.insertAdjacentHTML('beforeend', '<div data-sample-panel><img data-style-sample="lazy" data-engine="zit"></div>');
        await new Promise(resolve => setImmediate(resolve));
        assert.ok(lazyDetails.querySelector('[data-sample-panel]').classList.contains('styler-queue-destination-focus'));
        await new Promise(resolve => setImmediate(resolve));
    }

    {
        const h = setup('<div data-style-entry><div id="card_artist" class="card-overview"><button class="delete-style-button" hx-vals="{&quot;style_tag&quot;:&quot;artist&quot;}"></button></div></div><button data-styles-scan></button><span data-styles-status></span><button data-style-duplicate-scan></button><span data-style-duplicate-status></span>');
        const {window, hooks, socket} = h;
        hooks.stylesStatus('Idle', false);
        hooks.styleDuplicateStatus('Idle', false);
        window.document.querySelector('[data-styles-scan]').click();
        assert.deepEqual(socket.messages.at(-1), {action: 'styles_scan'});
        assert.equal(window.document.querySelector('[data-styles-status]').textContent, 'Scanning styles…');
        window.document.querySelector('[data-style-duplicate-scan]').click();
        assert.deepEqual(socket.messages.at(-1), {action: 'style_duplicate_scan'});
        assert.equal(window.document.querySelector('[data-style-duplicate-status]').textContent, 'Style duplicate scan queued…');

        const deleteButton = window.document.querySelector('.delete-style-button');
        deleteButton.click();
        assert.equal(deleteButton.disabled, true);
        window.lastHtmxOptions.handler(null, {xhr: {status: 204, responseText: JSON.stringify({lost_count: 1})}});
        assert.equal(window.document.querySelector('[data-style-entry]'), null);

        const lostEvents = [];
        window.document.addEventListener('styler:category-added', event => lostEvents.push(event.detail));
        window.document.body.insertAdjacentHTML('beforeend', '<wa-tab-panel name="tag_LOST"><span data-lost-summary data-page="0" data-page-size="5"></span><div data-style-entry><div class="card-overview" id="card_lost"><button class="delete-style-button" hx-vals="{&quot;style_tag&quot;:&quot;lost&quot;}"></button></div></div></wa-tab-panel>');
        const lostDelete = window.document.querySelector('#card_lost .delete-style-button');
        lostDelete.click();
        window.lastHtmxOptions.handler(null, {xhr: {status: 200, responseText: JSON.stringify({lost_count: 2})}});
        assert.equal(window.document.querySelector('[data-lost-summary]').textContent, 'Showing 1–2 of 2 LOST styles');
        assert.equal(lostEvents.at(-1).count, 2);

        const rejected = window.document.createElement('button');
        rejected.className = 'delete-style-button';
        rejected.setAttribute('hx-vals', '{"style_tag":"other"}');
        const rejectedCard = window.document.createElement('div');
        rejectedCard.className = 'card-overview';
        rejectedCard.id = 'card_other';
        rejectedCard.append(rejected);
        window.document.body.append(rejectedCard);
        rejected.click();
        window.lastHtmxOptions.handler(null, {xhr: {status: 500, responseText: ''}});
        assert.equal(rejected.disabled, false);
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    {
        const h = setup('<wa-tab-panel name="tag_LOST"><div data-style-page data-style-page data-page-url="/styles/page" data-page-category="LOST" data-page="0" data-page-total="1"><span data-style-page-summary></span><div data-style-entry data-style-key="stale"></div></div></wa-tab-panel>');
        const {window, hooks} = h;
        const page = window.document.querySelector('[data-style-page]');
        window.fetch = async () => ({ok: true, json: async () => ({tag: 'LOST', page: 0, page_size: 5, total: 0, keys: [], entries: {}})});
        await hooks.refillStylePage(page);
        assert.equal(page.querySelector('[data-style-entry]'), null, 'A refill removes entries missing from the returned page');
        assert.equal(page.dataset.pageTotal, '0');
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    {
        const h = setup(`<button data-regenerate></button><div id="styler-queue-current"></div><div id="styler-queue-near"></div><div id="styler-queue-overflow" hidden></div><div class="styler-queue-body-inner"></div>
            <button id="styler-worker-reset"></button><span id="styler-worker-reset-status"></span>
            <button data-stockpile-scan></button><span data-stockpile-status></span>
            <button data-styles-scan></button><span data-styles-status></span>
            <button data-style-duplicate-scan></button><span data-style-duplicate-status></span>
            <div id="card_artist" class="card-overview"><wa-details open><div data-image-tags data-style-tag="artist" data-engine="zit" data-base-style-prompt="classic"></div></wa-details>
            <button data-style-retag="artist" data-engine="zit"></button><span class="retag-status"></span><div><button data-style-retag="offline" data-engine="qwen"></button><span class="retag-status"></span></div><span data-style-history-description data-style-tag="artist"></span>
            <span data-character-placement data-style-tag="artist" data-engine="zit" data-base-style-prompt="classic"></span>
            <input class="style-tier-slider" data-style-tag="artist" data-engine="zit"><wa-badge data-style-tag="artist" data-engine="zit"></wa-badge><span data-style-name-text></span><img data-style-sample="artist" data-engine="zit" data-base-style-prompt="classic"></div><div id="card_alias"></div>`);
        const {window, hooks, socket, emit} = h;
        const refreshes = [];
        window.document.body.addEventListener('stockpile-refresh', () => refreshes.push(true));
        socket.dispatchEvent(new window.Event('open'));
        const regen = window.document.querySelector('[data-regenerate]');
        regen.dispatchEvent(new window.CustomEvent('htmx:beforeRequest', {bubbles: true, detail: {elt: regen}}));
        assert.equal(hooks.regenerateScrollPositions.has(regen), true, 'beforeRequest captures scroll if pointerdown did not');
        regen.dispatchEvent(new window.CustomEvent('htmx:afterSettle', {bubbles: true, detail: {elt: regen}}));
        const details = window.document.querySelector('#card_artist wa-details');
        const otherDetails = window.document.createElement('wa-details');
        otherDetails.open = true;
        window.document.querySelector('#card_artist').append(otherDetails);
        details.open = true;
        details.dispatchEvent(new window.CustomEvent('wa-show', {bubbles: true}));
        assert.equal(otherDetails.open, false, 'Opening one card closes the others');
        assert.ok(socket.messages.some(message => message.action === 'missing_tags'));
        window.document.querySelector('#card_artist wa-details').open = true;

        emit({state: 'queue_plan', running: null, jobs: [{job_id: 'queued', label: 'Queued', state: 'queued'}], queue_count: 1, overflow_count: 0});
        const queuedRow = window.document.querySelector('[data-job-id="queued"]');
        queuedRow.querySelector('[data-queue-delete]').click();
        emit({state: 'queue_delete_rejected', job_id: 'queued', message: 'Still active'});
        assert.equal(queuedRow.querySelector('[data-queue-delete]').disabled, false);
        emit({state: 'job_promoted', job_id: 'queued', priority: true, message: 'Next up'});
        assert.equal(queuedRow.dataset.promoted, 'true');
        assert.equal(queuedRow.title, 'Next up');
        emit({state: 'job_promote_failed', job_id: 'unknown'});
        emit({state: 'queue_task_removed', job_id: 'queued'});
        assert.equal(window.document.querySelector('[data-job-id="queued"]'), null);

        emit({state: 'queue_plan', running: null, jobs: [{job_id: 'locate', label: 'Find artist', style_tag: 'artist', engine: 'zit', action: 'styler_sample', base_style_prompt: 'classic'}], queue_count: 1, overflow_count: 0});
        const locateRow = window.document.querySelector('[data-job-id="locate"]');
        const sentBeforeNavigation = socket.messages.length;
        locateRow.querySelector('[data-queue-find]').click();
        assert.equal(socket.messages.length, sentBeforeNavigation, 'Find navigates without sending a queue action');

        emit({state: 'queue_plan', running: null, jobs: [], queue_count: 0, overflow_count: 0,
            completed: [{job_id: 'completed', label: 'Completed', completed_at: Date.now() / 1000}]});
        const completedRow = window.document.querySelector('[data-job-id="completed"]');
        completedRow.querySelector('[data-queue-retry]').click();
        emit({state: 'retry_rejected', job_id: 'completed', message: 'Retry refused'});
        assert.equal(completedRow.querySelector('[data-queue-retry]').disabled, false);
        emit({state: 'retry_queued', job_id: 'completed', job: {job_id: 'completed', label: 'Again'}});
        assert.equal(window.document.querySelector('[data-job-id="completed"]').dataset.state, 'queued');

        emit({state: 'worker_reset_queued'});
        assert.equal(window.document.querySelector('#styler-worker-reset-status').textContent, 'Reset queued; after current task');
        emit({action: 'worker_reset', state: 'complete'});
        assert.equal(window.document.querySelector('#styler-worker-reset-status').textContent, 'Restarting worker…');
        emit({action: 'worker_reset', state: 'worker_ready'});
        assert.equal(window.document.querySelector('#styler-worker-reset-status').textContent, 'Worker ready');

        emit({state: 'style_duplicate_scan_queued', already_queued: true});
        assert.equal(window.document.querySelector('[data-style-duplicate-status]').textContent, 'Style duplicate scan already queued.');
        emit({action: 'styler_style_duplicate_scan', state: 'running', message: 'Checking aliases'});
        assert.equal(window.document.querySelector('[data-style-duplicate-status]').textContent, 'Checking aliases');
        window.document.getElementById('card_alias')?.remove();
        emit({state: 'style_duplicate_scan_complete', merged: [{canonical_key: 'artist', name: 'Artist', aliases: ['alias']}]});
        assert.equal(window.document.querySelector('[data-style-name-text]').textContent, 'Artist');
        emit({state: 'style_duplicate_scan_failed', message: 'Scan failed'});
        assert.equal(window.document.querySelector('[data-style-duplicate-status]').textContent, 'Scan failed');
        emit({action: 'styler_style_description', state: 'style_description_complete', style_tag: 'artist', description: 'Past notes'});
        assert.equal(window.document.querySelector('[data-style-history-description]').textContent, 'Past notes');
        emit({state: 'style_description_failed', style_tag: 'artist', message: 'No text'});
        assert.match(window.document.querySelector('[data-style-history-description]').textContent, /No text/);

        window.document.querySelector('[data-stockpile-scan]').click();
        emit({state: 'stockpile_queued', scanned: 3, complete: 1, jobs: ['s1'], errors: ['bad']});
        assert.match(window.document.querySelector('[data-stockpile-status]').textContent, /1 unreadable/);
        emit({state: 'stockpile_complete', job_id: 's1'});
        assert.equal(refreshes.length, 1);
        emit({state: 'stockpile_queued', scanned: 1, complete: 1, jobs: [], errors: []});
        assert.equal(refreshes.length, 2);
        emit({state: 'stockpile_scan_failed', message: 'Could not scan'});
        assert.equal(window.document.querySelector('[data-stockpile-status]').textContent, 'Could not scan');
        socket.readyState = 3;
        window.document.querySelector('[data-stockpile-scan]').disabled = false;
        window.document.querySelector('[data-stockpile-scan]').click();
        assert.match(window.document.querySelector('[data-stockpile-status]').textContent, /Disconnected/);
        window.document.querySelector('[data-styles-scan]').disabled = false;
        window.document.querySelector('[data-styles-scan]').click();
        assert.match(window.document.querySelector('[data-styles-status]').textContent, /Disconnected/);
        window.document.querySelector('[data-style-duplicate-scan]').disabled = false;
        window.document.querySelector('[data-style-duplicate-scan]').click();
        assert.match(window.document.querySelector('[data-style-duplicate-status]').textContent, /Disconnected/);
        socket.readyState = 1;

        window.document.querySelector('[data-styles-scan]').click();
        emit({state: 'styles_scan_queued', version: 4, jobs: ['i1', 'i2']});
        emit({state: 'styles_scan_image_failed', job_id: 'i1'});
        assert.match(window.document.querySelector('[data-styles-status]').textContent, /1\/2 processed, 1 failed/);
        emit({state: 'styles_scan_image_complete', job_id: 'i2', style_tag: 'artist', engine: 'zit', image_tags: ['new image tag']});
        assert.equal(window.document.querySelector('[data-image-tags] wa-tag').textContent, 'new image tag');
        emit({state: 'styles_scan_complete', scan_version: 4});
        assert.match(window.document.querySelector('[data-styles-status]').textContent, /version 4 complete/);
        emit({state: 'styles_scan_failed', message: 'Scan failed'});
        assert.equal(window.document.querySelector('[data-styles-status]').textContent, 'Scan failed');
        emit({state: 'styles_scan_queued', version: 5, jobs: []});
        assert.match(window.document.querySelector('[data-styles-status]').textContent, /complete/);

        emit({state: 'retag_queued', style_tag: 'artist', engines: ['zit']});
        emit({state: 'retag_failed', style_tag: 'artist', engine: 'zit', message: 'Retag error'});
        assert.equal(window.document.querySelector('.retag-status').textContent, 'Retag error');
        hooks.retagging.set(hooks.sampleKey('artist', 'zit'), {style_tag: 'artist', engine: 'zit', failed: false});
        emit({state: 'tag_failed', style_tag: 'artist', engine: 'zit'});
        assert.equal(window.document.querySelector('.retag-status').textContent, 'Retag failed');
        hooks.retagging.set(hooks.sampleKey('artist', 'zit'), {style_tag: 'artist', engine: 'zit', failed: false});
        emit({state: 'tag_abyss', style_tag: 'artist', engine: 'zit'});
        assert.equal(window.document.querySelector('.retag-status').textContent, 'Style needs manual tag repair');
        emit({style_tag: 'artist', engine: 'zit', state: 'running', job_id: 'sample-fail', percent: null});
        emit({style_tag: 'artist', engine: 'zit', state: 'failed', job_id: 'sample-fail', message: 'GPU failed'});
        assert.match(window.document.querySelector('[data-style-sample]').textContent, /Sample failed/);
        emit({state: 'loaded', filename: '/cached.png', style_tag: 'artist', engine: 'zit', image_tags: []});
        assert.ok(socket.messages.some(message => message.action === 'retag'));
        assert.equal(window.document.querySelector('.styler-image-open img').getAttribute('src'), '/cached.png');
        socket.readyState = 3;
        assert.equal(hooks.requestRetag('offline', 'qwen'), false);
        assert.match(window.document.querySelectorAll('.retag-status')[1].textContent, /Disconnected/);
        await new Promise(resolve => setImmediate(resolve));
        h.dom.window.close();
    }

    console.log('Styler samples contract helper tests passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
