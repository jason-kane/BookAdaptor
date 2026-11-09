const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function card(title, state = '') {
  const button = {disabled: state === 'running', dataset: {moveTarget: 'storage'},
    addEventListener(name, callback) { this[name] = callback; }};
  const message = {textContent: ''};
  return {dataset: {author: 'Writer', title, location: 'active', transferState: state,
    locationUrl: `/library/Writer/${title}/location`}, button, message,
    querySelectorAll() { return [button]; },
    querySelector(selector) { return selector === '[data-move-target]' ? button : message; }};
}
const first = card('First', 'running'), second = card('Second');
const cards = [first, second], sockets = [], requests = [], timers = [];
const root = {dataset: {transferWebsocket: 'ws://localhost:5001/library-transfers-ws', storageAvailable: 'true'},
  querySelectorAll() { return cards; }};
class Socket {
  constructor(url) { this.url = url; this.listeners = {}; sockets.push(this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  emit(name, status) { this.listeners[name]?.(name === 'message' ? {data: JSON.stringify(status)} : {}); }
  close() { this.emit('close'); }
}
let respond, reloads = 0;
const window = {location: {reload() { reloads++; }},
  addEventListener(name, callback) { this[name] = callback; }};
const context = {document: {getElementById() { return root; }}, window, WebSocket: Socket,
  fetch(url, options) { requests.push({url, options}); return new Promise(resolve => { respond = resolve; }); },
  setTimeout(callback, delay) { const timer = {callback, delay}; timers.push(timer); return timer; },
  clearTimeout(timer) { if (timer) timer.cancelled = true; }};
vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/library-storage.js', 'utf8'), context);
assert.equal(sockets.length, 1);
assert.equal(requests.length, 0, 'An in-progress book must not start HTTP status polling');
const event = fields => ({type: 'library_transfer', author: 'Writer', title: 'First', ...fields});
sockets[0].emit('message', event({state: 'running', bytes_done: '3', bytes_total: '10', message: 'Copying'}));
assert.equal(first.message.textContent, 'Copying 30%');
assert.equal(first.button.disabled, true);
sockets[0].emit('message', event({state: 'complete', target: 'active', message: 'Historical completion'}));
assert.equal(reloads, 0, 'An initial completed snapshot at the current location must not reload');

(async () => {
  const submission = second.button.click();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, 'POST');
  sockets[0].emit('message', event({title: 'Second', state: 'failed', message: 'Destination full'}));
  respond({ok: true, json: async () => ({job_id: 'move', state: 'queued'})});
  await submission;
  assert.equal(second.dataset.transferState, 'failed', 'A late POST reply must not overwrite pushed state');
  assert.equal(second.message.textContent, 'Destination full');
  assert.equal(second.button.disabled, false);
  sockets[0].emit('close');
  assert.equal(timers.length, 1);
  assert.equal(requests.length, 1, 'Disconnect must reconnect the socket, never poll HTTP');
  timers[0].callback();
  assert.equal(sockets.length, 2);
  sockets[1].emit('message', event({state: 'complete', target: 'storage', message: 'Moved'}));
  sockets[1].emit('message', event({state: 'complete', target: 'storage', message: 'Moved'}));
  assert.equal(reloads, 1);
  window.pagehide();
  assert.equal(timers.length, 1, 'Leaving the page must not schedule another connection');
  assert.ok(requests.every(request => request.options.method === 'POST'));

  const shelfCard = card('Shelf test');
  shelfCard.button.disabled = true;
  const shelfButton = {disabled:false, dataset:{moveTarget:'shelf', moveSource:'active'},
    addEventListener(name, callback) { this[name] = callback; }};
  shelfCard.querySelectorAll = () => [shelfCard.button, shelfButton];
  const shelfRoot = {dataset:{storageAvailable:'false', shelfAvailable:'true',
    transferWebsocket:'ws://localhost:5001/library-transfers-ws'}, querySelectorAll:() => [shelfCard]};
  let shelfRequest;
  vm.runInNewContext(fs.readFileSync('src/artifact_editor/static/js/library-storage.js', 'utf8'), {
    ...context, document:{getElementById:() => shelfRoot},
    fetch:async (url, options) => { shelfRequest=JSON.parse(options.body); return {ok:true,json:async()=>({job_id:'shelf'})}; }
  });
  await shelfButton.click();
  assert.equal(shelfRequest.target, 'shelf');
  assert.equal(shelfRequest.source, 'active');
  assert.equal(shelfCard.button.disabled, true);
  assert.equal(shelfButton.disabled, true);
  sockets.at(-1).emit('message', {type:'library_transfer',author:'Writer',title:'Shelf test',state:'failed',message:'Retry'});
  assert.equal(shelfButton.disabled, false, 'Shelf remains available when external Storage is disconnected');
  assert.equal(shelfCard.button.disabled, true, 'Disconnected Storage must remain unavailable');
  console.log('Library transfer WebSocket UI passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
