const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const settle = () => new Promise(resolve => setImmediate(resolve));

async function setup(hash, markup) {
    const dom = new JSDOM(markup, {url:`http://example.test/styles/${hash}`,runScripts:'outside-only'});
    const {window} = dom;
    for (const name of ['wa-tab-group','wa-tab','wa-tab-panel']) {
        window.customElements.define(name,class extends window.HTMLElement {});
    }
    const requests = [], processed = new WeakSet();
    window.htmx = {
        process: group => processed.add(group),
        trigger(tab,event) {
            assert.ok(processed.has(tab.closest('wa-tab-group')), 'Install HTMX before requesting category content');
            requests.push([tab.dataset.styleCategory,event]);
        },
    };
    window.fetch = async url => {
        assert.equal(url, '/styles/stockpile/tags');
        return {ok:true,json:async()=>({categories:[]})};
    };
    window.eval(fs.readFileSync('src/artifact_editor/static/js/styler-categories.js','utf8'));
    await settle();
    return {dom,window,requests};
}

const groupMarkup = `<wa-tab-group data-category-tab-group>
    <wa-tab panel="tag_custom" data-style-category="custom" data-style-count="2"></wa-tab>
    <wa-tab panel="tag_art" data-style-category="art &amp; ink" data-style-count="1"></wa-tab>
</wa-tab-group>`;

test('initial URL selection and tab changes load each category once', async t => {
    const h = await setup('#art%20%26%20ink', groupMarkup);
    t.after(() => h.dom.window.close());
    const group=h.window.document.querySelector('wa-tab-group');
    assert.equal(group.active,'tag_art');
    assert.deepEqual(h.requests,[['art & ink','styler-category-load']]);
    group.active='tag_custom';
    group.dispatchEvent(new h.window.Event('wa-tab-show'));
    assert.equal(h.window.location.pathname+h.window.location.hash,'/styles/#custom');
    assert.equal(h.requests.length,2);
    group.dispatchEvent(new h.window.Event('wa-tab-show'));
    assert.equal(h.requests.length,2,'Duplicate selection must not reload a category');
});

test('new category groups use hierarchical URL state after topic navigation', async t => {
    const h = await setup('#Art,Ink,art%20%26%20ink','');
    t.after(() => h.dom.window.close());
    h.window.document.body.insertAdjacentHTML('beforeend',groupMarkup);
    await settle();
    const group=h.window.document.querySelector('wa-tab-group');
    assert.equal(group.active,'tag_art');
    assert.deepEqual(h.requests,[['art & ink','styler-category-load']]);
    assert.ok(group.querySelector('[data-style-category="art & ink"]').hasAttribute('data-target-selected'));
});

test('malformed URL fragments do not load an arbitrary category', async t => {
    const h = await setup('#%bad',groupMarkup);
    t.after(() => h.dom.window.close());
    assert.equal(h.requests.length,0);
});

test('category events preserve stockpile counts and remove empty style tabs', async t => {
    const h = await setup('#custom',groupMarkup);
    t.after(() => h.dom.window.close());
    const {window}=h;
    const group=window.document.querySelector('wa-tab-group');
    const custom=group.querySelector('[data-style-category="custom"]');
    const emit=(name,detail)=>window.document.dispatchEvent(new window.CustomEvent(name,{detail}));
    emit('styler:category-added',{tag:'custom',count:2,stockpile_count:3});
    assert.equal(custom.textContent,'custom (5)');
    emit('styler:category-empty',{tag:'custom'});
    assert.ok(custom.isConnected,'Stockpile-only tabs remain available');
    assert.equal(custom.textContent,'custom (3)');
    emit('styler:category-added',{tag:'custom',count:2});
    assert.equal(custom.textContent,'custom (5)');
    window.fetch=async()=>({ok:true,json:async()=>({categories:[{tag:'custom',stockpile_count:4}]})});
    window.document.body.dispatchEvent(new window.CustomEvent('stockpile-refresh'));
    await settle();
    assert.equal(custom.textContent,'custom (6)');
    window.fetch=async()=>({ok:true,json:async()=>({categories:[]})});
    window.document.body.dispatchEvent(new window.CustomEvent('stockpile-refresh'));
    await settle();
    assert.equal(custom.textContent,'custom (2)');
    emit('styler:category-empty',{tag:'custom'});
    assert.equal(custom.isConnected,false);
    assert.equal(group.querySelectorAll('wa-tab').length,1);
});
