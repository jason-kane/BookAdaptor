const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
(async () => {
    const browser = await chromium.launch({executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const styles = fs.readFileSync('src/styler/templates/styles.html', 'utf8').split('<style>')[1].split('</style>')[0];
    const queue = fs.readFileSync('src/styler/templates/job-queue.html', 'utf8');
    await page.setContent(`<style>html {font-size:16px;} body {background:#222; color:#eee;} ${styles}</style>${queue}<div id="card_running" class="card-overview"><div data-sample-panel><img data-style-sample="running" data-engine="qwen"></div></div><div id="card_priority" class="card-overview"><div data-sample-panel><img data-style-sample="priority" data-engine="qwen"></div></div><div id="card_done-4" class="card-overview"><div data-sample-panel><img data-style-sample="done-4" data-engine="qwen"></div></div>`);
    await page.evaluate(icon => {
        customElements.define('wa-slider', class extends HTMLElement {
            static get observedAttributes() {return ['disabled'];}
            connectedCallback() {
                if (!this.shadowRoot) this.attachShadow({mode:'open'}).innerHTML = '<style>:host {display:block;} input {display:block;width:100%;height:1rem;margin:0;accent-color:#f89b7f;}</style><input type="range" min="0" max="2" step="1" value="1" aria-label="Background task rate">';
                this.attributeChangedCallback();
            }
            attributeChangedCallback() {if (this.shadowRoot) this.shadowRoot.querySelector('input').disabled = this.hasAttribute('disabled');}
            get value() {return Number(this.shadowRoot?.querySelector('input').value ?? this.getAttribute('value') ?? 1);}
            set value(value) {if (this.shadowRoot) this.shadowRoot.querySelector('input').value = value;}
        });
        customElements.define('wa-button', class extends HTMLElement {
            connectedCallback() {
                if (!this.shadowRoot) this.attachShadow({mode:'open'}).innerHTML = '<style>:host {display:inline-flex;} button {color:inherit;background:transparent;cursor:pointer;}</style><button type="button" part="base"><slot></slot></button>';
            }
        });
        customElements.define('wa-icon', class extends HTMLElement {
            connectedCallback() {
                if (!this.shadowRoot && this.getAttribute('src')?.includes('location-crosshairs')) this.attachShadow({mode:'open'}).innerHTML = '<style>:host {display:inline-flex;width:1em;height:1em;} svg {width:100%;height:100%;fill:currentColor;}</style>' + icon;
            }
        });
        class Socket extends EventTarget {
            static OPEN = 1;
            constructor() {super(); this.readyState = 1; this.messages = []; window.queueSocket = this;}
            send(data) {this.messages.push(JSON.parse(data));}
            close() {}
        }
        window.WebSocket = Socket;
        window.emitQueue = event => window.queueSocket.dispatchEvent(new MessageEvent('message', {data:JSON.stringify(event)}));
        // Emulate the component's shadow track, including delayed custom-element upgrade.
        window.upgradeProgress = () => customElements.define('wa-progress-bar', class extends HTMLElement {
            connectedCallback() {
                if (!this.shadowRoot) this.attachShadow({mode:'open'}).innerHTML = '<style>:host {display:block;} div {height:var(--track-height, 1rem); background:#5b9ef5;}</style><div></div>';
            }
        });
    }, fs.readFileSync('src/artifact_editor/static/fontawesome7/svgs/solid/location-crosshairs.svg', 'utf8'));
    await page.addScriptTag({content: fs.readFileSync('src/artifact_editor/static/js/styler-samples.js', 'utf8'), 'data-styler-websocket': ''});
    const plan = (count, history = 5) => page.evaluate(({count, history}) => {
        const job = id => ({job_id:id, label:id, action:'styler_sample', style_tag:id, engine:'qwen'});
        window.emitQueue({state:'queue_plan', running:{...job('running'), percent:30}, jobs:Array.from({length:count}, (_,i) => job(`pending-${i}`)),
            queue_count:count + 1, overflow_count:0, maintenance_mode:'walk',
            completed:Array.from({length:history}, (_,i) => ({...job(`done-${i}`), promoted:i === 4, completed_at:Date.now()/1000 - 5 + i / 100}))});
    }, {count, history});
    const inspect = () => page.evaluate(() => {
        const rect = element => {const r = element.getBoundingClientRect(); return {top:r.top, bottom:r.bottom, height:r.height, left:r.left, right:r.right};};
        const body = document.querySelector('.styler-queue-body-inner');
        const rows = [...document.querySelectorAll('.styler-queue-task[data-job-id]')];
        return {body:rect(body), rows: rows.map(row => ({id:row.dataset.jobId, state:row.dataset.state, next:row.dataset.next, rect:rect(row),
            bar:row.querySelector('wa-progress-bar') ? rect(row.querySelector('wa-progress-bar')) : null,
            track:row.querySelector('wa-progress-bar')?.shadowRoot ? rect(row.querySelector('wa-progress-bar').shadowRoot.querySelector('div')) : null})),
            summary:document.querySelector('#styler-queue-overflow').hidden ? null : document.querySelector('#styler-queue-overflow').textContent,
            scrollbars:[...document.querySelectorAll('#styler-job-queue *')].filter(el => getComputedStyle(el).overflowY === 'auto' || getComputedStyle(el).overflowY === 'scroll').length};
    });
    await plan(25);
    await page.waitForTimeout(800);
    const rate = page.locator('#styler-maintenance-rate input');
    await rate.focus();
    await page.keyboard.press('ArrowRight');
    let rateMessage = await page.evaluate(() => window.queueSocket.messages.filter(message => message.action === 'maintenance_mode').at(-1));
    assert.equal(rateMessage.mode, 'run');
    await page.evaluate(() => window.emitQueue({state:'maintenance_mode', mode:'run'}));
    assert.equal(await page.locator('.styler-queue-rate').getAttribute('data-mode'), 'run');
    await page.keyboard.press('Home');
    rateMessage = await page.evaluate(() => window.queueSocket.messages.filter(message => message.action === 'maintenance_mode').at(-1));
    assert.equal(rateMessage.mode, 'off');
    await page.evaluate(() => window.emitQueue({state:'maintenance_mode', mode:'off'}));
    await page.keyboard.press('ArrowRight');
    await page.evaluate(() => window.emitQueue({state:'maintenance_mode', mode:'walk'}));
    assert.equal(await page.locator('.styler-queue-rate').getAttribute('data-mode'), 'walk');
    const before = await inspect();
    await page.evaluate(() => window.upgradeProgress());
    await page.waitForTimeout(50);
    const after = await inspect();
    assert.deepEqual(after.rows.map(row => row.rect), before.rows.map(row => row.rect), 'Progress upgrade cannot move boxes');
    for (const row of after.rows.filter(row => row.bar)) {
        assert.ok(row.track.bottom <= row.rect.bottom, 'The full track fits inside its row');
        assert.ok(row.track.bottom <= after.body.bottom, 'The bottom bar is not clipped by the panel');
    }
    for (const viewport of [{width:1280,height:900}, {width:1280,height:600}, {width:390,height:500}]) {
        await page.setViewportSize(viewport);
        await page.waitForTimeout(100);
        const view = await inspect();
        assert.equal(view.rows.filter(row => row.state === 'running').length, 1);
        assert.equal(view.rows.filter(row => row.next === 'true').length, 1);
        assert.ok(view.rows.filter(row => row.state === 'complete').length >= 1);
        assert.ok(view.rows.every(row => row.rect.bottom <= view.body.bottom + 0.5), 'No silently clipped rows');
        assert.equal(view.scrollbars, 0);
        const shownPending = view.rows.filter(row => row.state === 'queued').length;
        assert.equal(view.summary.trim(), `${25 - shownPending} more tasks queued`);
    }
    await page.setViewportSize({width:1280,height:900});
    await plan(4);
    await page.waitForTimeout(400);
    let view = await inspect();
    assert.equal(view.rows.filter(row => row.state === 'complete').length, 5, 'Spare room displays all five completions');
    assert.equal(view.summary, null);
    await page.evaluate(() => {
        window.emitQueue({state:'queue_plan', running:{job_id:'running',label:'running',percent:80,promoted:true,action:'styler_sample',style_tag:'running',engine:'qwen'}, jobs:[{job_id:'priority', label:'priority',promoted:true,action:'styler_sample',style_tag:'priority',engine:'qwen'}, {job_id:'pending-0',label:'pending-0'}], queue_count:3,overflow_count:0});
    });
    await page.waitForTimeout(400);
    view = await inspect();
    assert.equal(view.rows.find(row => row.next === 'true').id, 'priority');
    const findButtons = page.locator('.styler-queue-task [data-queue-find]');
    assert.equal(await findButtons.count(), 3, 'Only tasks with supported destinations show find buttons');
    const beforeFind = await page.evaluate(() => window.queueSocket.messages.filter(message => message.action === 'queue_promote').length);
    await page.locator('[data-job-id="priority"] [data-queue-find]').click();
    await page.locator('[data-job-id="priority"] [data-queue-find] button').focus();
    await page.keyboard.press('Enter');
    const afterFind = await page.evaluate(() => window.queueSocket.messages.filter(message => message.action === 'queue_promote').length);
    assert.equal(afterFind, beforeFind, 'Find pointer and keyboard clicks never promote');
    const actionGeometry = await findButtons.evaluateAll(buttons => buttons.map(button => ({button:button.getBoundingClientRect().toJSON(), row:button.closest('.styler-queue-task').getBoundingClientRect().toJSON()})));
    assert.ok(actionGeometry.every(({button,row}) => button.top >= row.top && button.bottom <= row.bottom && button.right <= row.right), 'Find controls fit inside stable row bounds');
    const golden = await page.locator('.styler-queue-task[data-promoted="true"]').evaluateAll(rows => rows.map(row => ({
        state:row.dataset.state, shadow:getComputedStyle(row).boxShadow, glow:getComputedStyle(row, '::after').animationName
    })));
    assert.deepEqual(golden.map(row => row.state), ['complete', 'running', 'queued'], 'Gold persists across every lifecycle state');
    assert.ok(golden.every(row => row.shadow !== 'none' && row.glow === 'styler-queue-golden-glow'));
    await page.locator('#styler-job-queue').screenshot({path:'/tmp/styler-queue-golden.png'});
    await page.evaluate(() => window.emitQueue({state:'failed', job_id:'running', queue_failed:true,
        completed_at:Date.now()/1000, message:'CUDA out of memory'}));
    await page.waitForTimeout(400);
    const failure = page.locator('.styler-queue-task[data-job-id="running"]');
    assert.equal(await failure.getAttribute('data-state'), 'failed');
    assert.equal(await failure.locator('.styler-queue-task-state').textContent(), 'failed');
    assert.equal(await failure.getAttribute('title'), 'CUDA out of memory');
    assert.equal(await failure.locator('wa-progress-bar').count(), 0);
    assert.equal(await failure.evaluate(row => getComputedStyle(row).borderInlineStartColor), 'rgb(139, 30, 39)');
    await page.locator('#styler-job-queue').screenshot({path:'/tmp/styler-queue-failed.png'});
    let refillRequests = 0;
    await page.route('**/styles/style_page*', route => {
        refillRequests++;
        return route.fulfill({status:200, contentType:'application/json', headers:{'Access-Control-Allow-Origin':'*'},
            body:JSON.stringify({tag:'LOST', page:0, page_size:5, total:5, keys:['b','c','d','e','f'],
                entries:{f:'<div data-style-entry data-style-key="f"><div class="card-overview" id="card_f">Next style</div></div>'}})});
    });
    await page.evaluate(() => {
        document.body.insertAdjacentHTML('beforeend', '<wa-tab-panel name="tag_LOST"><div id="lost-page" data-style-page data-page="0" data-page-size="5" data-page-total="6" data-page-category="LOST" data-page-url="http://fixture.test/styles/style_page"><span data-lost-summary data-style-page-summary data-page="0" data-page-size="5"></span><wa-button data-style-page-nav="previous"></wa-button><wa-button data-style-page-nav="next"></wa-button><div data-style-entry data-style-key="idle-resolved" id="idle-lost-entry" style="height:160px;width:300px"><div id="card_idle-resolved" class="card-overview"><wa-details><div id="style_details_idle-resolved"></div></wa-details></div></div><div data-style-entry data-style-key="b" id="still-lost"><div class="card-overview" id="card_b"><wa-details><input value="Keep my edit"></wa-details></div></div><div data-style-entry data-style-key="c"><div class="card-overview" id="card_c"></div></div><div data-style-entry data-style-key="d"><div class="card-overview" id="card_d"></div></div><div data-style-entry data-style-key="e"><div class="card-overview" id="card_e"></div></div></div></wa-tab-panel>');
        window.savedPageSurvivor = document.getElementById('still-lost');
        window.savedPageSurvivor.querySelector('wa-details').open = true;
        window.savedPageSurvivor.querySelector('input').focus();
        window.emitQueue({state:'styles_scan_image_complete', job_id:'idle-normalization', style_tag:'idle-resolved',
            engine:'qwen', tags:['painting'], image_tags:['painting'], counts:[], lost:false, lost_count:5});
    });
    await page.locator('#idle-lost-entry.lost-style-dissolving').waitFor({state:'attached'});
    assert.equal(await page.locator('#idle-lost-entry').evaluate(entry => getComputedStyle(entry).pointerEvents), 'none');
    await page.locator('#idle-lost-entry').waitFor({state:'detached', timeout:2000});
    await page.locator('#lost-page [data-style-key="f"]').waitFor({state:'attached', timeout:3000});
    assert.equal(await page.locator('#lost-page > [data-style-entry]').count(), 5, 'The real page backfills after its poof');
    assert.equal(refillRequests, 1);
    assert.ok(await page.evaluate(() => document.getElementById('still-lost') === window.savedPageSurvivor
        && window.savedPageSurvivor.querySelector('wa-details').open
        && document.activeElement === window.savedPageSurvivor.querySelector('input')),
        'Browser focus and open details survive page replenishment');
    assert.deepEqual(errors, []);
    await browser.close();
    console.log('Browser queue geometry, clipping, resize, capacity, history and priority checks passed');
})().catch(error => {console.error(error); process.exit(1);});
