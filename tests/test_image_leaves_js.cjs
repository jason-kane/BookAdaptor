const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor() {
        this.children = [];
        this.events = {};
        this.attributes = {};
        this.dataset = {};
        this.properties = {};
        this.style = {setProperty: (key, value) => {this.properties[key] = value;}};
    }
    append(child) {child.parent = this; this.children.push(child);}
    remove() {if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);}
    setAttribute(key, value) {this.attributes[key] = value;}
    addEventListener(name, callback) {this.events[name] = callback;}
}

(async () => {
    const stage = new Element();
    const identity = {author: 'Author', title: 'Book', chapter_number: 3, language: 'english'};
    stage.dataset.chapterIdentity = JSON.stringify(identity);
    const documentEvents = {}, windowEvents = {};
    const timers = [];
    const context = {
        performance: {now: () => 100},
        document: {hidden: false, createElement: () => new Element(), querySelector: () => stage,
            addEventListener: (name, callback) => {documentEvents[name] = callback;}},
        window: {setTimeout: callback => timers.push(callback),
            setInterval: () => {throw new Error('Idle image polling must not be scheduled');},
            addEventListener: (name, callback) => {windowEvents[name] = callback;}},
        fetch: () => {throw new Error('Image reveals must use WebSocket messages, not HTTP');},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('src/artifact_editor/static/js/image-leaves.js', 'utf8'), context);
    vm.runInContext(fs.readFileSync('src/artifact_editor/static/js/library-image-leaves.js', 'utf8'), context);
    const emit = (state, fields = {}) => documentEvents['chapter:image-event']({
        detail: {state, ...identity, ...fields},
    });
    const existing = {id: 'existing', index: 0, url: '/old.png'};
    emit('chapter_image_snapshot', {images: [existing]});
    assert.equal(stage.children.length, 0, 'Existing chapter images are the baseline, not a replay');
    const fresh = {id: 'new', index: 1, url: '/new.png'};
    emit('chapter_image_complete', {image: fresh, title: 'Another book'});
    assert.equal(stage.children.length, 0, 'Events from other chapters are ignored');
    emit('chapter_image_complete', {image: fresh});
    assert.equal(stage.children.length, 1);
    const first = stage.children[0];
    assert.equal(first.className, 'image-leaf from-right');
    assert.equal(first.children[0].src, '/new.png');
    assert.equal(first.attributes['aria-hidden'], 'true');
    emit('chapter_image_complete', {image: fresh});
    assert.equal(stage.children.length, 1, 'Repeated completion snapshots never duplicate a reveal');
    const redraw = {id: 'redraw', index: 0, url: '/redraw.png'};
    emit('chapter_image_complete', {image: redraw});
    assert.equal(stage.children.length, 2, 'A redraw of the same image can be revealed again');
    const second = stage.children[1];
    assert.equal(second.className, 'image-leaf');
    assert.equal(second.properties['--leaf-delay'], '650ms');
    first.events.animationend();
    second.children[0].events.error();
    assert.equal(stage.children.length, 0, 'Animation and load errors clean up their elements');
    context.document.hidden = true;
    const hiddenImage = {id: 'hidden', index: 2, url: '/hidden.png'};
    emit('chapter_image_complete', {image: hiddenImage});
    assert.equal(stage.children.length, 0, 'Hidden pages defer animation');
    context.document.hidden = false;
    documentEvents.visibilitychange();
    assert.equal(stage.children.length, 1, 'Deferred images reveal when the page is visible');
    stage.children[0].remove();
    const missed = {id: 'missed', index: 3, url: '/missed.png'};
    emit('chapter_image_snapshot', {images: [existing, fresh, redraw, hiddenImage, missed]});
    assert.equal(stage.children.length, 1, 'Reconnect snapshots reveal only missed completions');
    assert.equal(stage.children[0].children[0].src, '/missed.png');
    stage.children[0].remove();
    windowEvents.pagehide();
    emit('chapter_image_complete', {image: {id: 'after-leaving', url: '/unused.png'}});
    assert.equal(stage.children.length, 0, 'Leaving the page stops reveals');
    assert.match(fs.readFileSync('src/artifact_editor/static/styles/image-leaves.css', 'utf8'), /prefers-reduced-motion/);
    console.log('Shared falling-image renderer and library completion lifecycle passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
