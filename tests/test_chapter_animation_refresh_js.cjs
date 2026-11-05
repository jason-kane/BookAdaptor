const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const handlers = new Map();
    const requests = [];
    const panel = {
        dataset: {imageEditorUrl: '/timeline/image/2?tab=animation', imageEditorTarget: '#timeline-image-editor-2'},
        closest() { return null; },
    };
    const status = {
        dataset: {videoJob: 'animation-job', videoState: 'running'},
        closest(selector) {
            return ['[data-image-editor-url]', '[data-animation-description-status]'].includes(selector) ? panel : null;
        },
    };
    const players = {};
    const videos = [{id: 'timeline-animation-player-2'}];
    let disposed = 0;
    function videojs(video) {
        const element = {classList: {contains: name => name === 'chapter-animation-video'}};
        const player = {el: () => element, isDisposed: () => false,
            dispose() { disposed++; delete players[video.id]; }};
        players[video.id] = player;
        return player;
    }
    videojs.getPlayers = () => players;
    videojs.getPlayer = id => players[id];
    const document = {
        addEventListener(name, handler) {
            if (!handlers.has(name)) handlers.set(name, []);
            handlers.get(name).push(handler);
        },
        querySelectorAll(selector) {
            if (selector === 'video.chapter-animation-video') return videos;
            if (selector.includes('[data-video-job]')) return [status];
            return [];
        },
        getElementById() { return null; },
    };
    const htmx = {ajax(method, url, options) {
        requests.push({method, url, ...options});
        return Promise.resolve();
    }};
    vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/chapter-timeline.js', 'utf8'), {
        document, window: {videojs}, videojs, htmx, console,
    });
    const dispatch = (name, event = {}) => handlers.get(name)?.forEach(handler => handler(event));
    return {status, requests, players, videos, dispatch, disposed: () => disposed};
}

test('animation completion refreshes its editor once with the animation tab selected', () => {
    const s = setup();
    s.dispatch('htmx:wsAfterMessage');
    assert.equal(s.requests.length, 0);
    s.status.dataset.videoState = 'complete';
    s.dispatch('htmx:wsAfterMessage');
    s.dispatch('htmx:wsAfterMessage');
    assert.equal(s.requests.length, 1);
    assert.equal(s.requests[0].url, '/timeline/image/2?tab=animation');
    assert.equal(s.requests[0].target, '#timeline-image-editor-2');
    assert.equal(s.requests[0].swap, 'innerHTML');
    s.status.dataset.videoState = 'running';
    s.dispatch('htmx:wsAfterMessage');
    s.status.dataset.videoState = 'complete';
    s.dispatch('htmx:wsAfterMessage');
    assert.equal(s.requests.length, 2);
});

test('editor swap disposes the old video player and initializes the replacement', () => {
    const s = setup();
    s.dispatch('htmx:afterSettle');
    const previous = s.players[s.videos[0].id];
    assert.ok(previous);
    s.dispatch('htmx:beforeCleanupElement', {detail: {elt: {contains: () => true}}});
    assert.equal(s.disposed(), 1);
    s.dispatch('htmx:afterSettle');
    assert.ok(s.players[s.videos[0].id]);
    assert.notEqual(s.players[s.videos[0].id], previous);
});

test('failed animation does not reload its player', () => {
    const s = setup();
    s.status.dataset.videoState = 'failed';
    s.dispatch('htmx:wsAfterMessage');
    assert.equal(s.requests.length, 0);
});
