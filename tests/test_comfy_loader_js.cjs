const assert = require('node:assert/strict');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const html = execFileSync('python', ['-c', `from home.app import create_app
print(create_app({'TESTING': True}).test_client().get('/programs/comfyui?workflow=Aesop%20%232%20%26%20image.json').get_data(as_text=True))`], {encoding:'utf8'});
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
(async () => {
 for (const running of [false,true]) {
  const calls=[];let redirected;let checks=0;
  const status={};
  vm.runInNewContext(script, {
   document:{getElementById:id=>id==='status'?status:{addEventListener(){}}},
   URL, Date, setTimeout,
   window:{location:{href:'http://myhost:8080/programs/comfyui',replace(url){redirected=url;}}},
   fetch:async (url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({ready:running || ++checks>1})};}
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(redirected,status.textContent);
  const result=new URL(redirected);
  assert.equal(result.host,'myhost:8188');
  assert.equal(result.searchParams.get('workflow'),'Aesop #2 & image.json');
  assert.equal(calls.filter(call=>call.options.method==='POST').length,running?0:1);
 }
 console.log('ComfyUI loader starts stopped services, skips ready services, and preserves the workflow destination.');
})();
