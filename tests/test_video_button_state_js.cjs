const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('old terminal video messages cannot override prerequisite-owned disabled state', () => {
    const handlers = {};
    const button = {disabled: true, hasAttribute: name => name === 'data-workflow-action'};
    const notice = {querySelector: () => button};
    const status = {dataset: {videoJob:'old-video',videoState:'failed'}, closest: () => notice};
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/textoviva-player.js','utf8'), {
        window: {}, document: {addEventListener: (name, fn) => handlers[name] = fn,
            querySelectorAll: () => [status]},
    });
    for (let i=0;i<5;i++) handlers['htmx:wsAfterMessage']();
    assert.equal(button.disabled, true);
    status.dataset.videoState = 'complete';
    handlers['htmx:wsAfterMessage']();
    assert.equal(button.disabled, true);
});
