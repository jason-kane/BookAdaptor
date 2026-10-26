const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(`<body>${fs.readFileSync('src/styler/templates/sample-viewer.html', 'utf8')}</body>`, {runScripts:'outside-only'});
const {window} = dom;
const timers = [];
window.setTimeout = (callback, delay) => {timers.push({callback, delay}); return timers.length;};
window.clearTimeout = id => {if (timers[id - 1]) timers[id - 1].cancelled = true;};
window.eval(fs.readFileSync('src/artifact_editor/static/js/styler-image-viewer.js', 'utf8'));
const emit = state => window.document.dispatchEvent(new window.CustomEvent('styler:sample-status', {detail:{state, style_tag:'artist', style_name:'Artist', engine:'qwen', percent:45}}));
const progress = window.document.getElementById('styler-viewer-progress');
for (const state of ['complete', 'loaded']) {
    emit('running');
    assert.equal(progress.hidden, false);
    emit(state);
    assert.equal(window.document.getElementById('styler-viewer-progress-bar').getAttribute('value'), '100');
    assert.equal(progress.classList.contains('styler-progress-dissolving'), true);
    assert.equal(timers.at(-1).delay, 650);
    timers.at(-1).callback();
    assert.equal(progress.hidden, true, `${state} follows the same ready/dissolve path`);
}
dom.window.close();
console.log('Loaded and complete sample viewer paths passed');
