const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const calls = [];
    const context = {drawImage: (...args) => calls.push(args), measureText: text => ({width: text.length * 20}), strokeText() {}, fillText() {}};
    const controls = {};
    for (const name of ['canvas', 'status', 'title', 'size', 'color', 'position', 'capture', 'image', 'download', 'resolution', 'format', 'copy-status', 'stage', 'mobile', 'small', 'guides', 'overlay', 'zoom', 'zoom-label', 'pan-x', 'pan-y', 'reset']) {
        controls[name] = {value: '', style: {}, handlers: {}, addEventListener(event, handler) {this.handlers[event] = handler;}};
    }
    controls.zoom.value = '100';
    controls['pan-x'].value = '0';
    controls['pan-y'].value = '0';
    controls.canvas.getBoundingClientRect = () => ({width: 640, height: 360});
    controls.canvas.setPointerCapture = () => {};
    controls.canvas.hasPointerCapture = () => true;
    controls.canvas.releasePointerCapture = () => {};
    controls.canvas.getContext = () => context;
    controls.canvas.toBlob = callback => callback({type: 'image/png', size: 1000});
    controls.small.width = 240;
    controls.small.getContext = () => ({drawImage() {}});
    controls.resolution.value = 'youtube';
    controls.format.value = 'png';
    controls.size.value = '7';
    controls.position.value = 'bottom';
    const player = {videoWidth: 1920, videoHeight: 1080, readyState: 2, pause() {calls.push('pause');}};
    const workshop = {dataset: {filename: 'chapter-thumbnail.png'}, querySelector(selector) {return controls[selector.slice(16, -1)];}};
    const downloads = [];
    const document = {
        getElementById: () => workshop,
        querySelector: () => player,
        addEventListener() {},
        createElement(type) {
            if (type === 'canvas') return {getContext: () => context};
            return {click() {downloads.push({href: this.href, filename: this.download});}};
        },
    };
    const images = [];
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-thumbnail.js', 'utf8'), {
        document, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
        Image: function () {images.push(this);}, setTimeout: callback => callback(),
    });
    return {controls, player, calls, downloads, images};
}

test('capture and PNG export use the 1280 by 720 preset', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    assert.equal(s.controls.canvas.width, 1280);
    assert.equal(s.controls.canvas.height, 720);
    assert.equal(s.controls.download.disabled, false);
    s.controls.download.handlers.click();
    assert.equal(s.downloads[0].filename, 'chapter-thumbnail.png');
});

test('missing video metadata blocks capture with an actionable message', () => {
    const s = setup();
    s.player.videoWidth = 0;
    s.controls.capture.handlers.click();
    assert.match(s.controls.status.textContent, /Load a rendered chapter video/);
    assert.equal(s.controls.canvas.width, undefined);
});

test('portrait image fills a landscape frame without distorting it', () => {
    const s = setup();
    s.controls.image.files = [{}];
    s.controls.image.handlers.change();
    Object.assign(s.images[0], {naturalWidth: 600, naturalHeight: 1200});
    s.images[0].onload();
    const draw = s.calls.at(-1);
    assert.equal(draw[1], 0);
    assert.equal(draw[2], -920);
    assert.equal(draw[3], 1280);
    assert.equal(draw[4], 2560);
    assert.equal(s.controls.canvas.height, 720);
});


test('same-resolution mode retains the actual video dimensions', () => {
    const s = setup();
    s.controls.resolution.value = 'video';
    s.controls.capture.handlers.click();
    assert.equal(s.controls.canvas.width, 1920);
    assert.equal(s.controls.canvas.height, 1080);
});

test('YouTube image upload works without rendered video metadata', () => {
    const s = setup();
    s.player.videoWidth = 0;
    s.controls.image.files = [{}];
    s.controls.image.handlers.change();
    Object.assign(s.images[0], {naturalWidth: 1280, naturalHeight: 720});
    s.images[0].onload();
    assert.equal(s.controls.canvas.width, 1280);
    assert.equal(s.controls.canvas.height, 720);
});

