const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const listeners = {};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-import.js', 'utf8'), {
    document: {addEventListener: (name, callback) => listeners[name] = callback}, window: {},
});
const command = {action: 'chapter_assign_characters', job_id: 'current', author: 'Writer',
    title: 'Book', chapter_number: 4, language: 'english'};
const event = {detail: {elt: {closest: () => ({}), getAttribute: () => JSON.stringify(command)},
    parameters: {action: 'other', job_id: 'wrong'}}};
listeners['htmx:wsConfigSend'](event);
assert.deepEqual(JSON.parse(event.detail.messageBody), command);
assert.equal(event.detail.parameters.job_id, 'current');
console.log('Chapter command keeps the button identity and overrides unrelated form values');

const feedback = {}, timers = [];
const status = {id: 'video-progress-import', dataset: {}, textContent: ''};
const container = {dataset: {chapterImport: 'import'}};
const button = {id: 'import_btn', closest: () => container};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-import.js', 'utf8'), {
    document: {addEventListener: (name, callback) => feedback[name] = callback,
        getElementById: () => status},
    window: {setTimeout: callback => timers.push(callback)},
});
feedback.click({target: {closest: () => button}});
assert.match(status.textContent, /Connecting/);
timers.shift()();
assert.match(status.textContent, /unavailable/);
feedback['htmx:wsAfterSend']({detail: {elt: button}});
assert.equal(button.disabled, true);
assert.match(status.textContent, /Submitting/);
status.dataset.videoState = 'queued';
status.textContent = 'Queued for chapter import';
timers.shift()();
assert.equal(status.textContent, 'Queued for chapter import');
console.log('Import immediately reports connection state and preserves server acknowledgment');
