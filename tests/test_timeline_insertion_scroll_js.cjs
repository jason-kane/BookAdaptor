const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/artifact_editor/static/js/chapter-timeline.js', 'utf8');
const listeners = {}, viewportEvents = {}, frames = new Map();
let frameId = 0, resize, disconnected = false;
const viewport = {scrollTop: 1800, getBoundingClientRect: () => ({top: 100}),
    addEventListener: (name, fn) => {viewportEvents[name] = fn;},
    removeEventListener: name => {delete viewportEvents[name];}};
let anchorTop = 180;
const anchor = {id: 'phrase_40', getBoundingClientRect: () => ({top: anchorTop})};
const elements = {'chapter-timeline-scroll': viewport, phrase_40: anchor,
    'chapter-phrase-list': {children: [anchor]}};
const button = {dataset: {preserveTimelinePosition: 'current-phrase'},
    closest: selector => selector === '[data-preserve-timeline-position]' ? button : anchor};
const document = {addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
    querySelectorAll: () => [anchor], getElementById: id => elements[id]};
vm.runInNewContext(source, {document, window: {addEventListener() {}}, WeakMap,
    ResizeObserver: class {constructor(fn) {resize = fn;} observe() {} disconnect() {disconnected = true;}},
    requestAnimationFrame: fn => {frames.set(++frameId, fn); return frameId;},
    cancelAnimationFrame: id => frames.delete(id), setTimeout() {}});
function fire(type, detail) {for (const fn of listeners[type] || []) {
    // Run insertion handlers; other handlers initialize unrelated timeline UI.
    if (fn.name === 'restoreTimelineInsertionPosition' || type === 'htmx:beforeRequest') fn({type, detail});
}}
const xhr = {};
const detail = {target: {id: 'chapter-phrase-list'}, xhr, requestConfig: {elt: button}};
fire('htmx:beforeRequest', detail);
viewport.scrollTop = 0; // Browser clamps when the old list is removed.
anchorTop = 1980;
fire('htmx:afterSwap', detail);
assert.equal(viewport.scrollTop, 1800);
// Account for editor height changes during settle, keeping the same anchor offset.
anchorTop = 220;
fire('htmx:afterSettle', detail);
assert.equal(viewport.scrollTop, 1840);
assert.equal(typeof resize, 'function', 'Observe custom-element layout after settling');
viewportEvents.wheel();
assert.equal(disconnected, true);
assert.equal(frames.size, 0, 'User scrolling cancels outstanding position restoration');
viewport.scrollTop = 2000;
fire('htmx:afterSettle', detail);
assert.equal(viewport.scrollTop, 2000, 'Finished requests cannot override later user scrolling');
console.log('Phrase insertion preserves its viewport anchor until the user scrolls');
