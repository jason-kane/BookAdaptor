const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function node() {
  const listeners = {};
  const classes = new Set();
  return {dataset: {}, attributes: {}, styles: {}, listeners, classes,
    style: {getPropertyValue(name) {return this[name] || '';},setProperty(name,value) {this[name]=String(value);},removeProperty(name) {delete this[name];}},
    classList: {add(name) {classes.add(name);},remove(name) {classes.delete(name);}},
    setAttribute(name,value) {this.attributes[name]=value;},
    addEventListener(name,handler) {listeners[name]=handler;},
    remove() {this.removed=true;}
  };
}
function fire(target,name,data={}) {
  const event={button:0,pointerId:1,clientX:0,clientY:0,preventDefault(){},stopPropagation(){},...data};
  target.listeners[name](event);
}
function setup() {
  const panel=node(); panel.dataset.expanded='false';
  const toggle=node(); const group={active:'open'};
  panel.querySelector=selector=>selector==='[data-panel-expand]'?toggle:group;
  panel.getBoundingClientRect=()=>({left:100,top:100,right:400,bottom:228,width:300,height:128});
  const handles=Object.fromEntries(['nw','ne','sw','se','w','e','n','s'].map(corner=>{
    const handle=node(); handle.dataset.resizeCorner=corner;
    handle.closest=()=>panel;
    handle.setPointerCapture=id=>{handle.captured=id;};
    handle.hasPointerCapture=id=>handle.captured===id;
    handle.releasePointerCapture=()=>{handle.captured=null;};
    return [corner,handle];
  }));
  const grid=node(); grid.dataset.view='array';
  grid.querySelectorAll=selector=>selector==='.service-panel'?[panel]:Object.values(handles);
  const root=node(); const ghosts=[];
  let columns=3; let observe;
  class Observer {constructor(fn){observe=fn;} observe(){}}
  vm.runInNewContext(fs.readFileSync('home/static/js/panel-resize.js','utf8'),{
    document:{getElementById:()=>grid,documentElement:root,createElement:()=>node(),
      body:{append(ghost){ghosts.push(ghost);}},addEventListener(){}},
    window:{ResizeObserver:Observer,addEventListener(){}},ResizeObserver:Observer,
    getComputedStyle:()=>({gridTemplateColumns:Array(columns).fill('300px').join(' '),gridAutoRows:'128px',columnGap:'24px',rowGap:'24px'})
  });
  return {panel,toggle,group,handles,grid,root,ghosts,resizeColumns(value){columns=value;observe();}};
}

test('bottom-right drag previews and commits whole column and row spans',()=>{
  const s=setup();
  fire(s.handles.se,'pointerdown',{clientX:400,clientY:228});
  fire(s.handles.se,'pointermove',{clientX:724,clientY:532});
  assert.equal(s.ghosts[0].textContent,'2 × 3 grid slots');
  assert.equal(s.ghosts[0].style.width,'624px');
  assert.equal(s.ghosts[0].style.height,'432px');
  assert.equal(s.panel.dataset.expanded,'false'); // Commit only on release.
  fire(s.handles.se,'pointerup');
  assert.equal(s.panel.style['--panel-columns'],'2');
  assert.equal(s.panel.style['--panel-rows'],'3');
  assert.equal(s.panel.dataset.expanded,'true');
  assert.equal(s.toggle.attributes['aria-expanded'],'true');
  assert.ok(s.ghosts[0].removed);
  assert.ok(!s.root.classes.has('panel-drag-resizing'));
});

test('all four corners grow outward along both axes',()=>{
  for(const [corner,x,y] of [['nw',-224,-204],['ne',724,-204],['sw',-224,532],['se',724,532]]){
    const s=setup(); const handle=s.handles[corner];
    const west=corner.includes('w'),north=corner.includes('n');
    fire(handle,'pointerdown',{clientX:west?100:400,clientY:north?100:228});
    fire(handle,'pointermove',{clientX:x,clientY:y});
    assert.equal(s.ghosts[0].textContent,'2 × 3 grid slots',corner);
    fire(handle,'pointerup');
    assert.equal(s.panel.style['--panel-columns'],'2',corner);
    assert.equal(s.panel.style['--panel-rows'],'3',corner);
  }
});

