const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('WebSocket prerequisites unlock buttons and clear reasons without enabling busy actions', async () => {
    const listeners = {}, socketListeners = {};
    const attributes = {title: 'Must add missing characters first'};
    const wrapper = {setAttribute(k,v) {attributes[k]=v;}, removeAttribute(k) {delete attributes[k];}};
    const button = {disabled: true, dataset: {workflowAction: 'chapter_assign_characters'},
        closest: () => wrapper, setAttribute: wrapper.setAttribute, removeAttribute: wrapper.removeAttribute};
    let requests = 0, socketUrl;
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-import.js', 'utf8'), {
        document: {currentScript: {dataset: {workflowWebsocket: 'ws://example/workflow'}},
            getElementById: () => null, querySelector: () => null,
            addEventListener: (name, handler) => listeners[name] = handler,
            querySelectorAll: () => [button]},
        console, WebSocket: class {
            constructor(url) {socketUrl=url;}
            addEventListener(name, handler) {socketListeners[name]=handler;}
        },
        fetch: async (url, options) => {
            assert.equal(url, '/chapter/workflow-status');
            assert.equal(options.cache, 'no-store');
            requests++;
            return {ok: true, json: async () => ({prerequisites:{chapter_assign_characters:null}})};
        },
        window: {addEventListener() {}},
    });
    assert.equal(socketUrl, 'ws://example/workflow');
    const update = reason => socketListeners.message({data:JSON.stringify({
        type:'workflow_prerequisites', prerequisites:{chapter_assign_characters:reason},
    })});
    update('Must add missing characters first');
    assert.equal(button.disabled, true);
    update(null);
    assert.equal(button.disabled, false);
    assert.equal(attributes.title, undefined);
    button.dataset.workflowBusy = 'true';
    update(null);
    assert.equal(button.disabled, true);
    button.dataset.workflowBusy = 'false';
    update('Must import chapter first');
    assert.equal(button.disabled, true);
    assert.equal(attributes.title, 'Must import chapter first');
    assert.equal(requests, 0, 'WebSocket prerequisite updates need no HTTP polling');
    await listeners['htmx:afterRequest']({detail:{successful:true,elt:{
        id:'chapter-character-attributions-verified',dataset:{prerequisitesUrl:'/chapter/workflow-status'},
    }}});
    assert.equal(requests, 1, 'A manual attribution change refreshes prerequisites immediately');
    assert.equal(button.disabled, false);
    assert.equal(attributes.title, undefined);
});
