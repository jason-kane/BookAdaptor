const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let now = 100000;
const context = {window: {setInterval() {}}, document: {readyState: 'loading', addEventListener() {}},
    Date: {now: () => now}};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/task-timing.js', 'utf8'), context);
const timing = context.window.TaskTiming;
const job = {job_id: 'a', state: 'running', percent: 99, timing: {as_of: 100,
    count: 3, min_seconds: 10, avg_seconds: 20, max_seconds: 30,
    samples_seconds: [10, 20, 30], elapsed_seconds: 5, unit: 'task'}};
assert.equal(timing.describe(job).label, '~15s left');
assert.equal(timing.describe({...job, percent: 1}).label, '~15s left');
assert.equal(timing.describe(job, 20).label, '~5s left', 'Use surviving longer observations');
assert.equal(timing.describe(job, 26).label, 'Longer than observed · 31s elapsed');
assert.notEqual(timing.describe(job, 26).value, 100, 'Never claim completion before an outcome');
assert.match(timing.describe(job).title, /Min 10s · Avg 20s · Max 30s · 3 runs/);
assert.equal(timing.describe({state: 'running', timing: {elapsed_seconds: 5}}).label, 'Learning duration · 5s elapsed');
assert.equal(timing.describe({state: 'queued', timing: {eta_seconds: 45}}, 5).label, 'Queued · ~40s to finish');
assert.equal(timing.describe({...job, state: 'complete', duration_seconds: 12}).label, 'Done · 12s');
assert.equal(timing.describe({...job, state: 'cancelled', duration_seconds: 8}, 99).label, 'Cancelled · 8s');
assert.equal(timing.describe({...job, state: 'cancelled', duration_seconds: 8}).value, null);
assert.equal(timing.duration(0.003), '3ms');
assert.equal(timing.cleanMessage('Rendering (99%) — frames'), 'Rendering frames');
timing.remember({...job, parent_job_id: 'render'});
assert.equal(timing.describe({job_id: 'render', state: 'running'}).label, 'GPU · ~15s left');
now += 5000;
timing.enrich({job_id: 'a', state: 'running', percent: 100});
assert.equal(timing.describe({job_id: 'render', state: 'running'}).label, 'GPU · ~10s left', 'Progress messages must not restart the timing clock');
console.log('Historical ETA, queue wait, overrun, uncertainty, and clock behavior passed');
