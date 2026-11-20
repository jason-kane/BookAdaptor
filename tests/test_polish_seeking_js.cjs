const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('home/static/js/polish.js','utf8');
const model = {id:'ollama:example',label:'Ollama · example'};
const settle = () => new Promise(resolve => setImmediate(resolve));

function setup(handler) {
  const dom = new JSDOM(`<button data-goals-toggle aria-expanded="false">Show Goals</button>
    <section id="polish-goals" hidden><button data-model-refresh>Refresh models</button>
    <span data-model-message></span><span data-goal-count>(1)</span><ol data-goals-list>
    <li data-goal-id="G001"><h3>First goal</h3></li></ol></section>`, {runScripts:'outside-only',url:'http://localhost/'});
  dom.window.setTimeout = () => 1;
  dom.window.clearTimeout = () => {};
  dom.window.fetch = async (url,options={}) => {
    const body = await handler(url, options);
    return {ok:true,json:async () => body};
  };
  dom.window.eval(source);
  return dom.window;
}

async function choose(window) {
  const doc = window.document;
  doc.querySelector('[data-goals-toggle]').click();
  await settle();
  const select = doc.querySelector('[data-goal-model]');
  select.value = model.id;
  select.dispatchEvent(new window.Event('change',{bubbles:true}));
  return select;
}

test('Each goal selects a model and starts a durable job that links to its new entry',async () => {
  let job;
  const window = setup(async (url,options) => {
    if (url==='/polish/models/refresh') return {refreshing:true};
    if (url==='/polish/models') return {models:[model],errors:[]};
    if (url==='/polish/jobs') return {jobs:job ? [job] : []};
    assert.equal(url,'/polish/goals/G001/improvements');
    const input = JSON.parse(options.body);
    assert.equal(input.model,model.id);
    assert.match(input.request_key,/^[a-f0-9-]{36}$/);
    job={id:'job',goal_id:'G001',model_id:model.id,model:model.label,status:'running',message:'Working',entry_id:null};
    return {job};
  });
  const select = await choose(window);
  const button = window.document.querySelector('[data-goal-seek]');
  assert.equal(button.disabled,false);
  button.click();
  await settle();
  assert.equal(button.disabled,true);
  assert.equal(select.disabled,true);
  assert.match(window.document.querySelector('[data-goal-job-status]').textContent,/Working/);
  job={...job,status:'completed',message:'New Polish entry ready for review.',entry_id:42};
  await window.eval('pollGoalJobs()');
  assert.equal(button.disabled,false);
  assert.equal(window.document.querySelector('[data-goal-job-status] a').getAttribute('href'),'?new_entry=42#entry-42');
});

test('Unknown network outcomes reuse the request key when the user retries',async () => {
  const keys=[];
  const window=setup(async (url,options) => {
    if(url==='/polish/models/refresh') return {refreshing:true};
    if(url==='/polish/models') return {models:[model],errors:[]};
    if(url==='/polish/jobs') return {jobs:[]};
    keys.push(JSON.parse(options.body).request_key);
    throw Error('Connection dropped');
  });
  await choose(window);
  const button=window.document.querySelector('[data-goal-seek]');
  button.click();
  await settle();
  assert.equal(button.disabled,false);
  assert.match(window.document.querySelector('[data-goal-job-status]').textContent,/Connection dropped/);
  button.click();
  await settle();
  assert.equal(keys.length,2);
  assert.equal(keys[0],keys[1]);
});

test('Reloaded active jobs restore their model and disable duplicate dispatch',async () => {
  const window=setup(async url => {
    if(url==='/polish/models/refresh') return {refreshing:true};
    if(url==='/polish/models') return {models:[model],errors:[]};
    return {jobs:[{id:'job',goal_id:'G001',model_id:model.id,model:model.label,status:'queued',message:'Queued.'}]};
  });
  window.document.querySelector('[data-goals-toggle]').click();
  await settle();
  assert.equal(window.document.querySelector('[data-goal-model]').value,model.id);
  assert.equal(window.document.querySelector('[data-goal-seek]').disabled,true);
  assert.match(window.document.querySelector('[data-goal-job-status]').textContent,/Queued/);
});

function setupRub(handler) {
  const dom=new JSDOM(`<section><select data-rub-model><option value="">Choose a model</option></select>
    <button data-rub-start disabled>Rub one out</button><button data-six-start disabled>Six pack</button>
    <p data-model-message></p><p data-rub-status></p><div data-six-status></div></section>`,
    {runScripts:'outside-only',url:'http://localhost/'});
  dom.window.setTimeout=()=>1;
  dom.window.clearTimeout=()=>{};
  dom.window.fetch=async (url,options={})=>{
    const body=await handler(url,options);
    return {ok:!body.http_status || body.http_status<400,status:body.http_status||200,json:async()=>body};
  };
  dom.window.eval(source);
  return dom.window;
}

