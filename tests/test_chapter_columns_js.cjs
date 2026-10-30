const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const handlers = {};
    const button = { disabled: false };
    const status = { dataset: {}, textContent: '' };
    const panel = {};
    const refreshes = [];
    const group = {
        dataset: { chapterAssets: 'appearance-job' },
        querySelector(selector) {
            if (selector === 'wa-button') return button;
            if (selector === '[data-video-state]') return status.dataset.videoState ? status : null;
            return null;
        },
    };
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-columns.js', 'utf8'), {
        document: {
            addEventListener(name, handler) { handlers[name] = handler; },
            getElementById(id) { return id === 'video-progress-appearance-job' ? status : null; },
            querySelector() { return panel; },
            querySelectorAll() { return [group]; },
        },
        window: {},
        htmx: { trigger(target, name) { refreshes.push({ target, name }); } },
    });
    return { handlers, button, status, panel, refreshes, group };
}

test('bulk submission clears old terminal state and disables its button', () => {
    const s = setup();
    s.status.dataset = { videoState: 'complete', appearanceRefreshed: 'true' };
    s.handlers['htmx:wsAfterSend']({ detail: { elt: { closest: () => s.group } } });
    assert.equal(s.button.disabled, true);
    assert.equal(s.status.dataset.videoState, undefined);
    assert.equal(s.status.dataset.appearanceRefreshed, undefined);
    assert.match(s.status.textContent, /Queued/);
});

test('bulk completion refreshes Character Changes once and enables its button', () => {
    const s = setup();
    s.button.disabled = true;
    s.status.dataset.videoState = 'running';
    s.handlers['htmx:wsAfterMessage']();
    assert.equal(s.refreshes.length, 0);
    s.status.dataset.videoState = 'complete';
    s.handlers['htmx:wsAfterMessage']();
    s.handlers['htmx:wsAfterMessage']();
    assert.equal(s.button.disabled, false);
    assert.deepEqual(s.refreshes, [{ target: s.panel, name: 'load-column' }]);
});

test('bulk failure also refreshes successfully saved character changes', () => {
    const s = setup();
    s.button.disabled = true;
    s.status.dataset.videoState = 'failed';
    s.handlers['htmx:wsAfterMessage']();
    assert.equal(s.button.disabled, false);
    assert.equal(s.refreshes.length, 1);
});
