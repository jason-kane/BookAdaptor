const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function element() {
  return {handlers: {}, attrs: {}, dataset: {},
    addEventListener(name, handler) { this.handlers[name] = handler; },
    setAttribute(name, value) { this.attrs[name] = value; },
    focus() { this.focused = true; }
  };
}
function setup(view = 'array') {
  const cards = [100, 160].map(height => {
    const card = element();
    card.button = element();
    card.classes = new Set();
    card.classList = {add(name) {card.classes.add(name);}, remove(name) {card.classes.delete(name);}};
    card.dataset.expanded = 'false';
    card.styles = {};
    card.style = {
      setProperty(name, value) { card.styles[name] = value; },
      removeProperty(name) { delete card.styles[name]; }
    };
    card.querySelector = () => card.button;
    card.getBoundingClientRect = () => { throw new Error('Grid expansion must not depend on measured height'); };
    return card;
  });
  const panels = {dataset: {view}, querySelectorAll: () => cards};
  const table = {hidden: view !== 'list'};
  const buttons = ['list', 'icons', 'array'].map(view => {
    const button = element();
    button.dataset.panelView = view;
    return button;
  });
  vm.runInNewContext(fs.readFileSync('home/static/js/panel-view.js', 'utf8'), {
    document: {getElementById: id => id === 'service-panels' ? panels : table, querySelectorAll: () => buttons}
  });
  return {cards, panels, buttons, table};
}

test('one entry expands without measuring pixel height and toggles back', () => {
  const {cards} = setup();
  cards[0].button.handlers.click();
  assert.equal(cards[0].styles['--expanded-panel-height'], undefined);
  assert.equal(cards[0].dataset.expanded, 'true');
  assert.equal(cards[0].button.attrs['aria-expanded'], 'true');
  cards[1].button.handlers.click();
  assert.equal(cards[0].dataset.expanded, 'false');
  assert.equal(cards[1].dataset.expanded, 'true');
  cards[1].button.handlers.click();
  assert.equal(cards[1].dataset.expanded, 'false');
});

test('the click following a header drag does not toggle the panel', () => {
  const {cards} = setup();
  const header = cards[0].button;
  header.dataset.suppressPanelClick = 'true';
  let prevented = false;
  header.handlers.click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(cards[0].dataset.expanded, 'false');
  header.handlers.click();
  assert.equal(cards[0].dataset.expanded, 'true');
});

test('controls are preserved; Escape and changing views collapse rows', () => {
  const {cards, panels, buttons} = setup();
  assert.equal(cards[0].handlers.click, undefined);
  assert.equal(cards[0].dataset.expanded, 'false');
  cards[0].button.handlers.click();
  cards[0].handlers.keydown({key: 'Escape'});
  assert.equal(cards[0].dataset.expanded, 'false');
  assert.equal(cards[0].button.focused, true);
  cards[0].button.handlers.click();
  buttons[1].handlers.click();
  assert.equal(panels.dataset.view, 'icons');
  assert.equal(cards[0].dataset.expanded, 'false');
  cards[0].button.handlers.click();
  assert.equal(cards[0].dataset.expanded, 'false');
  buttons[0].handlers.click();
  assert.equal(panels.dataset.view, 'list');
});


test('press feedback preserves control clicks', () => {
  const {cards} = setup();
  const card = cards[0];
  card.handlers.pointerdown({button: 0, composedPath: () => [{matches: selector => selector === '[data-panel-expand]'}]});
  assert.ok(card.classes.has('is-pressed'));
  card.handlers.pointerup();
  assert.ok(!card.classes.has('is-pressed'));
  card.handlers.pointerdown({button: 0, composedPath: () => [{matches: selector => selector.includes('a, button')} ]});
  assert.ok(!card.classes.has('is-pressed'));
  card.button.handlers.keydown({key: ' '});
  assert.ok(card.classes.has('is-pressed'));
  card.button.handlers.keyup();
  assert.ok(!card.classes.has('is-pressed'));
  card.handlers.pointerdown({button: 0, composedPath: () => [{matches: selector => selector === '[data-panel-expand]'}]});
  card.handlers.pointercancel();
  assert.ok(!card.classes.has('is-pressed'));
});


test('Array retains expansion and activation feedback', () => {
  const {cards, panels, buttons} = setup();
  buttons[2].handlers.click();
  assert.equal(panels.dataset.view, 'array');
  assert.equal(buttons[2].attrs['aria-pressed'], 'true');
  cards[0].handlers.pointerdown({button: 0, composedPath: () => [{matches: selector => selector === '[data-panel-expand]'}]});
  assert.ok(cards[0].classes.has('is-pressed'));
  cards[0].handlers.pointerup();
  cards[0].button.handlers.click();
  assert.equal(cards[0].dataset.expanded, 'true');
  assert.equal(cards[0].styles['--expanded-panel-height'], undefined);
  buttons[0].handlers.click();
  assert.equal(panels.dataset.view, 'list');
  assert.equal(cards[0].dataset.expanded, 'false');
});


test('List is a selectable table and does not react as a pushbutton', () => {
  const {cards, panels, buttons, table} = setup('list');
  cards[0].handlers.pointerdown({button: 0, composedPath: () => [{matches: selector => selector === '[data-panel-expand]'}]});
  assert.equal(cards[0].handlers.click, undefined);
  assert.ok(!cards[0].classes.has('is-pressed'));
  assert.equal(cards[0].dataset.expanded, 'false');
  assert.equal(table.hidden, false);
  buttons[2].handlers.click();
  assert.equal(table.hidden, true);
  assert.equal(panels.dataset.view, 'array');
  buttons[0].handlers.click();
  assert.equal(table.hidden, false);
});


test('activation flashes and the effect is cleared when the CSS animation ends', () => {
  const {cards} = setup();
  cards[0].button.handlers.click();
  assert.ok(cards[0].classes.has('is-activated'));
  cards[0].handlers.animationend({animationName: 'brass-activation'});
  assert.ok(!cards[0].classes.has('is-activated'));
  cards[0].button.handlers.click();
  assert.ok(cards[0].classes.has('is-activated'));
});


test('the banner is the toggle and keyboard activation preserves its contents', () => {
  const {cards} = setup();
  cards[0].button.textContent = 'Library version and badge';
  cards[0].button.handlers.keydown({key: 'Enter', preventDefault(){}});
  assert.equal(cards[0].dataset.expanded, 'true');
  cards[0].button.handlers.keydown({key: ' ', preventDefault(){}});
  assert.equal(cards[0].dataset.expanded, 'false');
  assert.equal(cards[0].button.textContent, 'Library version and badge');
  assert.equal(cards[0].handlers.click, undefined);
});
