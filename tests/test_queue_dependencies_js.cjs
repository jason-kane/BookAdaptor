const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Geometry-focused DOM fixture: exercise the same lifecycle as the live queues,
// including clipping, minimization, row animation, and terminal job states.
class Element {
    constructor(rect = {left: 0, right: 1200, top: 0, bottom: 800, width: 1200, height: 800}) {
        this.rect = rect;
        this.dataset = {};
        this.attributes = new Map();
        this.children = [];
        this.hidden = false;
        this.parentElement = null;
        this.style = {visibility: 'visible', overflowX: 'visible', overflowY: 'visible'};
        const classes = new Set();
        this.classList = {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)};
    }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name); }
    getBoundingClientRect() { return this.rect; }
    getClientRects() { return this.hidden ? [] : [this.rect]; }
    append(...nodes) { for (const node of nodes) {node.parentElement = this; this.children.push(node);} }
    remove() { this.parentElement.children = this.parentElement.children.filter(node => node !== this); }
    querySelectorAll() { return this.rows || []; }
}
const cpu = new Element({left: 600, right: 900, top: 200, bottom: 780, width: 300, height: 580});
const gpu = new Element({left: 940, right: 1190, top: 200, bottom: 780, width: 250, height: 580});
const overlay = new Element();
const cpuRow = new Element({left: 610, right: 890, top: 300, bottom: 360, width: 280, height: 60});
cpuRow.dataset = {jobId: 'cpu-render', state: 'running'};
cpu.append(cpuRow);
cpu.rows = [cpuRow];
const gpuRow = new Element({left: 950, right: 1180, top: 450, bottom: 490, width: 230, height: 40});
gpuRow.dataset = {jobId: 'gpu-child', parentJobId: 'cpu-render', state: 'running'};
gpu.append(gpuRow);
const unrelated = new Element(gpuRow.rect);
unrelated.dataset = {jobId: 'unrelated-child', parentJobId: 'another-render', state: 'running'};
gpu.append(unrelated);
gpu.rows = [gpuRow, unrelated];
const elements = {'cpu-job-queue': cpu, 'styler-job-queue': gpu, 'queue-dependency-lines': overlay};
let frame;
const observers = [];
const events = {};
const context = {
    document: {getElementById: id => elements[id], createElementNS: () => new Element(), addEventListener: (type, callback) => {events[type] = callback;}},
    window: {innerWidth: 1200, innerHeight: 800, addEventListener: (type, callback) => {events[type] = callback;}},
    requestAnimationFrame: callback => {frame = callback;},
    getComputedStyle: node => node.style,
    MutationObserver: class {constructor(callback) {observers.push(callback);} observe() {}},
};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/queue-dependencies.js', 'utf8'), context);
function tick() { const next = frame; frame = null; assert.ok(next, 'An update is scheduled'); next(); }
function mutate() { observers.forEach(callback => callback()); tick(); }
tick();
assert.equal(overlay.children.length, 1, 'Connect the real dependency, ignoring unrelated GPU jobs');
let connection = overlay.children[0];
assert.equal(connection.dataset.cpuJobId, 'cpu-render');
assert.equal(connection.dataset.gpuJobId, 'gpu-child');
assert.equal(connection.children[0].getAttribute('d'), 'M 890 330 C 920 330, 920 470, 950 470');
assert.equal(connection.children[1].getAttribute('cx'), '890');
assert.equal(connection.children[1].getAttribute('cy'), '330');
assert.equal(connection.children[2].getAttribute('cx'), '950');
assert.equal(connection.children[2].getAttribute('cy'), '470');
gpuRow.rect = {...gpuRow.rect, top: 500, bottom: 540};
tick();
assert.equal(overlay.children[0], connection, 'Keep the same connector while a row animates');
assert.equal(connection.children[2].getAttribute('cy'), '520');

cpu.classList.add('is-minimized');
tick();
assert.equal(overlay.children.length, 0, 'Hide connectors for minimized queues');
cpu.classList.remove('is-minimized');
mutate();
assert.equal(overlay.children.length, 1);

gpu.style.overflowY = 'hidden';
gpuRow.rect = {...gpuRow.rect, top: 790, bottom: 830};
tick();
assert.equal(overlay.children.length, 0, 'Do not connect to a row clipped by the GPU queue');
gpuRow.rect = {...gpuRow.rect, top: 450, bottom: 490};
events.scroll();
tick();
assert.equal(overlay.children.length, 1);

gpuRow.dataset.state = 'complete';
tick();
assert.equal(overlay.children.length, 0, 'Remove a fulfilled dependency');
assert.equal(frame, null, 'Stop animation-frame polling when no dependency is visible');
gpuRow.dataset.state = 'queued';
mutate();
assert.equal(overlay.children.length, 1, 'A queued GPU task is also a dependency');
cpuRow.dataset.state = 'failed';
tick();
assert.equal(overlay.children.length, 0, 'A failed CPU parent is no longer waiting');
console.log('Queue dependency geometry, identity matching, movement, clipping, and lifecycle passed');
