const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('audio regeneration waits for pending phrase text save', async () => {
    const listeners = {};
    let saved;
    const pending = new Promise(resolve => saved = resolve);
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-import.js','utf8'), {
        document: {addEventListener: (name, callback) => listeners[name] = callback},
        window: {chapterPhraseSaves: {7: pending}}, console,
    });
    let prevented = false, requested = false;
    listeners['htmx:confirm']({preventDefault: () => prevented = true,
        detail: {elt: {closest: () => ({dataset: {regeneratePhrase: '7'}})},
            issueRequest: skip => {assert.equal(skip, true); requested = true;}}});
    assert.equal(prevented, true);
    assert.equal(requested, false);
    saved();
    await pending;
    assert.equal(requested, true);
});
