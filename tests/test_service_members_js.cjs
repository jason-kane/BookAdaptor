const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

for (const running of [false, true]) {
  test(`member button sends ${running ? 'stop' : 'start'} when running=${running}`, async () => {
    let click, request, refreshed = false;
    const message = {};
    const button = {
      dataset: {memberStart: 'redis', running: String(running), startUrl: '/start', stopUrl: '/stop'},
      addEventListener(name, fn) { click = fn; }, setAttribute() {}, removeAttribute() {}
    };
    const detail = {dataset: {memberDetail: 'redis'}, querySelector() { return message; }};
    const browser = {querySelectorAll(selector) {
      if (selector === '[data-member-start]') return [button];
      if (selector === '[data-member-detail]') return [detail];
      return [];
    }};
    vm.runInNewContext(fs.readFileSync('home/static/js/service-members.js', 'utf8'), {
      document: {querySelectorAll() { return [browser]; }, dispatchEvent() { refreshed = true; }},
      Event: class {},
      fetch: async (url, options) => {request = {url, options}; return {ok: true, json: async () => ({})};}
    });
    await click();
    assert.equal(request.url, running ? '/stop' : '/start');
    assert.equal(request.options.method, 'POST');
    assert.equal(button.disabled, false);
    assert.equal(refreshed, true);
    assert.match(message.textContent, running ? /^Stopped/ : /^Started/);
  });
}
