const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
(async () => {
    const dom = new JSDOM('<body><wa-tab data-style-category="artists">artists (10)</wa-tab><wa-tab data-style-category="painting">painting (4)</wa-tab></body>', {runScripts:'outside-only'});
    const {window} = dom;
    const calls=[];
    window.htmx={ajax:async(method,url,options)=>{
        calls.push([method,url,options.values]);
        options.handler(null,{xhr:{status:200,responseText:JSON.stringify({tag:options.values.tag,count:9})}});
    }};
    window.eval(fs.readFileSync('src/artifact_editor/static/js/styler-tags.js','utf8'));
    function add(style,tag) {
        const cloud=window.document.createElement('div');
        cloud.className='tags-removable';cloud.dataset.styleTag=style;
        const element=window.document.createElement('wa-tag');element.textContent=tag;
        cloud.append(element);window.document.body.append(cloud);return element;
    }
    for(const [style,tag] of [['first','artists'],['second','painting'],['third','new panel']]) {
        const element=add(style,tag);
        element.dispatchEvent(new window.Event('wa-remove',{bubbles:true}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(element.isConnected,false);
        assert.equal(calls.at(-1)[2].style_tag,style);
        assert.equal(calls.at(-1)[2].tag,tag);
    }
    assert.equal(calls.length,3,'One removal request per tag across styles');
    assert.equal(window.document.querySelector('[data-style-category="artists"]').textContent,'artists (9)');
    assert.equal(window.document.querySelector('[data-style-category="painting"]').textContent,'painting (9)');
    const panel = window.document.createElement('wa-tab-panel');
    panel.setAttribute('name','tag_artists');
    window.document.body.append(panel);
    async function removeInCategory(tagName, shouldRemoveEntry) {
        const tag = add('category-style',tagName);
        const entry = window.document.createElement('div');
        entry.dataset.styleEntry = '';
        entry.append(tag.parentElement);
        panel.append(entry);
        tag.dispatchEvent(new window.Event('wa-remove',{bubbles:true}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(entry.isConnected,!shouldRemoveEntry);
    }
    await removeInCategory('artists',true);
    await removeInCategory('painting',false);
    const pickerCloud=window.document.createElement('div');
    pickerCloud.className='tags-removable';pickerCloud.dataset.styleTag='pick-style';
    pickerCloud.innerHTML='<label>Add Tag<input class="style-tag-picker"></label>';
    window.document.body.append(pickerCloud);
    const picker=pickerCloud.querySelector('input');
    picker.dispatchEvent(new window.FocusEvent('focusin',{bubbles:true}));
    assert.ok(window.document.querySelector('#styler-tag-options option[value="artists"]'));
    picker.value='brand new tag';
    picker.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.at(-1)[0],'POST');
    assert.equal(calls.at(-1)[2].style_tag,'pick-style');
    assert.equal(pickerCloud.querySelector('wa-tag').textContent,'brand new tag');
    assert.equal(picker.value,'');
    picker.value='artists';
    picker.dispatchEvent(new window.Event('change',{bubbles:true}));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(pickerCloud.querySelectorAll('wa-tag').length,2);
    const card = window.document.createElement('div');
    card.className = 'card-overview';
    card.innerHTML = '<wa-details open><div slot="summary"><div data-image-tags data-style-tag="toggle-style"><wa-tag role="button" tabindex="0">ink drawing</wa-tag></div></div><div id="tag_cloud_toggle-style" class="tags-removable" data-style-tag="toggle-style"><wa-tag>existing style tag</wa-tag><label class="style-tag-picker-label">Add Tag<input class="style-tag-picker"></label></div></wa-details>';
    window.document.body.append(card);
    const imageTag = card.querySelector('[data-image-tags] wa-tag');
    const toggleCloud = card.querySelector('.tags-removable');
    let summaryClicks = 0;
    card.querySelector('[slot="summary"]').addEventListener('click', () => summaryClicks++);
    imageTag.click();
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['POST','/styles/add_tag',{style_tag:'toggle-style',tag:'ink drawing'}]);
    assert.equal(toggleCloud.querySelectorAll('wa-tag').length, 2);
    assert.equal(imageTag.textContent, 'ink drawing');
    assert.equal(summaryClicks, 0, 'Tag activation must not collapse the style');
    imageTag.dispatchEvent(new window.KeyboardEvent('keydown', {key:' ',bubbles:true}));
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['DELETE','/styles/tag',{style_tag:'toggle-style',tag:'ink drawing'}]);
    assert.equal(toggleCloud.querySelectorAll('wa-tag').length, 1);
    assert.ok(imageTag.isConnected, 'Removing style membership keeps the per-image evidence');
    window.htmx.ajax=async(method,url,options)=>options.handler(null,{xhr:{status:500}});
    imageTag.click();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(toggleCloud.querySelectorAll('wa-tag').length, 1, 'Failed additions preserve style membership');
    assert.equal(imageTag.hasAttribute('aria-busy'), false);

    const failed=add('fourth','keep on failure');
    failed.dispatchEvent(new window.Event('wa-remove',{bubbles:true}));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(failed.isConnected,true);
    dom.window.close();
    console.log('Styler tag removal across dynamic panels passed');
})();
