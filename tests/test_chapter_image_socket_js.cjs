const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
function loadFunction(name, context) {
    const start = source.indexOf(`    function ${name}(`);
    const end = source.indexOf('\n    }\n', start) + '\n    }'.length;
    assert.ok(start >= 0 && end > start);
    return vm.runInNewContext(`(${source.slice(start, end)})`, context);
}

test('shared socket forwards chapter snapshots and completions to the image renderer', () => {
    const received = [];
    const handler = loadFunction('handleSocketMessage', {
        document: {dispatchEvent(event) { received.push(event); }},
        CustomEvent: class { constructor(type, options) {this.type = type; this.detail = options.detail;} },
    });
    for (const state of ['chapter_image_snapshot', 'chapter_image_complete']) {
        handler(JSON.stringify({state, author: 'Author', title: 'Book', chapter_number: 3}));
    }
    assert.equal(received.length, 2);
    assert.ok(received.every(event => event.type === 'chapter:image-event'));
    assert.equal(received[0].detail.state, 'chapter_image_snapshot');
    assert.equal(received[1].detail.state, 'chapter_image_complete');
    assert.equal(received[1].detail.chapter_number, 3);
});

test('shared socket subscribes to the current chapter on every reconnect', () => {
    const sent = [];
    const identity = {author: 'Author', title: 'Book', chapter_number: 3, language: 'english'};
    const open = loadFunction('handleSocketOpen', {
        socket: {send(raw) {sent.push(JSON.parse(raw));}},
        targets: new Map(), pendingGPUProvider: null,
        updateWorkerResetControl() {}, reportQueueCapacity() {}, requestMissing() {},
        document: {
            querySelector() {return {dataset: {chapterIdentity: JSON.stringify(identity)}};},
            querySelectorAll() {return [];},
        },
    });
    open();
    open();
    assert.equal(sent.length, 2);
    assert.deepEqual(sent[0], {...identity, action: 'watch_chapter_images'});
    assert.deepEqual(sent[1], sent[0]);
});

test('style pages without a chapter do not subscribe to image snapshots', () => {
    const sent = [];
    const open = loadFunction('handleSocketOpen', {
        socket: {send(raw) {sent.push(raw);}}, targets: new Map(), pendingGPUProvider: null,
        updateWorkerResetControl() {}, reportQueueCapacity() {}, requestMissing() {},
        document: {querySelector() {return null;}, querySelectorAll() {return []; }},
    });
    open();
    assert.equal(sent.length, 0);
});
