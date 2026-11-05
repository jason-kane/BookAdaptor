const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('stage completion unlocks buttons and clears hover messages without enabling busy actions', async () => {
    const listeners = {}, intervals = [];
    let now = 10000;
    let prerequisites = {chapter_assign_characters: 'Must add missing characters first'};
    const attributes = {title: prerequisites.chapter_assign_characters};
    const wrapper = {setAttribute(k,v) {attributes[k]=v;}, removeAttribute(k) {delete attributes[k];}};
    const button = {disabled: true, dataset: {workflowAction: 'chapter_assign_characters'},
        closest: () => wrapper, setAttribute: wrapper.setAttribute, removeAttribute: wrapper.removeAttribute};
    let requests = 0;
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-import.js', 'utf8'), {
        document: {currentScript: {dataset: {workflowStatusUrl: '/chapter/workflow-status'}},
            visibilityState: 'visible', addEventListener: (name, handler) => listeners[name] = handler,
            querySelectorAll: () => [button]},
        Date: {now: () => now}, console,
        fetch: async (url, options) => {
            assert.equal(url, '/chapter/workflow-status');
            assert.equal(options.cache, 'no-store');
            requests++;
            return {ok: true, json: async () => ({prerequisites})};
        },
        window: {setInterval: fn => intervals.push(fn)},
    });
    const flush = () => new Promise(resolve => setImmediate(resolve));
    listeners.DOMContentLoaded();
    await flush();
    assert.equal(button.disabled, true);
    prerequisites = {chapter_assign_characters: null};
    now += 2000;
    intervals[0]();
    await flush();
    assert.equal(button.disabled, false);
    assert.equal(attributes.title, undefined);
    button.dataset.workflowBusy = 'true';
    now += 2000;
    intervals[0]();
    await flush();
    assert.equal(button.disabled, true);
    button.dataset.workflowBusy = 'false';
    prerequisites = {chapter_assign_characters: 'Must import chapter first'};
    now += 2000;
    intervals[0]();
    await flush();
    assert.equal(button.disabled, true);
    assert.equal(attributes.title, 'Must import chapter first');
    assert.equal(requests, 4);
});
