const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(extra = {}) {
    const events = {};
    return Object.assign({ events, dataset: {}, style: {}, attributes: {},
        addEventListener(name, handler) { events[name] = handler; },
        setAttribute(name, value) { this.attributes[name] = value; },
    }, extra);
}
function canvas() {
    const draws = [];
    const context = { drawImage(...args) { draws.push(args); }, fillRect() {}, strokeRect() {}, clearRect() {}, setLineDash() {}, fillText() {} };
    return element({ width: 800, height: 400, draws,
        getContext() { return context; },
        getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 200 }; },
        focus() {}, setPointerCapture() {},
    });
}
function picker() {
    const controls = {
        '[data-source-preview]': element({ complete: true, naturalWidth: 800, naturalHeight: 400 }),
        '[data-source-editor]': canvas(),
        'input[name="source_motion"]': element({ value: JSON.stringify({ first: {x:.5,y:.5,zoom:1}, last: {x:.5,y:.5,zoom:1} }) }),
        'input[name="image_source"]': element({ value: 'first.png' }),
        '[data-source-motion-preview]': canvas(),
        '[data-source-play]': element(),
        '[data-source-scrub]': element(),
        '[data-source-caption]': element(),
    };
    for (const frame of ['first', 'last']) {
        controls[`[data-source-frame="${frame}"]`] = canvas();
        controls[`[data-source-zoom="${frame}"]`] = element({ dataset: {sourceZoom: frame}, value: 1 });
        controls[`[data-source-zoom-label="${frame}"]`] = element();
        controls[`[data-source-edit="${frame}"]`] = element({ dataset: {sourceEdit: frame} });
    }
    const result = element({ controls, isConnected: true, dataset: {outputWidth: 160, outputHeight: 80, motionDuration: 4},
        querySelector(selector) { assert.ok(controls[selector], selector); return controls[selector]; },
        querySelectorAll(selector) {
            if (selector === '[data-source-zoom]') return ['first','last'].map(f=>controls[`[data-source-zoom="${f}"]`]);
            if (selector === '[data-source-edit]') return ['first','last'].map(f=>controls[`[data-source-edit="${f}"]`]);
            if (selector === 'button[data-source-image]') return result.thumbnails;
            throw new Error(selector);
        },
    });
    result.thumbnails = ['first.png','second.png'].map(name=>element({ dataset: {sourceImage:name,sourceUrl:'/'+name}, closest(){return result;} }));
    return result;
}
function setup() {
    const first = picker(), second = picker(), handlers = {}, ticks = [];
    const context = vm.createContext({
        document: { querySelectorAll(){return [first,second];}, addEventListener(name,handler){handlers[name]=handler;} },
        performance: {now(){return 0;}},
        requestAnimationFrame(callback) { ticks.push(callback); return ticks.length; }, cancelAnimationFrame(){},
    });
    vm.runInContext(fs.readFileSync('src/artifact_editor/static/js/source-images.js','utf8'),context);
    const state = p=>JSON.parse(p.controls['input[name="source_motion"]'].value);
    const zoom = (p,frame,value)=>{const input=p.controls[`[data-source-zoom="${frame}"]`];input.value=value;input.events.input();};
    return {first,second,handlers,ticks,state,zoom,context};
}

test('independent zoom and keyboard position changes stay within their picker',()=>{
    const s=setup();
    s.zoom(s.first,'first',2);
    assert.equal(s.state(s.first).first.zoom,2);
    assert.equal(s.state(s.first).last.zoom,1);
    assert.equal(s.state(s.second).first.zoom,1);
    const editor=s.first.controls['[data-source-editor]'];
    editor.events.keydown({key:'ArrowRight',shiftKey:true,preventDefault(){}});
    assert.equal(s.state(s.first).first.x,.55);
    assert.equal(s.state(s.second).first.x,.5);
    editor.events.pointerdown({button:0,clientX:220,clientY:100,pointerId:1,preventDefault(){}});
    editor.events.pointermove({clientX:260,clientY:140});
    editor.events.pointerup();
    assert.ok(Math.abs(s.state(s.first).first.x-.65)<1e-9);
    assert.ok(Math.abs(s.state(s.first).first.y-.7)<1e-9);
});

test('browsing preserves edits per illustration and never saves them to the server',()=>{
    const s=setup();
    s.zoom(s.first,'first',3);
    const click=button=>s.handlers.click({target:{closest(){return button;}}});
    click(s.first.thumbnails[1]);
    assert.equal(s.state(s.first).first.zoom,1);
    s.zoom(s.first,'last',4);
    click(s.first.thumbnails[0]);
    assert.equal(s.state(s.first).first.zoom,3);
    assert.equal(s.state(s.first).last.zoom,1);
    click(s.first.thumbnails[1]);
    assert.equal(s.state(s.first).last.zoom,4);
    assert.equal(s.state(s.second).last.zoom,1);
});

test('motion preview reaches the selected final region and stops',()=>{
    const s=setup();
    s.zoom(s.first,'last',4);
    const p=s.first.controls;
    p['[data-source-play]'].events.click();
    assert.equal(p['[data-source-play]'].textContent,'Stop preview');
    s.ticks.shift()(2000);
    assert.equal(p['[data-source-scrub]'].value,500);
    s.ticks.shift()(4000);
    assert.equal(p['[data-source-scrub]'].value,1000);
    assert.equal(p['[data-source-play]'].textContent,'Preview motion');
    const finalDraw=p['[data-source-motion-preview]'].draws.at(-1);
    assert.deepEqual(finalDraw.slice(1,5),[300,150,200,100]);
});

test('crop geometry stays within the source at every supported output aspect',()=>{
    const s=setup();
    for(const [width,height,aspect] of [[300,900,16/9],[900,300,9/16],[800,400,1]]) {
        s.context.values={width,height,aspect};
        const box=vm.runInContext('sourceCropBox(values.width,values.height,values.aspect,{x:1,y:0,zoom:2})',s.context);
        assert.ok(box.x>=0 && box.y>=0);
        assert.ok(box.x+box.w<=width+1e-9 && box.y+box.h<=height+1e-9);
        assert.ok(Math.abs(box.w/box.h-aspect)<1e-9);
    }
});