test('oversized PNG is blocked instead of downloading over the limit', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    s.controls.canvas.toBlob = callback => callback({size: 3 * 1024 * 1024});
    s.controls.download.handlers.click();
    assert.equal(s.downloads.length, 0);
    assert.match(s.controls.status.textContent, /exceeds 2 MB/);
});

test('JPEG export reduces quality until it fits and uses the jpg extension', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    s.controls.format.value = 'jpeg';
    const qualities = [];
    s.controls.canvas.toBlob = (callback, mime, quality) => {
        assert.equal(mime, 'image/jpeg');
        qualities.push(quality);
        callback({size: quality > 0.8 ? 3 * 1024 * 1024 : 1000});
    };
    s.controls.download.handlers.click();
    assert.equal(s.downloads[0].filename, 'chapter-thumbnail.jpg');
    assert.equal(qualities.length, 3);
});


test('zoom and position controls change the crop used by preview and export', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    s.controls.zoom.value = '200';
    s.controls.zoom.handlers.input();
    let crop = s.calls.at(-1);
    assert.ok(Math.abs(crop[1] - (-640)) < 0.001);
    assert.ok(Math.abs(crop[2] - (-360)) < 0.001);
    assert.ok(Math.abs(crop[3] - (2560)) < 0.001);
    assert.ok(Math.abs(crop[4] - (1440)) < 0.001);
    s.controls['pan-x'].value = '100';
    s.controls['pan-y'].value = '-100';
    s.controls['pan-x'].handlers.input();
    crop = s.calls.at(-1);
    assert.ok(Math.abs(crop[1] - (0)) < 0.001);
    assert.ok(Math.abs(crop[2] - (-720)) < 0.001);
    s.controls.download.handlers.click();
    assert.equal(s.downloads.length, 1);
    assert.equal(s.controls.canvas.width, 1280);
});

test('pointer dragging accounts for preview scaling and clamps to image edges', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    s.controls.zoom.value = '200';
    s.controls.zoom.handlers.input();
    const canvas = s.controls.canvas;
    canvas.handlers.pointerdown({button: 0, pointerId: 1, clientX: 100, clientY: 100});
    canvas.handlers.pointermove({pointerId: 1, clientX: 260, clientY: 190});
    let crop = s.calls.at(-1);
    assert.ok(Math.abs(crop[1] - (-320)) < 0.001);
    assert.ok(Math.abs(crop[2] - (-180)) < 0.001);
    canvas.handlers.pointermove({pointerId: 1, clientX: 9999, clientY: -9999});
    crop = s.calls.at(-1);
    assert.ok(Math.abs(crop[1] - (0)) < 0.001);
    assert.ok(Math.abs(crop[2] - (-720)) < 0.001);
    canvas.handlers.pointercancel({pointerId: 1});
    const count = s.calls.length;
    canvas.handlers.pointermove({pointerId: 1, clientX: 0, clientY: 0});
    assert.equal(s.calls.length, count);
});

test('reset and a newly selected image restore centered fill framing', () => {
    const s = setup();
    s.controls.capture.handlers.click();
    s.controls.zoom.value = '300';
    s.controls['pan-x'].value = '70';
    s.controls.reset.handlers.click();
    assert.equal(s.controls.zoom.value, '100');
    assert.equal(s.controls['pan-x'].value, '0');
    assert.ok(Math.abs(s.calls.at(-1)[1]) < 0.001);
    s.controls.zoom.value = '200';
    s.controls.image.files = [{}];
    s.controls.image.handlers.change();
    Object.assign(s.images[0], {naturalWidth: 600, naturalHeight: 1200});
    s.images[0].onload();
    assert.equal(s.controls.zoom.value, '100');
    assert.equal(s.calls.at(-1)[2], -920);
});
