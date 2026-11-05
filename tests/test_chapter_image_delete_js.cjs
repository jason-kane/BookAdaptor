const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/artifact_editor/static/js/chapter-image-delete.js', 'utf8');
const storage = new Map();
function setup(denyStorage = false) {
    const elements = {};
    for (const id of ['chapter-image-delete-dialog', 'chapter-image-delete-skip', 'chapter-image-delete-message', 'chapter-image-delete-confirm', 'chapter-image-delete-cancel']) {
        elements[id] = {events: {}, addEventListener(name, callback) { this.events[name] = callback; }};
    }
    const events = {};
    vm.runInNewContext(source, {document: {getElementById: id => elements[id], addEventListener: (name, cb) => { events[name] = cb; }},
        sessionStorage: {getItem(key) { if (denyStorage) throw Error(); return storage.get(key); },
            setItem(key, value) { if (denyStorage) throw Error(); storage.set(key, value); }}});
    let sent = 0;
    function ask(image = true) {
        let prevented = false;
        events['htmx:confirm']({preventDefault() { prevented = true; }, detail: {
            elt: {hasAttribute: () => image, getAttribute: () => '5'},
            issueRequest(skip) { assert.equal(skip, true); sent++; },
        }});
        return prevented;
    }
    return {elements, ask, count: () => sent};
}
let app = setup();
assert.equal(app.ask(false), false, 'Other timeline deletes keep their own confirmation');
assert.equal(app.ask(), true);
assert.equal(app.elements['chapter-image-delete-dialog'].open, true);
assert.match(app.elements['chapter-image-delete-message'].textContent, /Image 5/);
assert.equal(app.count(), 0);
app.elements['chapter-image-delete-skip'].checked = true;
app.elements['chapter-image-delete-cancel'].events.click();
assert.equal(app.count(), 0);
assert.equal(storage.size, 0, 'Cancel does not save the skip choice');
app.ask();
app.elements['chapter-image-delete-confirm'].events.click();
assert.equal(app.count(), 1);
app.ask();
app.elements['chapter-image-delete-dialog'].events['wa-hide']();
app.elements['chapter-image-delete-confirm'].events.click();
assert.equal(app.count(), 1, 'Closing the dialog cancels the request');
app.ask();
app.elements['chapter-image-delete-skip'].checked = true;
app.elements['chapter-image-delete-confirm'].events.click();
assert.equal(app.count(), 2);
app.ask();
assert.equal(app.count(), 3, 'Confirmed session preference skips subsequent dialogs');
app = setup();
app.ask();
assert.equal(app.count(), 1, 'Session preference survives page reloads');
storage.clear();
app = setup(); app.ask();
assert.equal(app.count(), 0, 'A fresh session requires confirmation');
app = setup(true); app.ask();
app.elements['chapter-image-delete-skip'].checked = true;
app.elements['chapter-image-delete-confirm'].events.click();
app.ask();
assert.equal(app.count(), 2, 'Unavailable storage still permits the in-page choice');
console.log('Image deletion confirmation and session preference passed');
