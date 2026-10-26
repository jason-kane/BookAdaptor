const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..') + path.sep;
const html = execFileSync(process.env.PYTHON || 'python', ['-c',
  'from home.app import create_app; print(create_app({"TESTING": True}).test_client().get("/").get_data(as_text=True))'
], {cwd: root, encoding: 'utf8'});
const dom = new JSDOM(html, {
  url:'http://localhost:8080/', runScripts:'outside-only', pretendToBeVisual:true
});
const w=dom.window;
let copied='';
let infoCalls=0;
const memberStarts=[];
w.alert=message=>{throw new Error(message);};
Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
const metadata={all:[
  {service:'redis',version:'8.8',state:'running',image:'redis:8.8',ports:'6379'},
  {service:'redisinsight',version:'2.70',state:'exited',image:'redis/redisinsight:2.70',ports:'5540'},
  ...['artifactserver','comfyui','glances','test','nginx'].map(service=>({service,version:'1.2.3',state:'running',image:`${service}:1.2.3`,ports:'8080'}))
]};
w.fetch=async url=>{
 if(url.includes('/members/')){memberStarts.push(url);return {ok:true,json:async()=>({started:true})};}
 return {ok:true,json:async()=>metadata,text:async()=>{infoCalls++;return '<p>Current Docker state</p>';}};
};
for(const file of ['vendor/datatables/jquery-3.7.1.min.js','vendor/datatables/dataTables-2.3.4.min.js',
 'vendor/datatables/colReorder-2.1.0.min.js','js/service-info.js','js/panel-versions.js','js/service-table.js','js/service-members.js']) {
 w.eval(fs.readFileSync(root+'home/static/'+file,'utf8'));
}
const table=w.jQuery('.service-table').DataTable();
const settle=()=>new Promise(resolve=>setTimeout(resolve,30));
(async()=>{
 await settle();
 const stylerLink=w.document.querySelector('[data-panel-id="styles"] [data-member-detail] a');
 assert.equal(stylerLink.href,'http://localhost:8082/styles/');
 const redisBrowser=w.document.querySelector('#redis-contents [data-service-members]');
 assert.equal(redisBrowser.querySelector('[data-member-select]'),null);
 assert.equal(memberStarts.length,0);
 assert.equal(redisBrowser.querySelector('[data-member-detail="redis"]').hidden,false);
 assert.equal(redisBrowser.querySelector('[data-member-detail="redisinsight"]').hidden,false);
 redisBrowser.querySelector('[data-member-start="redis"]').click(); await settle();
 assert.equal(memberStarts[0],'/programs/panels/redis/members/redis/stop');
 assert.equal(redisBrowser.querySelector('[data-member-detail="redis"]').hidden,false);
 assert.match(redisBrowser.querySelector('[data-member-detail="redis"] [data-member-message]').textContent,/Stopped/);
 assert.equal(redisBrowser.querySelector('[data-member-start="redis"]').disabled,false);
 assert.equal(table.rows().count(),9);
 assert.equal(w.document.querySelectorAll('.table-detail').length,0);
 assert.equal(w.document.querySelector('.table-tools').hidden,false);
 const filter=w.document.querySelector('tfoot th[data-label="Name"] input');
 filter.value='Redis'; filter.dispatchEvent(new w.Event('input'));
 assert.equal(table.rows({search:'applied'}).count(),2);
 const info=w.document.querySelector('[data-table-info]');
 info.click(); await settle();
 assert.equal(info.getAttribute('aria-expanded'),'true');
 assert.equal(infoCalls,1);
 assert.ok(w.document.querySelector('.table-detail').textContent.includes('Current Docker state'));
 // Refresh must invalidate sort/search data without losing the Info control.
 w.document.querySelector('.service-table').dispatchEvent(new w.Event('service-table-data'));
 info.click(); assert.equal(info.getAttribute('aria-expanded'),'false');
 table.colReorder.move(0,2);
 assert.deepEqual([...table.columns().header().toArray()].map(h=>h.textContent.trim()).slice(0,3),['Compose service','Version','Name']);
 filter.value='Glances'; filter.dispatchEvent(new w.Event('input'));
 assert.equal(table.rows({search:'applied'}).count(),1);
 filter.value='Redis'; filter.dispatchEvent(new w.Event('input'));
 table.order([[2,'desc']]).draw();
 assert.equal(table.rows({search:'applied',order:'applied'}).nodes()[0].querySelector('th').textContent,'Redis Insight');
 w.document.querySelector('[data-table-copy]').click(); await settle();
 assert.ok(copied.startsWith('Compose service\tVersion\tName'));
 assert.equal(copied.split('\n').length,3);
 assert.ok(copied.includes('redis\t8.8\tRedis'));
 assert.ok(!copied.includes('Close info'));
 let downloaded;
 w.URL.createObjectURL=blob=>{downloaded=blob;return 'blob:test';};
 w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){};
 w.document.querySelector('[data-table-csv]').click();
 const csv=await new Promise((resolve,reject)=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(downloaded);});
 assert.ok(csv.includes('"Compose service","Version","Name"'));
 assert.equal(csv.trim().split('\r\n').length,3);
 w.document.querySelector('[data-table-reset]').click();
 assert.equal(table.rows({search:'applied'}).count(),9);
 table.page.len(3).draw();
 assert.equal(table.rows({page:'current'}).count(),3);
 // Detached pages still retain live data because updates hold references to cells.
 assert.ok(table.rows().nodes().toArray().every(row=>!row.textContent.includes('Checking')));
 console.log('Actual DataTables: sorting, filtering, column reordering, child Info, live invalidation, pagination, TSV copy, CSV export, reset, and service row actions passed');
 dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
