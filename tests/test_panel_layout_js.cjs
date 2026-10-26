const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const script = fs.readFileSync('home/static/js/panel-layout.js', 'utf8');
const key = 'home.array-layout.v1';
function page(saved, preferredView, legacy = false) {
  const dom = new JSDOM(`<button data-panel-view="array"></button><button data-panel-view="list"></button><button data-panel-view="icons"></button>
    <div id="service-panels" data-view="list">${['a','b','new'].map(id => `<section class="service-panel" data-panel-id="${id}" data-expanded="false"><button data-panel-expand></button><div data-service-info></div></section>`).join('')}</div>`,
    {url: 'http://localhost/', runScripts: 'outside-only'});
  const w = dom.window;
  const grid = w.document.getElementById('service-panels');
  w.eval(fs.readFileSync('home/static/js/panel-view.js', 'utf8'));
  if (saved) (legacy ? w.sessionStorage : w.localStorage).setItem(key, saved);
  if (preferredView) w.localStorage.setItem('home.panel-view', preferredView);
  w.eval(script);
  return {w, grid};
}
(async () => {
 const {w, grid} = page(JSON.stringify({view:'array',panels:[
  {id:'b',columns:2,rows:5,expanded:true,position:[2,4]},
  {id:'removed',columns:1,rows:3,expanded:false},
  {id:'a',columns:1,rows:3,expanded:false,position:[null,null]}
 ]}));
 assert.equal(grid.dataset.view,'array');
 assert.deepEqual([...grid.children].map(panel=>panel.dataset.panelId),['b','a','new']);
 const panel=grid.children[0];
 assert.equal(panel.style.getPropertyValue('--panel-start-row'),'4');
 assert.equal(panel.dataset.resizedColumns,'2');
 assert.equal(panel.dataset.expanded,'true');
 assert.equal(panel.querySelector('button').getAttribute('aria-expanded'),'true');
 panel.dataset.resizedRows='6';panel.style.setProperty('--panel-rows',6);
 await new Promise(resolve=>setImmediate(resolve));
 const saved=w.localStorage.getItem(key);
 const restored=page(saved);
 assert.equal(restored.grid.children[0].dataset.resizedRows,'6');
 assert.equal(restored.grid.children[0].style.getPropertyValue('--panel-start-column'),'2');
 assert.equal(page('{broken').grid.dataset.view,'list');
 const invalid=page(JSON.stringify({view:'array',panels:[{id:'a',columns:-5,rows:Infinity,position:[90,-3]}]}));
 assert.equal(invalid.grid.children[0].style.getPropertyValue('--panel-columns'),'');
 const newTab=page(saved,'icons');
 assert.equal(newTab.grid.dataset.view,'icons');
 assert.equal(newTab.grid.children[0].style.getPropertyValue('--panel-start-row'),'4');
 for(const mode of ['array','list','icons']) {
  newTab.w.document.querySelector(`[data-panel-view="${mode}"]`).click();
  assert.equal(newTab.w.localStorage.getItem('home.panel-view'),mode);
  const visit=page(saved,newTab.w.localStorage.getItem('home.panel-view'));
  assert.equal(visit.grid.dataset.view,mode);
  visit.w.close();
 }
 const legacy=page(saved,undefined,true);
 assert.equal(legacy.grid.children[0].dataset.resizedRows,'6');
 assert.equal(legacy.w.localStorage.getItem(key),saved);
 const malformed=page('{broken','icons');
 assert.equal(malformed.grid.dataset.view,'icons');
 w.close();restored.w.close();invalid.w.close();newTab.w.close();legacy.w.close();malformed.w.close();
 console.log('Persistent layout and last-used view restore view/order/size/position/expansion, saves changes, and tolerates stale or invalid data.');
})();