test('side handles change only their own axis despite diagonal pointer movement',()=>{
  for(const [edge,x,y,columns,rows,cursor] of [
    ['w',-324,600,'2',undefined,'ew-resize'],
    ['e',324,600,'2',undefined,'ew-resize'],
    ['n',600,-304,'1','3','ns-resize'],
    ['s',600,304,'1','3','ns-resize']
  ]){
    const s=setup();
    fire(s.handles[edge],'pointerdown');
    assert.equal(s.root.style['--panel-resize-cursor'],cursor);
    fire(s.handles[edge],'pointermove',{clientX:x,clientY:y});
    fire(s.handles[edge],'pointerup');
    assert.equal(s.panel.style['--panel-columns'],columns,edge);
    assert.equal(s.panel.style['--panel-rows'],rows,edge);
  }
});

test('side handle keyboard controls ignore the other axis and cancellation preserves size',()=>{
  const s=setup();
  fire(s.handles.e,'keydown',{key:'ArrowDown'});
  assert.equal(s.panel.dataset.resizedRows,undefined);
  fire(s.handles.e,'keydown',{key:'ArrowRight'});
  assert.equal(s.panel.style['--panel-columns'],'2');
  fire(s.handles.s,'keydown',{key:'ArrowDown'});
  assert.equal(s.panel.style['--panel-rows'],'2');
  fire(s.handles.w,'pointerdown');
  fire(s.handles.w,'pointermove',{clientX:-600,clientY:600});
  fire(s.handles.w,'pointercancel');
  assert.equal(s.panel.style['--panel-columns'],'2');
  assert.equal(s.panel.style['--panel-rows'],'2');
});

test('Escape and pointer cancellation discard the preview',()=>{
  for(const cancel of ['Escape','pointercancel']){
    const s=setup();
    fire(s.handles.se,'pointerdown');
    fire(s.handles.se,'pointermove',{clientX:500,clientY:500});
    if(cancel==='Escape')fire(s.handles.se,'keydown',{key:'Escape'});
    else fire(s.handles.se,'pointercancel');
    assert.equal(s.panel.dataset.resizedColumns,undefined);
    assert.equal(s.panel.dataset.expanded,'false');
    assert.ok(s.ghosts[0].removed);
    assert.equal(s.handles.se.captured,null);
  }
});

test('keyboard resizing clamps to available columns and preserves the default expansion after shrinking',()=>{
  const s=setup();
  for(let i=0;i<5;i++)fire(s.handles.se,'keydown',{key:'ArrowRight'});
  assert.equal(s.panel.style['--panel-columns'],'3');
  fire(s.handles.se,'keydown',{key:'ArrowDown'});
  assert.equal(s.panel.style['--panel-rows'],'2');
  fire(s.handles.se,'keydown',{key:'ArrowUp'});
  assert.equal(s.panel.dataset.expanded,'false');
  assert.equal(s.panel.style['--panel-rows'],undefined);
  s.resizeColumns(1);
  assert.equal(s.panel.style['--panel-columns'],'1');
  s.resizeColumns(3);
  assert.equal(s.panel.style['--panel-columns'],'3');
});

test('resizing and narrowing the viewport keep positioned panels within the grid',()=>{
  const s=setup();
  s.panel.style.setProperty('--panel-start-column',3);
  fire(s.handles.se,'keydown',{key:'ArrowRight'});
  assert.equal(s.panel.style['--panel-start-column'],'2');
  s.resizeColumns(1);
  assert.equal(s.panel.style['--panel-start-column'],'1');
});
