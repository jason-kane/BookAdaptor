const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/artifact_editor/static/js/chapter-timeline.js', 'utf8');
const listeners = {};
const viewport = {scrollTop: 1800, getBoundingClientRect: () => ({top: 100})};
let anchorTop = 180;
const anchor = {id: 'phrase_40', getBoundingClientRect: () => ({top: anchorTop})};
const elements = {'chapter-timeline-scroll': viewport, phrase_40: anchor};
const button = {closest: selector => selector === '[data-preserve-timeline-position]' ? button : anchor};
const document = {addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
    getElementById: id => elements[id]};
vm.runInNewContext(source, {document, window: {addEventListener() {}}, WeakMap});
function fire(type, detail) {for (const fn of listeners[type] || []) {
    // Run the insertion handlers; other module handlers initialize unrelated UI.
    if (fn.name === 'restoreTimelineInsertionPosition' || type === 'htmx:beforeSwap') fn({type, detail});
}}
const xhr = {};
const detail = {target: {id: 'chapter-phrase-list'}, xhr, requestConfig: {elt: button}};
fire('htmx:beforeSwap', detail);
viewport.scrollTop = 0; // Browser clamps when the old list is removed.
anchorTop = 1980;
fire('htmx:afterSwap', detail);
assert.equal(viewport.scrollTop, 1800);
// Account for editor height changes during settle, keeping the same anchor offset.
anchorTop = 220;
fire('htmx:afterSettle', detail);
assert.equal(viewport.scrollTop, 1840);
viewport.scrollTop = 2000;
fire('htmx:afterSettle', detail);
assert.equal(viewport.scrollTop, 2000, 'completed requests cannot override later user scrolling');
console.log('Phrase insertion preserves its viewport anchor through swap and settle');
