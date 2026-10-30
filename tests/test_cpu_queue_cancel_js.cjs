const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag;
        this.children = [];
        this.dataset = {};
        this.events = {};
        this.className = '';
        this.attributes = {};
        this.classList = {contains: name => this.className.split(' ').includes(name),
            add: name => {this.className += ` ${name}`;}, toggle: () => false};
    }
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children = children;}
    setAttribute(name, value) {this.attributes[name] = value;}
    getAttribute(name) {return this.attributes[name];}
    addEventListener(name, callback) {this.events[name] = callback;}
    querySelectorAll(selector) {
        const matches = child => selector.startsWith('.')
            ? child.className.split(' ').includes(selector.slice(1).split('[')[0])
            : selector.startsWith('[data-')
                ? selector.slice(6, -1).replace(/-([a-z])/g, (_, char) => char.toUpperCase()) in child.dataset
                : child.tagName === selector;
        return this.children.flatMap(child => [
            ...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) {return this.querySelectorAll(selector)[0];}
}

const elements = {};
for (const name of ['cpu-job-queue', 'cpu-queue-toggle', 'cpu-queue-count', 'cpu-queue-summary',
    'cpu-queue-renders', 'cpu-queue-running', 'cpu-queue-pending', 'cpu-queue-failed', 'cpu-queue-completed']) {
    elements[name] = new Element();
}
const panel = elements['cpu-job-queue'];
panel.append(...Object.values(elements).filter(item => item !== panel));
const events = {};
const sent = [];
const document = {
    getElementById: id => elements[id], createElement: tag => new Element(tag),
    addEventListener: (type, callback) => {(events[type] ||= []).push(callback);},
    dispatchEvent: event => {
        if (event.type === 'cpu:cancel-send') sent.push(event.detail);
        for (const callback of events[event.type] || []) callback(event);
    },
};
let allow = false;
let confirmations = 0;
const context = {
    document, window: {confirm: () => {confirmations++; return allow;}},
    CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail;}},
    setTimeout: () => {}, navigator: {},
};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/cpu-queue.js', 'utf8'), context);
const emit = (type, detail) => document.dispatchEvent({type, detail});
emit('cpu:queue-plan', {workers: 2, running_count: 1, queued_count: 1,
    running: [{job_id: 'running', state: 'running', label: 'Running task', cancellable: true}],
    jobs: [{job_id: 'queued', state: 'queued', label: 'Queued task', cancellable: true}], renders: []});
const queued = elements['cpu-queue-pending'].children[0];
const running = elements['cpu-queue-running'].children[0];
queued.querySelector('[data-cpu-cancel]').events.click();
assert.equal(sent.length, 1);
assert.equal(sent[0].job_id, 'queued');
assert.equal(sent[0].allow_running, false);
assert.equal(confirmations, 0);
running.querySelector('[data-cpu-cancel]').events.click();
assert.equal(sent.length, 1, 'Declining running cancellation sends no request');
allow = true;
running.querySelector('[data-cpu-cancel]').events.click();
assert.equal(sent.length, 2);
assert.equal(sent[1].job_id, 'running');
assert.equal(sent[1].allow_running, true);
emit('cpu:cancel-result', {state: 'cpu_cancel_rejected', job_id: 'running', message: 'Disconnected'});
assert.equal(running.querySelector('[data-cpu-cancel]').disabled, false);
assert.equal(running.querySelector('.cpu-queue-message').textContent, 'Disconnected');
emit('cpu:queue-plan', {workers: 2, running_count: 0, queued_count: 0, renders: [],
    completed: [{job_id: 'running', state: 'cancelled', message: 'Cancelled by user', retryable: true}]});
const cancelled = elements['cpu-queue-completed'].children[0];
assert.equal(cancelled.querySelector('[data-cpu-cancel]'), undefined);
assert.equal(cancelled.querySelector('wa-progress-bar'), undefined);
assert.ok(cancelled.querySelector('[data-cpu-retry]'));

// Exercise the actual socket sender with connected and disconnected sockets.
const transportSource = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
const start = transportSource.indexOf("    document.addEventListener('cpu:cancel-send'");
const end = transportSource.indexOf('    // Reset connection-scoped', start);
let socketMessages = [];
context.socket = {readyState: 1, send: value => socketMessages.push(JSON.parse(value))};
context.WebSocket = {OPEN: 1};
vm.runInNewContext(transportSource.slice(start, end), context);
emit('cpu:cancel-send', {job_id: 'running', allow_running: true});
assert.deepEqual(socketMessages[0], {action: 'cancel_cpu_job', job_id: 'running', allow_running: true});
context.socket.readyState = 3;
emit('cpu:cancel-send', {job_id: 'running', allow_running: true});
assert.equal(socketMessages.length, 1, 'Disconnected sockets cannot silently discard cancellation');
console.log('CPU cancellation controls and transport passed');
