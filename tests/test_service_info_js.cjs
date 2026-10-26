const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
  const handlers = {};
  const button = {
    disabled: false, setAttribute() {}, removeAttribute() {},
    addEventListener(name, fn) { handlers[`button:${name}`] = fn; }
  };
  const message = {textContent: ''};
  const content = {innerHTML: '', querySelectorAll() { return []; }};
  const group = {
    dataset: {infoUrl: '/programs/panels/redis/info'},
    querySelector(selector) {
      return {'[data-info-refresh]': button, '[data-info-message]': message,
              '[data-info-content]': content}[selector];
    },
    addEventListener(name, fn) { handlers[name] = fn; }
  };
  const calls = [];
  const timers = [];
  const document = {
    hidden: false, querySelectorAll() { return [group]; },
    addEventListener(name, fn) { handlers[`document:${name}`] = fn; }
  };
  let fail = false;
  let selection = ''; 
  vm.runInNewContext(fs.readFileSync('home/static/js/service-info.js', 'utf8'), {
    document, Date, getSelection: () => ({toString: () => selection}),
    setInterval(fn, delay) { timers.push({fn, delay}); },
    fetch: async (...args) => {
      calls.push(args);
      return {ok: !fail, text: async () => '<p>Fresh Compose status</p>'};
    }
  });
  return {handlers, group, content, button, message, document, calls, timers,
          fail() { fail = true; }, select(text) { selection = text; }};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('Info loads lazily, refreshes every 60 seconds only while visible', async () => {
  const s = setup();
  assert.equal(s.calls.length, 0);
  assert.equal(s.timers[0].delay, 60000);
  s.handlers['wa-tab-show']({target: s.group, detail: {name: 'info'}});
  await settle();
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0][0], '/programs/panels/redis/info');
  assert.equal(s.calls[0][1].cache, 'no-store');
  assert.equal(s.content.innerHTML, '<p>Fresh Compose status</p>');
  s.timers[0].fn();
  await settle();
  assert.equal(s.calls.length, 2);
  s.document.hidden = true;
  s.timers[0].fn();
  await settle();
  assert.equal(s.calls.length, 2);
  s.document.hidden = false;
  s.handlers['wa-tab-show']({target: s.group, detail: {name: 'open'}});
  s.timers[0].fn();
  await settle();
  assert.equal(s.calls.length, 2);
});

test('manual refresh preserves previous data and reports errors', async () => {
  const s = setup();
  s.handlers['wa-tab-show']({target: s.group, detail: {name: 'info'}});
  await settle();
  s.fail();
  await s.handlers['button:click']();
  assert.equal(s.content.innerHTML, '<p>Fresh Compose status</p>');
  assert.match(s.message.textContent, /not been updated/);
  assert.equal(s.button.disabled, false);
});

test('nested tab events are ignored and overlapping requests do not duplicate queries', async () => {
  const s = setup();
  s.handlers['wa-tab-show']({target: {}, detail: {name: 'info'}});
  await settle();
  assert.equal(s.calls.length, 0);
  s.handlers['wa-tab-show']({target: s.group, detail: {name: 'info'}});
  s.timers[0].fn();
  await s.handlers['button:click']();
  await settle();
  assert.equal(s.calls.length, 1);
});


test('selected text is preserved instead of refreshing the snapshot', async () => {
  const s = setup();
  s.handlers['wa-tab-show']({target: s.group, detail: {name: 'info'}});
  await settle();
  s.select('copied service details');
  s.timers[0].fn();
  await settle();
  assert.equal(s.calls.length, 1);
  assert.equal(s.content.innerHTML, '<p>Fresh Compose status</p>');
  s.select('');
  s.timers[0].fn();
  await settle();
  assert.equal(s.calls.length, 2);
});
