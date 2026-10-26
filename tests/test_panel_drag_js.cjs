const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function element() {
  const classes=new Set();
  return {dataset:{},listeners:{},style:{setProperty(name,value){this[name]=String(value);},removeProperty(name){delete this[name];}},classes,
    classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));}},
    addEventListener(name,fn){this.listeners[name]=fn;},
    setAttribute(){},remove(){this.removed=true;},focus(){this.focused=true;},
    setPointerCapture(id){this.captured=id;},hasPointerCapture(id){return this.captured===id;},releasePointerCapture(){this.captured=null;}
  };
}
function fire(node,event,values={}) {
  node.listeners[event]({button:0,pointerId:1,clientX:10,clientY:10,preventDefault(){},stopPropagation(){},...values});
}
function setup(view='array') {
  const order=[];
  for(const name of ['a','b','c']){
    const panel=element();panel.name=name;panel.header=element();
    panel.dataset.expanded='false';
    panel.getAttribute=()=>name;
    panel.querySelector=()=>panel.header;
    panel.closest=()=>panel;
    panel.getBoundingClientRect=()=>{const x=order.indexOf(panel)*320;return {left:x,right:x+300,top:0,bottom:100,width:300,height:100};};
    function insert(moving,after){order.splice(order.indexOf(moving),1);order.splice(order.indexOf(panel)+(after?1:0),0,moving);}
    panel.before=moving=>insert(moving,false);panel.after=moving=>insert(moving,true);
    order.push(panel);
  }
  const grid=element();grid.dataset.view=view;grid.querySelectorAll=()=>[...order];
  grid.getBoundingClientRect=()=>({left:0,right:940,top:0,bottom:500});
  const root=element(),doc=element(),win=element(),ghosts=[];
  Object.assign(doc,{documentElement:root,getElementById:()=>grid,createElement:()=>element(),
    body:{append(ghost){ghosts.push(ghost);}},
    elementsFromPoint(x,y){return order.filter(panel=>{const r=panel.getBoundingClientRect();return x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom;});}
  });
  vm.runInNewContext(fs.readFileSync('home/static/js/panel-drag.js','utf8'),{document:doc,window:win,getComputedStyle:()=>({gridTemplateColumns:'300px 300px 300px',gridAutoRows:'100px',columnGap:'20px',rowGap:'20px'})});
  return {order,grid,root,doc,ghosts};
}

test('small pointer movement stays a click rather than reordering',()=>{
  const s=setup();const header=s.order[0].header;
  fire(header,'pointerdown');fire(header,'pointermove',{clientX:12,clientY:12});fire(header,'pointerup');
  assert.equal(s.ghosts.length,0);
  assert.equal(header.dataset.suppressPanelClick,undefined);
  assert.deepEqual(s.order.map(panel=>panel.name),['a','b','c']);
});

test('dragging an expanded header moves the original panel without losing its state or size',()=>{
  const s=setup();const panel=s.order[0];panel.dataset.expanded='true';panel.dataset.resizedRows='5';
  fire(panel.header,'pointerdown');fire(panel.header,'pointermove',{clientX:900,clientY:70});
  assert.equal(s.ghosts[0].style.left,'640px');
  assert.equal(s.ghosts[0].style.top,'0px');
  assert.equal(panel.header.dataset.suppressPanelClick,'true');
  fire(panel.header,'pointerup');
  assert.equal(panel.style['--panel-start-column'],'3');
  assert.equal(panel.style['--panel-start-row'],'1');
  assert.equal(panel.dataset.expanded,'true');assert.equal(panel.dataset.resizedRows,'5');
  assert.ok(s.ghosts[0].removed);assert.ok(!s.root.classes.has('panel-drag-reordering'));
});

test('drag shadow locks to the resized border box at drag start',()=>{
  const s=setup();const panel=s.order[0];
  fire(panel.header,'pointerdown');
  // A resized panel can span multiple columns and rows, with fractional pixels.
  panel.getBoundingClientRect=()=>({left:0,right:624.5,top:0,bottom:736.25,width:624.5,height:736.25});
  fire(panel.header,'pointermove',{clientX:330,clientY:250});
  const style=s.ghosts[0].style;
  for(const key of ['width','minWidth','maxWidth']) assert.equal(style[key],'624.5px');
  for(const key of ['height','minHeight','maxHeight']) assert.equal(style[key],'736.25px');
  assert.equal(style.left,'320px');assert.equal(style.top,'240px');
});

test('dropping back on the source or outside the grid leaves the order intact',()=>{
  for(const [x,y] of [[30,30],[1000,600]]){
    const s=setup();const header=s.order[0].header;
    fire(header,'pointerdown');fire(header,'pointermove',{clientX:x,clientY:y});fire(header,'pointerup');
    assert.deepEqual(s.order.map(panel=>panel.name),['a','b','c']);
    assert.equal(s.order[0].style['--panel-start-column'],x===30?'1':undefined);
  }
});

test('Escape cancels a drag and keyboard shortcuts move focused headers',()=>{
  const s=setup();const panel=s.order[0];
  fire(panel.header,'pointerdown');fire(panel.header,'pointermove',{clientX:900,clientY:70});
  fire(s.doc,'keydown',{key:'Escape'});
  assert.deepEqual(s.order.map(panel=>panel.name),['a','b','c']);
  assert.ok(s.ghosts[0].removed);
  fire(panel.header,'keydown',{key:'ArrowRight',altKey:true});
  assert.deepEqual(s.order.map(panel=>panel.name),['b','a','c']);
  assert.ok(panel.header.focused);
});

test('reordering is limited to Array',()=>{
  const s=setup('icons');fire(s.order[0].header,'pointerdown');
  fire(s.order[0].header,'pointermove',{clientX:900,clientY:70});
  assert.equal(s.ghosts.length,0);
});


test('the cursor slot anchors the destination even inside a tall panel or empty space',()=>{
  for(const [x,y,column,row] of [[350,275,'2','3'],[670,490,'3','5'],[340,95,'2','1']]){
    const s=setup();const panel=s.order[0];
    fire(panel.header,'pointerdown',{clientX:200,clientY:20});
    fire(panel.header,'pointermove',{clientX:x,clientY:y});
    fire(panel.header,'pointerup');
    assert.equal(panel.style['--panel-start-column'],column);
    assert.equal(panel.style['--panel-start-row'],row);
  }
});

test('a wide panel cannot create extra columns at the right edge',()=>{
  const s=setup();const panel=s.order[0];
  panel.getBoundingClientRect=()=>({left:0,right:620,top:0,bottom:340,width:620,height:340});
  fire(panel.header,'pointerdown');fire(panel.header,'pointermove',{clientX:900,clientY:250});
  fire(panel.header,'pointerup');
  assert.equal(panel.style['--panel-start-column'],undefined);
});
