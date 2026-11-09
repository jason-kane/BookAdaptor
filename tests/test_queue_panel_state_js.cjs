const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/artifact_editor/static/js/queue-panels.js', 'utf8');
const saved = new Map();
function load(disabled = false) {
    const panels = ['cpu', 'gpu'].map(name => {
        const classes = new Set();
        const attributes = {};
        const toggle = {setAttribute: (key, value) => {attributes[key] = value;},
            addEventListener: (name, callback) => {toggle[name] = callback;}};
        return {id: `${name}-job-queue`, toggle, attributes,
            getAttribute: () => `${name.toUpperCase()} queue`,
            querySelector: selector => selector.includes('-queue-toggle') ? toggle : null,
            classList: {contains: value => classes.has(value), toggle: (name, enabled) => {
                if (enabled) classes.add(name); else classes.delete(name);
            }}};
    });
    vm.runInNewContext(source, {document: {querySelectorAll: () => panels},
        localStorage: {getItem: key => {if (disabled) throw Error('disabled'); return saved.get(key);},
            setItem: (key, value) => {if (disabled) throw Error('disabled'); saved.set(key, value);}}});
    return panels;
}
let [cpu, gpu] = load();
cpu.toggle.click();
assert.equal(cpu.attributes['aria-expanded'], 'false');
assert.equal(saved.get('bookadaptor:queue-panel-state:cpu-job-queue'), 'minimized');
[cpu, gpu] = load();
assert.equal(cpu.classList.contains('is-minimized'), true);
assert.equal(gpu.classList.contains('is-minimized'), false);
cpu.toggle.click();
gpu.toggle.click();
[cpu, gpu] = load();
assert.equal(cpu.attributes['aria-expanded'], 'true');
assert.equal(cpu.attributes['aria-label'], 'Minimize CPU queue');
assert.equal(gpu.attributes['aria-expanded'], 'false');
assert.equal(gpu.attributes['aria-label'], 'Expand GPU queue');
assert.doesNotThrow(() => load(true)[0].toggle.click());
console.log('Independent CPU/GPU toggle state survives reload; disabled storage is safe');
