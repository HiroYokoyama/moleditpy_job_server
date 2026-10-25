// Exercise the shipped browser logic with delayed responses; no browser or npm dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto, randomUUID} = require('node:crypto');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function response(data) {
  return {ok:true, status:200, headers:{get:()=> '1'}, json:async()=>data};
}
function element() {
  return {
    textContent:'', hidden:false, disabled:false, open:false, dataset:{}, children:[], listeners:{},
    classList:{add(){}, toggle(){}},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(value) { this.children.push(value); },
    replaceChildren(...values) { this.children = values; this.textContent = ''; },
    querySelector() { return element(); },
  };
}
async function application(fetch) {
  const elements = new Map();
  const get = name => {
    if (!elements.has(name)) elements.set(name, element());
    return elements.get(name);
  };
  const context = vm.createContext({
    TextEncoder, Uint8Array, ArrayBuffer, Blob, URL, Date, console,
    btoa: value=>Buffer.from(value,'binary').toString('base64'),
    crypto:{randomUUID, subtle:{sign:async()=>new Uint8Array(64), digest:webcrypto.subtle.digest.bind(webcrypto.subtle)}},
    document:{querySelector:get, querySelectorAll:()=>[], createElement:element},
    location:{origin:'http://127.0.0.1:8766'},
    setInterval(){}, setTimeout(){},
    fetch:async(url, options)=>url==='/api/v1/health' ? response({server_version:'synthetic'}) : fetch(url, options),
  });
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  await new Promise(done=>setImmediate(done));
  const state = vm.runInContext('state', context);
  return {context, state, get, call:name=>vm.runInContext(name, context)};
}
function signedIn(app) {
  app.state.profile={deviceId:'device-a',serverId:'server',privateKey:{}};
  app.state.me={role:'user',username:'alice',user_id:'alice'};
  app.state.token='existing-token';
  app.state.expires=Date.now()/1000+3600;
}
function job(id) {
  return {id,owner_id:'alice',state:'RUNNING',display_state:'RUNNING',created_at:1,exit_code:null,
    spec:{name:id,command:'echo synthetic',resources:{cpus_per_task:1,memory_mb:512}}};
}
async function authentication() {
  const entered=deferred(), reply=deferred();
  const app=await application(async url=>{
    assert.equal(url,'/api/v1/auth/token'); entered.resolve(); return reply.promise;
  });
  signedIn(app);
  const pending=app.call('authenticate')({server_id:'server',protocol_version:'1',nonce:'synthetic'}).catch(error=>error);
  await entered.promise;
  app.get('#signout').listeners.click();
  reply.resolve(response({token:'late-token',expires_at:Date.now()/1000+3600}));
  await pending;
  assert.equal(app.state.token,null,'Late authentication must not restore a signed-out session');
  assert.equal(app.state.profile,null);
}
async function refresh() {
  const entered=deferred(), reply=deferred();
  const app=await application(async url=>{
    if (url==='/api/v1/system/resources') return response({reserved_cpus:1,cpus:2,reserved_memory_mb:512,memory_mb:1024});
    assert.ok(url.startsWith('/api/v1/jobs?')); entered.resolve(); return reply.promise;
  });
  signedIn(app);
  const pending=app.call('refresh')().catch(error=>error);
  await entered.promise;
  app.get('#signout').listeners.click();
  reply.resolve(response([job('private-old-job')]));
  await pending;
  assert.equal(app.get('#jobs-body').children.length,0,'Signed-out jobs must stay cleared');
}
async function details() {
  const first=deferred(), entered=deferred();
  const app=await application(async url=>{
    if (url==='/api/v1/jobs/first') { entered.resolve(); return first.promise; }
    if (url==='/api/v1/jobs/second') return response(job('second'));
    if (url.includes('/log?')) return response({text:'',next_offset:0});
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);
  const pending=app.call('detail')('first').catch(error=>error);
  await entered.promise;
  await app.call('detail')('second');
  first.resolve(response(job('first')));
  await pending;
  assert.equal(app.state.job,'second','Late details must not replace a newer selection');
  assert.ok(app.get('#detail-name').textContent.startsWith('second'));
}
const cases={authentication,refresh,details};
cases[process.argv[3]]().then(()=>console.log('PASS '+process.argv[3])).catch(error=>{console.error(error);process.exitCode=1;});
