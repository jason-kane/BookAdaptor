const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
(async () => {
    const dom = new JSDOM('<body><script data-styler-websocket="ws://example/styler-ws"></script><div class="card-overview"><img data-style-sample="joe_quesada" data-engine="zit" data-sample-url="/styles/show_sample/joe_quesada_zit_sampler.png"><div id="tag_cloud_joe_quesada" class="tags-removable" data-style-tag="joe_quesada"><wa-tag>manual tag</wa-tag><label class="style-tag-picker-label">Add Tag<input class="style-tag-picker"></label></div></div></body>', {runScripts: 'outside-only'});
    const {window} = dom;
    const connections = [];
    class Socket extends window.EventTarget {
        static OPEN = 1;
        constructor(url) { super(); this.readyState = 1; this.messages = []; connections.push(this); }
        send(data) { this.messages.push(JSON.parse(data)); }
        close() {}
    }
    window.WebSocket = Socket;
    Object.defineProperty(window.document, 'currentScript', {value: window.document.querySelector('script')});
    const source = fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8');
    new vm.Script(source, {filename: require('node:path').resolve('src/artifact_editor/static/js/styler-samples.js')})
        .runInContext(dom.getInternalVMContext());
    const socket = connections[0];
    socket.dispatchEvent(new window.Event('open'));
    assert.equal(socket.messages.length, 1);
    window.document.body.append(window.document.createElement('div'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(socket.messages.length, 1, 'DOM changes must not duplicate requests');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({style_tag:'joe_quesada', engine:'zit', state:'queued', wrapper:'<span id="sample" data-style-sample="joe_quesada" data-engine="zit"><wa-spinner></wa-spinner></span>'})}));
    assert.equal(window.document.querySelector('wa-spinner'), null, 'Queued cards must remain idle');
    assert.ok(window.document.querySelector('img[data-style-sample]'));
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({style_tag:'joe_quesada', engine:'zit', state:'running', percent:45, message:'Sampling'})}));
    assert.equal(window.document.querySelector('wa-progress-bar').getAttribute('value'), '45');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(socket.messages.length, 1, 'Progress must not trigger more sample requests');
    assert.match(window.document.querySelector('[data-style-sample]').textContent, /Sampling \(45%\)/);
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({style_tag:'joe_quesada', engine:'zit', state:'complete', filename:'/styles/show_sample/joe_quesada_zit_sampler.png'})}));
    assert.equal(window.document.querySelector('img').getAttribute('src'), '/styles/show_sample/joe_quesada_zit_sampler.png');
    assert.equal(window.document.querySelectorAll('[data-style-sample]').length, 0);
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({style_tag:'joe_quesada',engine:'zit',state:'prompt_tagged',tags:['ink drawing'],added:['ink drawing'],removed:[],counts:[]})}));
    assert.equal(window.document.querySelectorAll('#tag_cloud_joe_quesada wa-tag').length, 2);
    assert.match(window.document.querySelector('#tag_cloud_joe_quesada').textContent,/ink drawing/);
    window.document.querySelector('.card-overview').insertAdjacentHTML('beforeend', '<div data-image-tags data-style-tag="joe_quesada" data-engine="zit" data-base-style-prompt="classic"></div><div data-image-tags data-style-tag="joe_quesada" data-engine="qwen" data-base-style-prompt="classic"></div>');
    const imageTags = ['ink drawing', 'crosshatching', 'bold lines', 'warm palette', 'flat color', 'paper texture', 'soft light', '<b>expressive</b>'];
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({style_tag:'joe_quesada', engine:'zit', state:'prompt_tagged', image_tags:imageTags, tags:['ink drawing'], added:[], removed:[], counts:[]})}));
    const column = window.document.querySelector('[data-image-tags][data-engine="zit"]');
    assert.equal(column.querySelectorAll('wa-tag').length, 8);
    assert.equal(column.querySelector('b'), null, 'Image tags must render as text');
    assert.equal(window.document.querySelector('[data-image-tags][data-engine="qwen"]').children.length, 0);
    window.document.querySelector('.card-overview').insertAdjacentHTML('beforeend',
        '<div data-image-tags data-style-tag="joe_quesada" data-engine="zit" data-base-style-prompt="kid_friendly"><wa-tag>child variant tag</wa-tag></div>');
    const childColumn = window.document.querySelector('[data-image-tags][data-base-style-prompt="kid_friendly"]');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'styles_scan_image_complete', style_tag: 'joe_quesada', engine: 'zit',
        base_style_prompt: 'kid_friendly', image_tags: [],
    })}));
    assert.equal(column.querySelectorAll('wa-tag').length, 8,
        'An empty kid-friendly normalization must not clear classic image tags');
    assert.equal(childColumn.children.length, 0);
    childColumn.textContent = 'keep child variant';
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'styles_scan_image_complete', style_tag: 'joe_quesada', engine: 'zit',
        image_tags: imageTags,
    })}));
    assert.equal(column.querySelectorAll('wa-tag').length, 8);
    assert.equal(childColumn.textContent, 'keep child variant',
        'Legacy normalization events default to classic, not every prompt variant');
    assert.equal(window.document.querySelectorAll('#tag_cloud_joe_quesada wa-tag').length, 2, 'Image evidence must not become style tags directly');
    window.document.body.insertAdjacentHTML('beforeend', '<img data-style-sample="joe_quesada" data-engine="zit">');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(socket.messages.length, 2, 'Replacement cards must request the sample again');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({style_tag:'joe_quesada', engine:'zit', state:'complete', filename:'/styles/show_sample/joe_quesada_zit_sampler.png'})}));
    assert.equal(window.document.querySelectorAll('img[src]').length, 2);
    window.document.querySelector('.card-overview').insertAdjacentHTML('beforeend', '<div><wa-button data-style-retag="joe_quesada" data-engine="zit">Retag</wa-button><span class="retag-status"></span></div>');
    window.document.querySelector('#tag_cloud_joe_quesada').insertAdjacentHTML('afterbegin', '<wa-tag>old tag</wa-tag>');
    const retag = window.document.querySelector('[data-style-retag]');
    retag.click();
    assert.deepEqual(socket.messages.at(-1), {action:'retag', style_tag:'joe_quesada', engine:'zit', base_style_prompt:'classic'});
    assert.equal(retag.disabled, true);
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({state:'retag_queued', style_tag:'joe_quesada', engines:['zit','qwen']})}));
    for (const engine of ['zit', 'qwen']) {
        socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({style_tag:'joe_quesada', engine, state:'prompt_tagged', image_tags:['ink drawing', engine], tags:['ink drawing'], added:['ink drawing'], removed:['old tag'], counts:[]})}));
    }
    assert.equal(retag.disabled, false, 'A prompt_tagged event completes the matching style and engine retag');
    assert.equal(window.document.querySelector('.retag-status').textContent, 'Retag complete');
    window.document.querySelector('.card-overview').insertAdjacentHTML('beforeend',
        '<div><wa-button data-style-retag="joe_quesada" data-engine="zit" data-base-style-prompt="kid_friendly">Retag child image</wa-button><span class="retag-status"></span></div>');
    const childRetag = window.document.querySelector('[data-style-retag][data-base-style-prompt="kid_friendly"]');
    childRetag.click();
    assert.deepEqual(socket.messages.at(-1), {
        action: 'retag', style_tag: 'joe_quesada', engine: 'zit', base_style_prompt: 'kid_friendly',
    });
    assert.equal(retag.disabled, false, 'Kid-friendly retag must not disable the classic button');
    retag.click();
    assert.equal(socket.messages.at(-1).base_style_prompt, 'classic',
        'A pending child retag must not suppress an independent classic request');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'retag_queued', style_tag: 'joe_quesada', engines: ['zit', 'zit'],
        samples: [{engine: 'zit', base_style_prompt: 'classic'}, {engine: 'zit', base_style_prompt: 'kid_friendly'}],
    })}));
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'prompt_tagged', style_tag: 'joe_quesada', engine: 'zit', base_style_prompt: 'classic',
    })}));
    assert.equal(retag.disabled, false);
    assert.equal(childRetag.disabled, true, 'Classic completion must not clear child retag tracking');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'prompt_tagged', style_tag: 'joe_quesada', engine: 'zit', base_style_prompt: 'kid_friendly',
    })}));
    assert.equal(childRetag.disabled, false);
    const autoDetails = window.document.createElement('wa-details');
    autoDetails.open = true;
    window.document.querySelector('.card-overview').id = 'card_joe_quesada';
    window.document.querySelector('.card-overview').append(autoDetails);
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'loaded', style_tag: 'joe_quesada', engine: 'zit', base_style_prompt: 'kid_friendly',
        filename: '/styles/show_sample/child.png', image_tags: [],
    })}));
    assert.equal(socket.messages.at(-1).base_style_prompt, 'kid_friendly',
        'Automatic retagging after loading a child image must preserve its variant');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({
        state: 'prompt_tagged', style_tag: 'joe_quesada', engine: 'zit', base_style_prompt: 'kid_friendly',
    })}));
    autoDetails.remove();
    await new Promise(resolve => setImmediate(resolve));
    const imageChips = window.document.querySelector('[data-image-tags][data-engine="qwen"]');
    assert.equal(imageChips.querySelector('wa-tag').getAttribute('variant'), 'success');
    assert.equal(imageChips.querySelectorAll('wa-tag')[1].hasAttribute('variant'), false);
    const cloud = window.document.querySelector('#tag_cloud_joe_quesada');
    const manualChip = window.document.createElement('wa-tag');
    manualChip.textContent = 'qwen';
    cloud.prepend(manualChip);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(imageChips.querySelectorAll('wa-tag')[1].getAttribute('variant'), 'success');
    manualChip.remove();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(imageChips.querySelectorAll('wa-tag')[1].hasAttribute('variant'), false);

    assert.equal(window.document.querySelector('[data-image-tags][data-engine="qwen"]').children.length, 2);
    assert.deepEqual([...window.document.querySelectorAll('#tag_cloud_joe_quesada wa-tag')].map(tag => tag.textContent).sort(), ['ink drawing', 'manual tag']);
    assert.ok(window.document.querySelector('#tag_cloud_joe_quesada .style-tag-picker'));
    assert.ok(window.document.querySelector('#tag_cloud_joe_quesada wa-tag[with-remove][pill]'));
    assert.equal(window.document.querySelector('[data-prompt-tags]'), null);
    window.document.body.insertAdjacentHTML('beforeend', '<wa-button data-stockpile-scan>Scan Stockpile</wa-button><span data-stockpile-status></span>');
    const scan = window.document.querySelector('[data-stockpile-scan]');
    scan.click();
    assert.deepEqual(socket.messages.at(-1), {action:'stockpile_scan'});
    assert.equal(scan.disabled, true);
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({state:'stockpile_queued',scanned:3,complete:1,jobs:['stock-1','stock-2'],errors:[]})}));
    assert.match(window.document.querySelector('[data-stockpile-status]').textContent, /3 images scanned, 1 already complete, 2 queued/);
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({state:'stockpile_complete',job_id:'stock-1'})}));
    assert.equal(scan.disabled, true);
    socket.dispatchEvent(new window.MessageEvent('message', {data:JSON.stringify({state:'stockpile_failed',job_id:'stock-2'})}));
    assert.equal(scan.disabled, false);
    assert.match(window.document.querySelector('[data-stockpile-status]').textContent, /2\/2 processed, 1 failed/);
    assert.equal(connections.length, 1);
    const details = window.document.createElement('wa-details');
    window.document.querySelector('.card-overview').append(details);
    for (let attempt = 0; attempt < 2; attempt++) {
        details.dispatchEvent(new window.Event('wa-show', {bubbles: true}));
        assert.deepEqual(socket.messages.at(-1), {action:'missing_tags', style_tag:'joe_quesada'});
    }
    window.matchMedia = () => ({matches: true});
    window.document.body.insertAdjacentHTML('beforeend', '<wa-tab-panel name="tag_LOST"><div data-style-entry><div class="card-overview"><div class="tags-removable" data-style-tag="finished"></div></div></div></wa-tab-panel>');
    socket.dispatchEvent(new window.MessageEvent('message', {data: JSON.stringify({state:'lost_resolved', style_tag:'finished', lost_count:2})}));
    assert.equal(window.document.querySelector('wa-tab-panel[name="tag_LOST"] [data-style-entry]'), null, 'Completed background tags dissolve the stale LOST entry');
    dom.window.close();
    console.log('Styler websocket image updates passed');
})();
