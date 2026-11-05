const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/artifact_editor/static/js/cpu-queue.js', 'utf8');
class Element {
    constructor() { this.dataset = {}; this.children = []; }
    setAttribute(name, value) { this[name] = value; }
    replaceChildren(...children) { this.children = children; }
    querySelector(name) { return this[name]; }
}
const rows = Object.fromEntries(['cpu', 'memory', 'swap'].map(name => {
    const row = new Element(); row.meter = new Element(); row.output = new Element();
    return [name, row];
}));
const map = new Element(), status = new Element();
const context = {panel: {querySelector: selector => rows[selector.match(/"(.*?)"/)[1]]},
    document: {getElementById: id => id === 'cpu-core-map' ? map : status,
        createElement: () => new Element()}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('    function updateResources'), source.indexOf("    document.addEventListener('cpu:queue-plan'")), context);
const update = metrics => context.updateResources(metrics);
update({cpu_percent: 98, core_percent: [100, 96], cpu_pressure_percent: 22,
    memory_used_mib: 3072, memory_total_mib: 8192, swap_used_mib: 0, swap_total_mib: 0});
assert.equal(rows.cpu.output.textContent, '98%');
assert.equal(rows.cpu.dataset.level, 'high');
assert.equal(rows.memory.output.textContent, '3.0 / 8.0 GiB');
assert.equal(rows.swap.output.textContent, 'Off');
assert.match(status.textContent, /CPU near capacity/);
assert.equal(map.children.length, 2);
update({cpu_percent: 25, core_percent: [100, 0, 0, 0], cpu_pressure_percent: 0});
assert.match(status.textContent, /1 logical CPU near capacity/);
update({cpu_percent: 25, io_wait_percent: 40});
assert.match(status.textContent, /I\/O wait/);
update({});
assert.equal(rows.cpu.output.textContent, '—');
assert.equal(rows.cpu.dataset.available, 'false');
assert.equal(map.children.length, 0);
assert.match(status.textContent, /Sampling/);
console.log('CPU resource meters passed');
