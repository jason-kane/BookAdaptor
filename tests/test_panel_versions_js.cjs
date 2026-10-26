const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('Start buttons use each service state and reset when status becomes unavailable', async () => {
  const buttons = ['redis', 'redisinsight', 'missing'].map(service => ({
    dataset: {memberStart: service, memberName: service}, symbol: {}, label: {},
    querySelector(selector) {return selector === '.member-launch-play' ? this.symbol : this.label;},
    setAttribute(name, value) {this[name] = value;}
  }));
  const handlers = {};
  let fail = false;
  let states = ['running', 'exited'];
  vm.runInNewContext(fs.readFileSync('home/static/js/panel-versions.js', 'utf8'), {
    document: {
      querySelectorAll: selector => selector === '[data-member-start]' ? buttons : [],
      querySelector: () => null,
      addEventListener: (name, fn) => { handlers[name] = fn; }
    },
    setInterval() {},
    fetch: async () => {
      if (fail) throw new Error('offline');
      return {ok: true, json: async () => ({redis: states.map((state, index) => ({
        service: buttons[index].dataset.memberStart, state
      }))})};
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(buttons.map(button => button.dataset.running), ['true', 'false', 'false']);
  assert.equal(buttons[0].symbol.textContent, '■');
  assert.equal(buttons[0].label.textContent, 'Stop');
  assert.equal(buttons[0]['aria-label'], 'Stop redis');
  states = ['restarting', 'running'];
  await handlers['service-members-started']();
  assert.deepEqual(buttons.map(button => button.dataset.running), ['false', 'true', 'false']);
  assert.equal(buttons[0].symbol.textContent, '▶');
  assert.equal(buttons[0].label.textContent, 'Start');
  fail = true;
  await handlers['service-members-started']();
  assert.deepEqual(buttons.map(button => button.dataset.running), ['false', 'false', 'false']);
});
