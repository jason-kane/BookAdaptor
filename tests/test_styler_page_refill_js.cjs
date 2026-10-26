const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
const entry = key => `<div data-style-entry data-style-key="${key}"><div id="card_${key}" class="card-overview"><wa-details><input value="preserved"></wa-details></div></div>`;
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(keys = ['a','b','c','d','e','f','g','h'], pageNumber = 0, motion = false) {
    let catalog = [...keys];
    const visible = keys.slice(pageNumber * 5, pageNumber * 5 + 5);
    const dom = new JSDOM(`<script data-styler-websocket="ws://example/styler-ws"></script><wa-tab-panel name="tag_LOST">
        <div id="tag_LOST" data-style-page data-page-category="LOST" data-page="${pageNumber}" data-page-size="5" data-page-total="${keys.length}" data-page-url="/styles/style_page">
        <span data-lost-summary data-style-page-summary data-page="${pageNumber}" data-page-size="5"></span>
        <wa-button data-style-page-nav="previous"></wa-button><wa-button data-style-page-nav="next"></wa-button>
        ${visible.map(entry).join('')}</div></wa-tab-panel>`, {runScripts:'outside-only', url:'http://example.test/styles/'});
    const {window} = dom;
    Object.defineProperty(window.document, 'currentScript', {value:window.document.querySelector('script')});
    window.matchMedia = () => ({matches:!motion});
    window.requestAnimationFrame = callback => callback();
    const timers = new Map();
    let timerId = 0;
    window.setTimeout = (callback, delay) => {timers.set(++timerId, {callback, delay}); return timerId;};
    window.clearTimeout = id => timers.delete(id);
    window.console.warn = () => {};
    const processed = [];
    window.htmx = {process:node => processed.push(node)};
    class Socket extends window.EventTarget {
        static OPEN = 1;
        constructor() {super(); this.readyState = 1; Socket.instance = this;}
        send() {}
        close() {}
    }
    window.WebSocket = Socket;
    const requests = [];
    const payload = url => {
        const present = new Set(url.searchParams.getAll('present'));
        const page = Math.min(Number(url.searchParams.get('page')), Math.max(0, Math.ceil(catalog.length / 5) - 1));
        const selected = catalog.slice(page * 5, page * 5 + 5);
        return {tag:'LOST', page, page_size:5, total:catalog.length, keys:selected,
            entries:Object.fromEntries(selected.filter(key => !present.has(key)).map(key => [key, entry(key)]))};
    };
    let fetchImpl = async url => ({ok:true, json:async()=>payload(url)});
    window.fetch = (url, options) => {requests.push(url); return fetchImpl(url, options);};
    new vm.Script(source, {filename: require('node:path').resolve('src/artifact_editor/static/js/styler-samples.js')})
        .runInContext(dom.getInternalVMContext());
    const root = window.document.querySelector('[data-style-page]');
    const remove = key => {
        catalog = catalog.filter(item => item !== key);
        Socket.instance.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({state:'lost_resolved', style_tag:key, tags:['painting'], lost:false, lost_count:catalog.length, counts:[]})}));
    };
    const run = async (delay = 80) => {
        await flush();
        for (const [id, timer] of [...timers]) if (timer.delay === delay) {timers.delete(id); timer.callback();}
        await flush();
    };
    return {window, root, remove, run, requests, processed, payload, timers,
        ids:() => [...root.querySelectorAll('[data-style-entry]')].map(row => row.dataset.styleKey),
        setFetch:fn => {fetchImpl = fn;}, drop:key => {catalog = catalog.filter(item => item !== key);},
        close:() => dom.window.close()};
}
(async () => {
    {
        const h = setup();
        const survivor = h.window.document.querySelector('[data-style-key="c"]');
        const details = survivor.querySelector('wa-details');
        details.open = true;
        const input = survivor.querySelector('input');
        input.value = 'User edit'; input.focus();
        h.remove('a'); h.remove('b');
        await h.run();
        assert.equal(h.requests.length, 1, 'Multiple removals share one refill');
        assert.deepEqual(h.ids(), ['c','d','e','f','g']);
        assert.equal(h.window.document.querySelector('[data-style-key="c"]'), survivor);
        assert.equal(details.open, true);
        assert.equal(input.value, 'User edit');
        assert.equal(h.window.document.activeElement, input, 'Surviving cards keep focus and state');
        assert.equal(h.processed.length, 2, 'Only the two new cards are initialized');
        assert.equal(h.root.querySelector('[data-lost-summary]').textContent, 'Showing 1–5 of 6 LOST styles');
        assert.equal(h.root.querySelector('[data-style-page-nav="next"]').disabled, false);
        h.close();
    }
    {
        const h = setup(undefined, 0, true);
        h.remove('a');
        await h.run();
        assert.equal(h.requests.length, 0, 'Refill waits for the poof to finish');
        await h.run(750);
        await h.run();
        assert.deepEqual(h.ids(), ['b','c','d','e','f']);
        h.close();
    }
    {
        const h = setup();
        let resolveFirst;
        h.setFetch(url => {
            const data = h.payload(url);
            return new Promise(resolve => {resolveFirst = () => resolve({ok:true, json:async()=>data});});
        });
        h.remove('a'); await h.run();
        h.remove('b');
        resolveFirst(); await flush();
        assert.ok(!h.ids().includes('b'), 'An old response cannot resurrect a newly removed card');
        h.setFetch(async url => ({ok:true, json:async()=>h.payload(url)}));
        await h.run();
        assert.deepEqual(h.ids(), ['c','d','e','f','g']);
        assert.equal(h.requests.length, 2);
        h.close();
    }
    {
        const h = setup(['a','b','c','d','e','f'], 1);
        h.remove('f'); await h.run();
        assert.equal(h.root.dataset.page, '0', 'An emptied last page falls back to the new last page');
        assert.deepEqual(h.ids(), ['a','b','c','d','e']);
        assert.equal(h.root.querySelector('[data-style-page-nav="previous"]').disabled, true);
        assert.equal(h.root.querySelector('[data-style-page-nav="next"]').disabled, true);
        h.close();
    }
    {
        const h = setup(['a']);
        h.remove('a'); await h.run();
        assert.deepEqual(h.ids(), []);
        assert.equal(h.root.querySelector('[data-lost-summary]').textContent, 'Showing 0–0 of 0 LOST styles');
        h.close();
    }
    {
        const h = setup();
        h.drop('a');
        h.root.querySelector('[data-style-key="a"]').remove();
        await h.run();
        assert.deepEqual(h.ids(), ['b','c','d','e','f'], 'External entry removal also refills a paginated list');
        h.close();
    }
    {
        const h = setup();
        let respond;
        h.setFetch(url => new Promise(resolve => {respond = () => resolve({ok:true, json:async()=>h.payload(url)});}));
        h.remove('a'); await h.run();
        h.root.remove(); respond(); await flush();
        assert.equal(h.processed.length, 0, 'Navigating away discards the old page response');
        h.close();
    }
    {
        const h = setup();
        h.setFetch(async () => {throw new Error('Temporary failure');});
        h.remove('a'); await h.run();
        assert.equal(h.requests.length, 1);
        h.setFetch(async url => ({ok:true, json:async()=>h.payload(url)}));
        await h.run(1000);
        assert.deepEqual(h.ids(), ['b','c','d','e','f'], 'Transient errors retry without leaving the page short');
        h.close();
    }
    console.log('Page refill batching, animation, state preservation, races, navigation and retry checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
