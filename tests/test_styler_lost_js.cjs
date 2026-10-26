const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
function setup(reducedMotion = true) {
    const dom = new JSDOM(`<script data-styler-websocket="ws://example/styler-ws"></script>
        <wa-tab-panel name="tag_LOST"><span data-lost-summary data-page="0" data-page-size="5"></span>
        <div data-style-entry id="lost-entry"><div id="card_artist" class="card-overview"><wa-details><div id="style_details_artist"></div></wa-details></div></div>
        <div data-style-entry id="other-entry"><div id="card_other" class="card-overview"></div></div></wa-tab-panel>
        <wa-tab-panel name="tag_painting"><div data-style-entry id="painting-entry"><div id="card_artist" class="card-overview">
        <div id="tag_cloud_artist" class="tags-removable" data-style-tag="artist"></div>
        <div data-image-tags data-style-tag="artist" data-engine="qwen"></div></div></div></wa-tab-panel>`, {runScripts:'outside-only'});
    const {window} = dom;
    Object.defineProperty(window.document, 'currentScript', {value:window.document.querySelector('script')});
    window.matchMedia = () => ({matches:reducedMotion});
    window.requestAnimationFrame = callback => callback();
    const timers = [];
    window.setTimeout = (callback, delay) => {timers.push({callback, delay}); return timers.length;};
    window.clearTimeout = () => {};
    class Socket extends window.EventTarget {
        static OPEN = 1;
        constructor() {super(); this.readyState = 1; Socket.instance = this;}
        send() {}
        close() {}
    }
    window.WebSocket = Socket;
    const source = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
    new vm.Script(source, {filename: require('node:path').resolve('src/artifact_editor/static/js/styler-samples.js')})
        .runInContext(dom.getInternalVMContext());
    const categories = [];
    window.document.addEventListener('styler:category-added', event => categories.push(event.detail));
    const emit = event => Socket.instance.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify(event)}));
    return {window, timers, categories, emit, close:() => dom.window.close()};
}
for (const state of ['lost_resolved', 'style_tags_reconciled', 'prompt_tagged', 'styles_scan_image_complete']) {
    const h = setup();
    h.emit({state, job_id:'idle-job', style_tag:'artist', engine:'qwen', tags:['painting'], image_tags:['painting'],
        added:['painting'], removed:[], counts:[{tag:'painting', count:2}], lost:false, lost_count:1});
    assert.equal(h.window.document.getElementById('lost-entry'), null, `${state} removes an unopened LOST card without a tag cloud`);
    assert.ok(h.window.document.getElementById('painting-entry'), 'The same style remains in its actual category');
    assert.ok(h.window.document.getElementById('other-entry'), 'Other LOST cards remain');
    assert.match(h.window.document.querySelector('[data-lost-summary]').textContent, /of 1 LOST styles/);
    assert.ok(h.categories.some(update => update.tag === 'LOST' && update.count === 1));
    assert.equal(h.window.document.querySelector('#tag_cloud_artist wa-tag').textContent, 'painting');
    if (state === 'styles_scan_image_complete') {
        assert.equal(h.window.document.querySelector('[data-image-tags] wa-tag').textContent, 'painting', 'Idle normalization refreshes image tags without a page-owned scan');
    }
    h.close();
}
{
    const h = setup();
    h.emit({state:'prompt_tagged', style_tag:'artist', engine:'qwen', tags:[], image_tags:['red'], counts:[], lost:true, lost_count:2});
    assert.ok(h.window.document.getElementById('lost-entry'), 'Image evidence alone must not remove an unclassified style');
    h.close();
}
{
    const h = setup(false);
    const event = {state:'lost_resolved', style_tag:'artist', tags:['painting'], lost:false, lost_count:1, counts:[]};
    h.emit(event);
    const entry = h.window.document.getElementById('lost-entry');
    assert.ok(entry.classList.contains('lost-style-dissolving'), 'Normal motion uses the poof animation');
    h.emit({...event, state:'style_tags_reconciled'});
    assert.equal(h.timers.filter(timer => timer.delay === 750).length, 1, 'Repeated worker notifications do not restart the poof');
    h.timers.find(timer => timer.delay === 750).callback();
    assert.equal(h.window.document.getElementById('lost-entry'), null);
    h.close();
}
console.log('LOST maintenance updates, collapsed cards, category isolation and poof lifecycle tests passed');
