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
  const classes=new Set();
  return {
    textContent:'', hidden:false, disabled:false, open:false, dataset:{}, children:[], listeners:{},
    classList:{add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);},toggle(name,value){if(value===undefined)value=!classes.has(name);if(value)classes.add(name);else classes.delete(name);}},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(value) { this.children.push(value); },
    replaceChildren(...values) { this.children = values; this.textContent = ''; },
    querySelector() { return element(); },
    show(){this.open=true;},showModal(){this.open=true;},close(){this.open=false;},
  };
}
async function application(fetch) {
  const elements = new Map(), events={};
  const get = name => {
    if (!elements.has(name)) elements.set(name, element());
    return elements.get(name);
  };
  const context = vm.createContext({
    TextEncoder, Uint8Array, ArrayBuffer, Blob, URL, Date, console, AbortController,
    btoa: value=>Buffer.from(value,'binary').toString('base64'),
    crypto:{randomUUID, subtle:{sign:async()=>new Uint8Array(64), digest:webcrypto.subtle.digest.bind(webcrypto.subtle)}},
    document:{querySelector:get, querySelectorAll:()=>[], createElement:element,body:element()},
    location:{origin:'http://127.0.0.1:8766'},
    setInterval(){}, setTimeout(){},clearTimeout(){},addEventListener(name,callback){events[name]=callback;},
    fetch:async(url, options)=>url==='/api/v1/health' ? response({server_version:'synthetic'}) : fetch(url, options),
  });
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  await new Promise(done=>setImmediate(done));
  const state = vm.runInContext('state', context);
  return {context, state, get, events, call:name=>vm.runInContext(name, context)};
}
function signedIn(app) {
  app.state.me={role:'user',username:'alice',user_id:'alice'};
  app.state.token='existing-token';
  app.state.expires=Date.now()/1000+3600;
  app.state.authenticated=Date.now()/1000;
  app.call('setSignedIn')(true);
}
function job(id) {
  return {id,owner_id:'alice',state:'RUNNING',display_state:'RUNNING',created_at:1,exit_code:null,
    spec:{name:id,command:'echo synthetic',resources:{cpus_per_task:1,memory_mb:512}}};
}
async function authentication() {
  const entered=deferred(), reply=deferred();
  const app=await application(async url=>{
    if(url==='/api/v1/auth/logout')return response({signed_out:true});
    assert.equal(url,'/api/v1/auth/web-login'); entered.resolve(); return reply.promise;
  });
  signedIn(app);
  const pending=app.call('login')('alice','x').catch(error=>error);
  await entered.promise;
  app.get('#signout').listeners.click();
  reply.resolve(response({token:'late-token',expires_at:Date.now()/1000+3600}));
  await pending;
  assert.equal(app.state.token,null,'Late authentication must not restore a signed-out session');
  assert.equal(app.state.me,null);
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
async function accountsPolling() {
  let users=[{id:'alice',username:'alice',role:'admin',enabled:1}],devices=[];
  const app=await application(async url=>{
    if(url==='/api/v1/admin/users')return response(users);
    if(url==='/api/v1/admin/devices')return response(devices);
    if(url==='/api/v1/system/resources')return response({paused:false});
    if(url.startsWith('/api/v1/admin/jobs?'))return response([]);
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);app.state.me.role='admin';app.get('#accounts').hidden=false;
  await app.call('refresh')();
  assert.equal(app.get('#users-body').children.length,1);
  // Another client (the GUI) creates a user/device, then disables/revokes it.
  users=[...users,{id:'bob',username:'bob',role:'user',enabled:1}];
  devices=[{id:'bob-device',user_id:'bob',last_access:1,revoked_at:null}];
  await app.call('refresh')();
  assert.equal(app.get('#users-body').children.length,2);
  assert.equal(app.get('#devices-body').children.length,1);
  users=users.map(u=>u.id==='bob'?{...u,enabled:0}:u);
  devices=devices.map(d=>({...d,revoked_at:2}));
  await app.call('refresh')();
  assert.equal(app.get('#users-body').children[1].children[2].textContent,'Disabled');
  assert.equal(app.get('#devices-body').children[0].children[3].textContent,'Revoked');
}
async function accountsOrdering() {
  const entered=deferred(),late=deferred();let requests=0;
  const user=name=>({id:name,username:name,role:'user',enabled:1});
  const app=await application(async url=>{
    if(url==='/api/v1/admin/users'){
      if(++requests===1){entered.resolve();return late.promise;}
      return response([user('new-user')]);
    }
    if(url==='/api/v1/admin/devices')return response([]);
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);app.state.me.role='admin';
  const pending=app.call('accounts')();await entered.promise;
  await app.call('accounts')();late.resolve(response([user('stale-user')]));await pending;
  assert.equal(app.get('#users-body').children[0].children[0].textContent,'new-user');
}
async function loginView() {
  const app=await application(async url=>{throw new Error('Unexpected URL '+url);});
  assert.ok(app.context.document.body.classList.contains('signed-out'));
  assert.equal(app.get('#login-dialog').open,true);
  assert.equal(app.get('#close-login').hidden,true);
  signedIn(app);
  assert.equal(app.context.document.body.classList.contains('signed-out'),false);
  assert.equal(app.get('#login-dialog').open,false);
  app.call('clearSession')();
  assert.ok(app.context.document.body.classList.contains('signed-out'));
  assert.equal(app.get('#login-dialog').open,true);
}
async function connectionFailure() {
  const app=await application(async url=>{throw new Error('Unexpected URL '+url);});
  app.context.fetch=async()=>({ok:false,status:403,json:async()=>({code:'origin_forbidden',message:'Browser origins are not permitted'})});
  await app.call('safe')(()=>app.call('refresh')());
  assert.equal(app.get('#connection-text').textContent,'Connection failed');
  assert.match(app.get('#login-status').textContent,/Settings → Web address/);
  assert.ok(app.get('#login-status').textContent.includes(app.context.location.origin));
  assert.ok(app.context.document.body.classList.contains('signed-out'));
}
async function connectionTimeout() {
  const app=await application(async url=>{throw new Error('Unexpected URL '+url);});
  app.context.setTimeout=call=>{call();return 1;};
  app.context.fetch=async(url,options)=>{
    assert.equal(options.signal.aborted,true);
    const error=new Error('aborted');error.name='AbortError';throw error;
  };
  await app.call('safe')(()=>app.call('refresh')());
  assert.equal(app.get('#connection-text').textContent,'Connection failed');
  assert.match(app.get('#login-status').textContent,/timed out/);
}
async function keylessLogin() {
  const calls=[];
  const app=await application(async(url,options)=>{
    calls.push([url,options]);
    if(url==='/api/v1/auth/web-login'){
      assert.deepEqual(JSON.parse(options.body),{username:'alice',password:'x'});
      assert.equal(options.credentials,'omit');
      return response({token:'web-token',expires_at:Date.now()/1000+900});
    }
    if(url==='/api/v1/me')return response({username:'alice',role:'user'});
    if(url==='/api/v1/capabilities')return response({});
    if(url==='/api/v1/system/resources')return response({});
    if(url.startsWith('/api/v1/jobs?'))return response([]);
    if(url==='/api/v1/auth/logout')return response({signed_out:true});
    throw new Error('Unexpected URL '+url);
  });
  await app.call('login')('alice','x');
  assert.equal(app.state.token,'web-token');
  assert.equal(app.context.document.body.classList.contains('signed-out'),false);
  const reloaded=await application(async()=>{throw new Error('Unexpected authenticated request');});
  assert.equal(reloaded.state.token,null,'Reloading must require a new password sign-in');
  app.events.pagehide();
  assert.equal(app.state.token,null);
  assert.equal(app.state.me,null);
  assert.ok(calls.some(([url])=>url==='/api/v1/auth/logout'));
  assert.ok(calls.every(([url])=>!url.includes('challenge')&&!url.includes('/auth/devices')));
}
const cases={keylessLogin,authentication,refresh,details,accountsPolling,accountsOrdering,loginView,connectionFailure,connectionTimeout};
cases[process.argv[3]]().then(()=>console.log('PASS '+process.argv[3])).catch(error=>{console.error(error);process.exitCode=1;});
