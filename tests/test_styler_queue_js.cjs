const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
function setup(height = 400, reducedMotion = true, sampleMarkup = '') {
    const dom = new JSDOM(`<script data-styler-websocket="ws://example/styler-ws"></script>
        <style>.styler-queue-task {margin-bottom: 4px;}</style><span id="styler-queue-count"></span><div class="styler-queue-body-inner">
        <div id="styler-queue-current"></div><div id="styler-queue-near"></div><div id="styler-queue-overflow" hidden></div></div>${sampleMarkup}`, {runScripts: 'outside-only'});
    const {window} = dom;
    Object.defineProperty(window.document, 'currentScript', {value: window.document.querySelector('script')});
    window.matchMedia = () => ({matches: reducedMotion});
    let now = 1000000;
    window.Date.now = () => now;
    const timers = new Map();
    let timerId = 0;
    window.setTimeout = (callback, delay) => {timers.set(++timerId, {callback, due: now + delay}); return timerId;};
    window.clearTimeout = id => timers.delete(id);
    const animations = [];
    window.HTMLElement.prototype.animate = function (frames, options) {
        const animation = {row: this, frames, options, cancel() {}, finished: Promise.resolve()};
        animations.push(animation);
        return animation;
    };
    window.HTMLElement.prototype.getBoundingClientRect = function () {
        if (this.id === 'styler-queue-overflow') return {height: 32};
        const siblings = [...this.parentElement.children];
        let top = siblings.indexOf(this) * 40;
        if (this.parentElement.id === 'styler-queue-near') top += window.document.querySelector('#styler-queue-current').children.length * 40;
        return {left: 0, top, bottom: top + 36, height: 36, width: 300};
    };
    let viewportHeight = height;
    Object.defineProperty(window.document.querySelector('#styler-queue-near'), 'clientHeight', {get: () => Math.max(0, viewportHeight - window.document.querySelector('#styler-queue-current').children.length * 40 - (window.document.querySelector('#styler-queue-overflow').hidden ? 0 : 32))});
    Object.defineProperty(window.document.querySelector('.styler-queue-body-inner'), 'clientHeight', {get: () => viewportHeight});
    let resize;
    window.ResizeObserver = class {constructor(callback) {resize = callback;} observe() {}};
    class Socket extends window.EventTarget {
        static OPEN = 1;
        constructor() {super(); this.readyState = 1; this.messages = []; Socket.instance = this;}
        send(data) {this.messages.push(JSON.parse(data));}
        close() {}
    }
    window.WebSocket = Socket;
    new vm.Script(source, {filename: require('node:path').resolve('src/artifact_editor/static/js/styler-samples.js')})
        .runInContext(dom.getInternalVMContext());
    const socket = Socket.instance;
    const emit = event => socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify(event)}));
    const job = id => ({job_id: id, label: id, action: 'styler_sample'});
    const plan = (running, jobs, extra = {}) => emit({state: 'queue_plan', running: running && job(running), jobs: jobs.map(job), queue_count: jobs.length + Boolean(running), overflow_count: 0, ...extra});
    const complete = (id, completedAt = now / 1000) => emit({...job(id), state: 'complete', queue_completed: true, completed_at: completedAt});
    const rows = selector => [...window.document.querySelectorAll(selector || '.styler-queue-task[data-job-id]')];
    const ids = selector => rows(selector).map(row => row.dataset.jobId);
    const advance = ms => {
        now += ms;
        for (const [id, timer] of [...timers]) if (timer.due <= now) {timers.delete(id); timer.callback();}
    };
    return {window, socket, plan, emit, complete, job, ids, rows, animations, advance,
        resize: height => {viewportHeight = height; resize();}, close: () => {}};
}
const pending = count => Array.from({length: count}, (_, index) => `p${index}`);
{
    const h = setup();
    h.plan('running', pending(20), {queue_count: 31, overflow_count: 10});
    for (let index = 0; index < 5; index++) h.complete(`done${index}`, 995 + index);
    assert.deepEqual(h.ids('#styler-queue-current [data-job-id]'), ['done2', 'done3', 'done4', 'running']);
    assert.equal(h.rows('[data-next="true"]').length, 1);
    assert.equal(h.rows('[data-state="running"]').length, 1);
    const visible = h.rows('#styler-queue-near [data-job-id]').length;
    assert.equal(visible, 5, 'Three completions, running, next and pending fill available space');
    assert.equal(h.window.document.querySelector('#styler-queue-overflow').textContent, '25 more tasks queued');
    h.resize(640);
    assert.equal(h.rows('[data-state="complete"]').length, 3, 'A resize recalculates visible rows without waiting for a plan');
    assert.equal(h.rows('#styler-queue-near [data-job-id]').length, 11);
    h.plan('running', ['only']);
    assert.deepEqual(h.ids('#styler-queue-current [data-job-id]'), ['done0', 'done1', 'done2', 'done3', 'done4', 'running']);
    assert.equal(h.window.document.querySelector('#styler-queue-overflow').hidden, true);
    h.plan(null, []);
    assert.equal(h.window.document.querySelector('#styler-queue-count').hidden, true, 'Zero total is hidden');
    h.plan('running', []);
    assert.equal(h.window.document.querySelector('#styler-queue-count').hidden, false);
    h.close();
}
{
    const h = setup(800, false);
    h.plan('a', ['b', 'c']);
    h.advance(700);
    const original = h.rows('[data-job-id="a"]')[0];
    const progress = original.querySelector('wa-progress-bar');
    h.complete('a');
    h.plan('b', ['c']);
    assert.equal(h.rows('[data-job-id="a"]')[0], original);
    assert.equal(original.querySelector('wa-progress-bar'), progress, 'Progress bar survives completion');
    const running = h.rows('[data-job-id="b"]')[0];
    const runningProgress = running.querySelector('wa-progress-bar');
    h.plan('b', ['priority', 'c']);
    assert.equal(h.rows('[data-next="true"]')[0].dataset.jobId, 'priority', 'Priority insertion is immediate during animation');
    assert.equal(h.rows('[data-job-id="b"]')[0], running);
    assert.equal(running.querySelector('wa-progress-bar'), runningProgress);
    h.plan('b', ['priority', 'c'], {running: {...h.job('b'), percent: 61}});
    assert.equal(runningProgress.getAttribute('value'), '61');
    h.plan('b', ['priority', 'c'], {running: {...h.job('b'), percent: 40}});
    assert.equal(runningProgress.getAttribute('value'), '61', 'Progress never moves backward');
    assert.ok(h.animations.every(animation => animation.frames.every(frame => !('height' in frame))), 'Animations never change row height');
    h.advance(30000);
    assert.equal(h.rows('[data-job-id="a"]').length, 0);
    const expiringRow = h.window.document.querySelector('.styler-queue-task-expiring');
    assert.ok(expiringRow, 'Expired completions get a separate dissolve element');
    assert.equal(expiringRow.getAttribute('aria-hidden'), 'true');
    h.close();
}
{
    const h = setup(800);
    for (let index = 0; index < 20; index++) h.complete(`batch-${index}`, 995 + index / 100);
    assert.deepEqual(h.ids(), ['batch-15', 'batch-16', 'batch-17', 'batch-18', 'batch-19']);
    h.complete('batch-19', 995.19);
    assert.equal(h.rows().length, 5, 'Repeated history snapshots do not duplicate entries');
    h.emit({...h.job('cached'), state: 'loaded', filename: '/cached.png'});
    assert.equal(h.rows('[data-job-id="cached"]').length, 0, 'Loaded samples do not enter queue history');
    h.close();
}
{
    const h = setup(800);
    h.plan(null, [], {completed: [{...h.job('from-snapshot'), completed_at: 990}]});
    assert.ok(h.ids().includes('from-snapshot'), 'Fresh connections receive recent worker completions');
    h.advance(20000);
    assert.ok(!h.ids().includes('from-snapshot'), 'Snapshot history keeps its original expiry');
    h.plan(null, [], {completed: [{...h.job('too-old'), completed_at: 980}]});
    assert.ok(!h.ids().includes('too-old'));
    h.close();
}
{
    const h = setup(400);
    h.plan(null, pending(9));
    assert.equal(h.rows().length, 9, 'Initial small queues appear in full when they fit');
    assert.equal(h.window.document.querySelector('#styler-queue-overflow').hidden, true);
    const reports = h.socket.messages.filter(message => message.action === 'queue_capacity').length;
    h.socket.dispatchEvent(new h.window.Event('open'));
    assert.equal(h.socket.messages.filter(message => message.action === 'queue_capacity').length, reports + 1);
    h.socket.dispatchEvent(new h.window.Event('open'));
    assert.equal(h.socket.messages.filter(message => message.action === 'queue_capacity').length, reports + 2, 'Reconnect always resends capacity');
    h.close();
}
{
    const h = setup(400, true, '<img data-style-sample="artist" data-engine="qwen">');
    const statuses = [];
    h.window.document.addEventListener('styler:sample-status', event => statuses.push(event.detail));
    h.socket.dispatchEvent(new h.window.Event('open'));
    h.emit({job_id:'cached', style_tag:'artist', engine:'qwen', state:'loaded', filename:'/styles/show_sample/artist_qwen_sampler.png', prompt_tags:['ink']});
    assert.equal(h.window.document.querySelector('.styler-image-open img').getAttribute('src'), '/styles/show_sample/artist_qwen_sampler.png');
    assert.equal(statuses.at(-1).state, 'loaded', 'Loaded images notify the viewer through the ready/status path');
    assert.equal(h.rows().length, 0, 'Loaded images do not create completed queue jobs');
    h.close();
}
{
    const h = setup(800);
    h.plan('running', pending(10), {overflow_count: 20, queue_count: 31});
    assert.ok(h.socket.messages.some(message => message.action === 'queue_capacity' && message.rows > 10), 'Spare room fetches more than the initial ten pending tasks');
    h.close();
}
{
    const h = setup(800);
    h.plan('running', ['first', 'clicked']);
    h.rows('[data-job-id="clicked"]')[0].click();
    assert.deepEqual(h.socket.messages.at(-1), {action:'queue_promote', job_id:'clicked', priority:true});
    assert.equal(h.rows('[data-job-id="clicked"]')[0].dataset.promoted, 'true');
    h.plan('running', ['clicked', 'first'], {jobs:[{...h.job('clicked'), promoted:true}, h.job('first')]});
    assert.equal(h.rows('[data-next="true"]')[0].dataset.jobId, 'clicked');
    assert.equal(h.rows('[data-job-id="clicked"]')[0].dataset.promoted, 'true');
    h.plan('clicked', ['first'], {running:{...h.job('clicked'), promoted:true, percent:50}});
    assert.equal(h.rows('[data-state="running"]')[0].dataset.promoted, 'true');
    h.complete('clicked');
    h.plan('first', []);
    assert.equal(h.rows('[data-job-id="clicked"]')[0].dataset.promoted, 'true', 'Golden glow survives completion');
    h.plan('first', ['keyboard']);
    h.rows('[data-job-id="keyboard"]')[0].dispatchEvent(new h.window.KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
    assert.deepEqual(h.socket.messages.at(-1), {action:'queue_promote', job_id:'keyboard', priority:true});
    h.emit({state:'job_promote_failed', job_id:'keyboard', message:'Task is no longer pending'});
    assert.equal(h.rows('[data-job-id="keyboard"]')[0].dataset.promoted, 'false');
    h.close();
}
{
    const h = setup(400, true, '<div class="styler-queue-rate" data-mode="walk"><wa-slider id="styler-maintenance-rate" min="0" max="2" step="1" value="1" disabled></wa-slider></div>');
    const slider = h.window.document.getElementById('styler-maintenance-rate');
    h.plan(null, [], {maintenance_mode:'walk'});
    assert.equal(slider.value, 1);
    assert.equal(slider.disabled, false);
    slider.value = 0;
    slider.dispatchEvent(new h.window.Event('input', {bubbles:true}));
    assert.deepEqual(h.socket.messages.at(-1), {action:'maintenance_mode', mode:'off'});
    assert.equal(slider.getAttribute('aria-valuetext'), 'Off');
    const sent = h.socket.messages.length;
    slider.dispatchEvent(new h.window.Event('change', {bubbles:true}));
    assert.equal(h.socket.messages.length, sent, 'Input/change do not send the same mode twice');
    h.emit({state:'maintenance_mode', mode:'off'});
    h.emit({state:'maintenance_mode', mode:'run'});
    assert.equal(slider.value, 2, 'Other windows update the slider');
    assert.equal(slider.closest('.styler-queue-rate').dataset.mode, 'run');
    h.plan(null, [], {maintenance_mode:'off'});
    assert.equal(slider.value, 0, 'Reconnect snapshots restore the shared setting');
    slider.value = 2;
    slider.dispatchEvent(new h.window.Event('change', {bubbles:true}));
    h.plan(null, [], {maintenance_mode:'off'});
    assert.equal(slider.value, 2, 'A stale snapshot cannot override an unacknowledged choice');
    h.emit({state:'maintenance_mode', mode:'run'});
    assert.equal(slider.value, 2);
    h.socket.readyState = 3;
    h.socket.dispatchEvent(new h.window.Event('close'));
    assert.equal(slider.disabled, true, 'Disconnected controls cannot misrepresent a changed server setting');
    h.close();
}
{
    const h = setup(800);
    h.plan('oom', []);
    h.emit({...h.job('oom'), state:'failed', queue_failed:true, completed_at:1000, message:'CUDA out of memory'});
    h.plan(null, []);
    let failed = h.rows('[data-job-id="oom"]')[0];
    assert.equal(failed.dataset.state, 'failed');
    assert.equal(failed.querySelector('.styler-queue-task-state').textContent, 'failed');
    assert.equal(failed.querySelector('wa-progress-bar'), null);
    assert.equal(failed.title, 'CUDA out of memory');
    h.advance(31000);
    assert.ok(h.ids().includes('oom'), 'Failures outlive the success history');
    for (let i = 0; i < 8; i++) h.complete(`success-${i}`, 1031 + i / 100);
    assert.ok(h.ids().includes('oom'), 'New successes cannot evict a failure');
    h.advance(268000);
    assert.ok(h.ids().includes('oom'), 'Failure remains just before five minutes');
    h.advance(1001);
    assert.ok(!h.ids().includes('oom'), 'Failure expires after five minutes');
    h.close();
}
{
    const h = setup(800);
    h.plan(null, [], {failed:[{...h.job('snapshot-oom'), state:'failed', completed_at:800, message:'Out of memory'}]});
    assert.ok(h.ids().includes('snapshot-oom'), 'Refreshing restores failures older than thirty seconds');
    h.advance(99000);
    assert.ok(h.ids().includes('snapshot-oom'));
    h.advance(1001);
    assert.ok(!h.ids().includes('snapshot-oom'), 'Refresh does not restart the five-minute expiry');
    h.plan(null, [], {failed:[{...h.job('retry'), state:'failed', completed_at:1100}]});
    h.plan('other', ['retry']);
    assert.equal(h.rows('[data-job-id="retry"]')[0].dataset.state, 'queued', 'Queued retries clear failed history immediately');
    h.close();
}
{
    const h = setup(800);
    h.plan(null, [], {failed:[{...h.job('retry-running'), state:'failed', completed_at:999}]});
    h.plan('retry-running', [], {running:{...h.job('retry-running'), queue_started_at:1000, percent:0}});
    assert.equal(h.rows('[data-job-id="retry-running"]')[0].dataset.state, 'running');
    h.plan(null, [], {failed:[{...h.job('hidden-retry'), state:'failed', completed_at:999}]});
    h.plan('other', [], {failed:[], overflow_count:20, queue_count:21});
    assert.ok(!h.ids().includes('hidden-retry'), 'Authoritative snapshots clear failures retried outside the visible queue slice');
    h.close();
}
for (const state of ['failed', 'tag_failed', 'tag_abyss', 'stockpile_failed', 'styles_scan_image_failed']) {
    const h = setup(800);
    h.plan('failure', []);
    h.emit({...h.job('failure'), state, queue_failed:true, completed_at:1000, message:'Processing failed'});
    h.plan(null, []);
    assert.equal(h.rows('[data-job-id="failure"]')[0].dataset.state, 'failed', `${state} is retained as a failed row`);
    h.close();
}
console.log('Styler queue layout, chronology, priority, progress, expiry, loading and reconnect tests passed');
(async () => {
    const h = setup(800, true, '<img data-stockpile-filename="stock.png">');
    const scrolled = [];
    h.window.requestAnimationFrame = callback => callback();
    h.window.HTMLElement.prototype.scrollIntoView = function () {scrolled.push(this);};

    h.plan('running', ['stock', 'unsupported'], {
        jobs: [
            {...h.job('stock'), filename: 'stock.png'},
            {...h.job('unsupported'), action: 'worker_rpc'},
        ],
    });
    let row = h.rows('[data-job-id="stock"]')[0];
    const find = row.querySelector('[data-queue-find]');
    assert.ok(find, 'A job with a visible stockpile target gets a find button');
    assert.equal(h.rows('[data-job-id="unsupported"]')[0].querySelector('[data-queue-find]'), null);
    const sentBeforeFind = h.socket.messages.length;
    find.click();
    await Promise.resolve();
    assert.equal(h.socket.messages.length, sentBeforeFind, 'Find does not promote the task');
    assert.equal(scrolled.at(-1), h.window.document.querySelector('[data-stockpile-filename]'));

    row.click();
    assert.deepEqual(h.socket.messages.at(-1), {action: 'queue_promote', job_id: 'stock', priority: true});
    h.emit({state: 'job_promoted', job_id: 'stock', promoted: true, priority: true});
    assert.equal(row.dataset.promoted, 'true');
    row.dispatchEvent(new h.window.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    assert.deepEqual(h.socket.messages.at(-1), {action: 'queue_promote', job_id: 'stock', priority: false});
    h.emit({state: 'job_demoted', job_id: 'stock', promoted: false, priority: false});
    assert.equal(row.dataset.promoted, 'false');

    const image = h.window.document.querySelector('[data-stockpile-filename]');
    image.remove();
    await Promise.resolve();
    assert.equal(row.querySelector('[data-queue-find]'), null, 'Removing a stockpile target removes its find button');
    const replacement = h.window.document.createElement('img');
    replacement.dataset.stockpileFilename = 'stock.png';
    h.window.document.body.append(replacement);
    await Promise.resolve();
    assert.ok(row.querySelector('[data-queue-find]'), 'Adding a target restores its find button');

    h.plan(null, [], {completed: [{...h.job('done'), completed_at: 1000}]});
    const completed = h.rows('[data-job-id="done"]')[0];
    const retry = completed.querySelector('[data-queue-retry]');
    assert.ok(retry, 'Completed history entries can be retried');
    retry.click();
    assert.deepEqual(h.socket.messages.at(-1), {action: 'retry_job', job_id: 'done'});
    h.emit({state: 'retry_queued', job_id: 'done', job: {...h.job('done'), state: 'queued'}});
    assert.equal(h.rows('[data-job-id="done"]')[0].dataset.state, 'queued');
    const deleteButton = h.rows('[data-job-id="done"]')[0].querySelector('[data-queue-delete]');
    deleteButton.click();
    assert.deepEqual(h.socket.messages.at(-1), {action: 'queue_delete', job_id: 'done'});
    h.emit({state: 'queue_task_removed', job_id: 'done'});
    assert.equal(h.rows('[data-job-id="done"]').length, 0);
    await new Promise(resolve => setImmediate(resolve));
    h.close();
    console.log('Queue target navigation, priority, retry and delete actions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