test('Rub one out loads models without opening Goals, dispatches and displays selection and progress',async()=>{
  let job;
  const window=setupRub(async (url,options)=>{
    if(url==='/polish/models') return {models:[model],errors:[],updated_at:1};
    if(url==='/polish/jobs') return {jobs:job?[job]:[],automatic_job:job};
    assert.equal(url,'/polish/rub-one-out');
    assert.equal(JSON.parse(options.body).model,model.id);
    job={id:'auto',goal_id:'G057',goal_title:'Condense goals',model:model.label,model_id:model.id,
      selection:{strategy:'lowest_score',score:-2.5,tie_count:1},status:'queued',message:'Queued.',created_at:1};
    return {job};
  });
  await settle();
  const select=window.document.querySelector('[data-rub-model]');
  const button=window.document.querySelector('[data-rub-start]');
  select.value=model.id; select.dispatchEvent(new window.Event('change'));
  assert.equal(button.disabled,false);
  button.click(); await settle();
  assert.equal(button.disabled,true);
  assert.match(window.document.querySelector('[data-rub-status]').textContent,/G057.*score -2.5/);
  job={...job,status:'completed',entry_id:57,message:'Ready.'};
  await window.eval('pollGoalJobs()');
  assert.equal(button.disabled,false);
  assert.equal(window.document.querySelector('[data-rub-status] a').getAttribute('href'),'?new_entry=57#entry-57');
});

test('Rub one out restores its latest active job across reloads',async()=>{
  const job={id:'auto',goal_id:'G100',goal_title:'Another goal',model:model.label,model_id:model.id,
    selection:{strategy:'lowest_score',score:0,tie_count:5},status:'running',message:'Seeking.',created_at:1};
  const window=setupRub(async url=>url==='/polish/models'?{models:[model],errors:[],updated_at:1}:{jobs:[job],automatic_job:job});
  await settle();
  assert.equal(window.document.querySelector('[data-rub-start]').disabled,true);
  assert.equal(window.document.querySelector('[data-rub-model]').value,model.id);
  assert.match(window.document.querySelector('[data-rub-status]').textContent,/Another goal/);
});

test('Rub one out submission failures remain retryable with the same key for unknown outcomes',async()=>{
  const keys=[];
  const window=setupRub(async (url,options)=>{
    if(url==='/polish/models') return {models:[model],errors:[],updated_at:1};
    if(url==='/polish/jobs') return {jobs:[]};
    keys.push(JSON.parse(options.body).request_key);
    throw Error('Connection lost');
  });
  await settle();
  const select=window.document.querySelector('[data-rub-model]');
  select.value=model.id; select.dispatchEvent(new window.Event('change'));
  const button=window.document.querySelector('[data-rub-start]');
  button.click(); await settle();
  assert.equal(button.disabled,false);
  assert.match(window.document.querySelector('[data-rub-status]').textContent,/Connection lost/);
  button.click(); await settle();
  assert.equal(keys.length,2);
  assert.equal(keys[0],keys[1]);
});

test('Six pack submits once, shows varied goals and each review link',async()=>{
  let pack;
  const window=setupRub(async (url,options)=>{
    if(url==='/polish/models') return {models:[model],errors:[],updated_at:1};
    if(url==='/polish/jobs') return {jobs:[],six_pack:pack};
    assert.equal(url,'/polish/six-pack');
    const request=JSON.parse(options.body);
    assert.equal(request.model,model.id);
    pack={batch_id:'batch',requested:6,created:6,completed:0,status:'running',jobs:[
      {goal_id:'G001',goal_title:'One goal',message:'Queued.',status:'queued'},
      {goal_id:'G002',goal_title:'Another goal',message:'Done.',status:'completed',entry_id:42}
    ]};
    return {six_pack:pack};
  });
  await settle();
  const select=window.document.querySelector('[data-rub-model]');
  select.value=model.id; select.dispatchEvent(new window.Event('change'));
  const button=window.document.querySelector('[data-six-start]');
  assert.equal(button.disabled,false);
  button.click(); await settle();
  assert.equal(button.disabled,true,window.document.querySelector('[data-six-status]').textContent);
  assert.match(window.document.querySelector('[data-six-status]').textContent,/0 of 6/);
  assert.match(window.document.querySelector('[data-six-status]').textContent,/G001.*One goal/);
  assert.equal(window.document.querySelector('[data-six-status] a').getAttribute('href'),'?new_entry=42#entry-42');
});
