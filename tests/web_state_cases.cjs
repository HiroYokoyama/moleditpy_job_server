// Exercise the shipped browser logic with delayed responses; no browser or npm dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto, randomUUID, createHash} = require('node:crypto');

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
async function application(fetch, storage=new Map()) {
  const elements = new Map(), events={};
  // The browser's history, enough to follow Back and Forward through hash addresses.
  const location={origin:'http://127.0.0.1:8766',hash:''};
  const history={state:null,entries:[{state:null,hash:''}],index:0,
    pushState(state,_title,hash){this.entries.splice(this.index+1);this.entries.push({state,hash});this.index++;this.state=state;location.hash=hash;},
    replaceState(state,_title,hash){this.entries[this.index]={state,hash};this.state=state;location.hash=hash;},
    go(step){const target=this.index+step;if(target<0||target>=this.entries.length)return;this.index=target;({state:this.state,hash:location.hash}=this.entries[target]);events.popstate?.();},
    back(){this.go(-1);},forward(){this.go(1);}};
  // Like the page: the running and waiting cards have no dial.
  const absent=new Set(['#dial-running','#dial-pending','#dial-text-running','#dial-text-pending']);
  const get = name => {
    if (absent.has(name)) return null;
    if (!elements.has(name)) elements.set(name, element());
    return elements.get(name);
  };
  const context = vm.createContext({
    TextEncoder, Uint8Array, ArrayBuffer, Blob, URL, Date, console, AbortController,
    btoa: value=>Buffer.from(value,'binary').toString('base64'),
    crypto:{randomUUID, subtle:{sign:async()=>new Uint8Array(64), digest:webcrypto.subtle.digest.bind(webcrypto.subtle)}},
    document:{querySelector:get, querySelectorAll:()=>[], createElement:element,body:element(),addEventListener(name,callback){events[name]=callback;}},
    sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},
    location, history,
    setInterval(){}, setTimeout(){},clearTimeout(){},addEventListener(name,callback){events[name]=callback;},
    fetch:async(url, options)=>url==='/api/v1/health' ? response({server_version:'synthetic'}) : fetch(url, options),
  });
  context.window=context;
  const path=require('node:path');
  vm.runInContext(fs.readFileSync(path.join(path.dirname(process.argv[2]),'sha256.min.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  await new Promise(done=>setImmediate(done));
  const state = vm.runInContext('state', context);
  return {context, state, get, events, storage, call:name=>vm.runInContext(name, context)};
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
async function userChip() {
  const app=await application(async url=>{
    if(url==='/api/v1/me')return response({username:'alice',role:'user'});
    if(url==='/api/v1/capabilities')return response({});
    if(url==='/api/v1/auth/logout')return response({signed_out:true});
    return response(url.startsWith('/api/v1/jobs?')?[]:{running:[],waiting:[],paused:false});
  });
  app.state.token='token';app.state.expires=Date.now()/1000+3600;app.state.authenticated=Date.now()/1000;
  await app.call('openSession')();
  assert.equal(app.get('#signin').hidden,true,'Sign in is not relabelled as the user once signed in');
  assert.equal(app.get('#user-chip').hidden,false);
  assert.equal(app.get('#user-name').textContent,'alice');
  assert.equal(app.get('#user-role').textContent,'User');
  assert.equal(app.get('#user-initial').textContent,'A');
  assert.equal(app.get('#signout').hidden,false);
  app.get('#signout').listeners.click();
  assert.equal(app.get('#user-chip').hidden,true);
  assert.equal(app.get('#signin').hidden,false);
}
async function refresh() {
  const entered=deferred(), reply=deferred();
  const app=await application(async url=>{
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
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
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
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
  assert.match(app.get('#login-status').textContent,/Settings → Web access URLs/);
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
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url==='/api/v1/system/resources')return response({});
    if(url.startsWith('/api/v1/jobs?'))return response([]);
    if(url==='/api/v1/auth/logout')return response({signed_out:true});
    throw new Error('Unexpected URL '+url);
  });
  await app.call('login')('alice','x');
  assert.equal(app.state.token,'web-token');
  assert.equal(app.context.document.body.classList.contains('signed-out'),false);
  app.call('signOut')();
  assert.equal(app.state.token,null);
  assert.equal(app.state.me,null);
  assert.ok(calls.some(([url])=>url==='/api/v1/auth/logout'));
  assert.ok(calls.every(([url])=>!url.includes('challenge')&&!url.includes('/auth/devices')));
}
async function queueControls() {
  const calls=[];let priority=0,forced=false;
  const app=await application(async(url,options)=>{
    if(url==='/api/v1/jobs/pending')return response({...job('pending'),state:'PENDING',display_state:'PENDING',priority,force_run:forced});
    if(url.includes('/log?'))return response({text:'',next_offset:0});
    if(url==='/api/v1/admin/jobs/pending/priority'){priority=JSON.parse(options.body).priority;calls.push(url);return response({});}
    if(url==='/api/v1/admin/jobs/pending/force-run'){forced=JSON.parse(options.body).force_run;calls.push(url);return response({});}
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);app.state.me.role='admin';
  await app.call('detail')('pending');
  assert.equal(app.get('#job-controls').hidden,false);
  app.get('#job-priority').value='25';await app.call('queueControl')('priority');
  assert.equal(priority,25);
  app.get('#job-priority').value='1.5';await assert.rejects(()=>app.call('queueControl')('priority'),/integer/);
  const confirms=[];app.context.confirm=text=>{confirms.push(text);return true;};
  await app.call('queueControl')('force');assert.equal(forced,true);
  assert.match(confirms[0],/CPU\/memory budgets/);
  assert.match(confirms[0],/Dependency conditions/);
  await app.call('queueControl')('force');assert.equal(forced,false);
  assert.equal(confirms.length,1,'Cancelling force does not ask to overcommit');
  app.state.me.role='user';const count=calls.length;
  await app.call('queueControl')('force');assert.equal(calls.length,count);
  await app.call('detail')('pending');assert.equal(app.get('#job-controls').hidden,true);
}
async function expiredDownload() {
  const app=await application(async url=>{throw new Error('Unexpected URL '+url);});
  signedIn(app);app.state.expires=0;
  await app.call('safe')(()=>app.call('download')('job',{}));
  assert.equal(app.state.token,null);
  assert.equal(app.state.me,null);
  assert.match(app.get('#login-status').textContent,/Sign in again/);
}
async function sessionRestore() {
  const storage=new Map();
  const saved={token:'saved-token',expires:Date.now()/1000+900,authenticated:Date.now()/1000};
  storage.set('job-server-web-session',JSON.stringify(saved));
  let passwordRequests=0;
  const restored=await application(async(url,options)=>{
    if(url==='/api/v1/auth/web-login')passwordRequests++;
    assert.equal(options.headers.Authorization,'Bearer saved-token');
    if(url==='/api/v1/me')return response({username:'alice',role:'user'});
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url==='/api/v1/capabilities'||url==='/api/v1/system/resources')return response({});
    if(url.startsWith('/api/v1/jobs?'))return response([]);
    if(url==='/api/v1/auth/logout')return response({});
    throw new Error('Unexpected URL '+url);
  },storage);
  assert.equal(restored.state.me.username,'alice');assert.equal(passwordRequests,0);
  assert.equal(restored.context.document.body.classList.contains('signed-out'),false);
  const rejected=await application(async()=>({ok:false,status:401,json:async()=>({message:'Revoked'})}),new Map(storage));
  assert.equal(rejected.state.token,null);assert.equal(rejected.state.me,null);
  assert.equal(rejected.storage.has('job-server-web-session'),false);
  restored.call('signOut')();assert.equal(storage.has('job-server-web-session'),false);
  const expired=new Map([['job-server-web-session',JSON.stringify({...saved,expires:1})]]);
  const signedOut=await application(async()=>{throw new Error('Expired session must not authenticate');},expired);
  assert.equal(signedOut.state.token,null);assert.equal(expired.size,0);
}
async function submittedAndCompleted() {
  let status='PENDING';
  const app=await application(async(url)=>{
    if(url==='/api/v1/jobs')return response({id:'submitted'});
    if(url.endsWith('/uploads'))return response([]);
    if(url.endsWith('/commit'))return response({});
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url==='/api/v1/system/resources')return response({running:status==='DONE'?0:1,pending:0});
    const record={...job('submitted'),state:status,display_state:status};
    if(url.startsWith('/api/v1/jobs?'))return response([record]);
    if(url==='/api/v1/jobs/submitted')return response(record);
    if(url.includes('/log?'))return response({text:'',next_offset:0});
    if(url.endsWith('/files'))return response([]);
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);app.state.page=3;
  const field=value=>({value});
  const form={querySelector:()=>({disabled:false}),elements:{files:{files:[]},name:field('submitted'),command:field('echo synthetic'),cpus:field('1'),memory:field('0.5'),walltime:field('60'),after:field(''),chain_any:{checked:false},globs:field('*.out')}};
  await app.get('#submit-form').listeners.submit({preventDefault(){},currentTarget:form});
  assert.equal(app.state.page,0);assert.equal(app.state.job,'submitted');
  assert.equal(app.get('#jobs-body').children[0].children[1].children[0].textContent,'PENDING');
  status='DONE';await app.call('refresh')();
  assert.equal(app.get('#jobs-body').children[0].children[1].children[0].textContent,'DONE');
  assert.equal(app.get('#running').textContent,0);
  assert.match(app.get('#detail-name').textContent,/DONE/);
  app.call('panel')('overview');await new Promise(done=>setImmediate(done));
  assert.equal(app.state.job,null);
}
async function queuedRefresh() {
  const entered=deferred(),late=deferred();let requests=0;
  const app=await application(async url=>{
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url==='/api/v1/system/resources')return response({});
    assert.ok(url.startsWith('/api/v1/jobs?'));
    if(++requests===1){entered.resolve();return late.promise;}
    return response([{...job('updated'),state:'DONE',display_state:'DONE'}]);
  });
  signedIn(app);const first=app.call('refresh')();await entered.promise;
  await app.call('refresh')();late.resolve(response([job('old')]));await first;
  await new Promise(done=>setImmediate(done));
  assert.equal(requests,2);
  assert.equal(app.get('#jobs-body').children[0].children[1].children[0].textContent,'DONE');
}
async function insecureContext() {
  const app=await application(async()=>response({}));
  app.context.crypto={getRandomValues:webcrypto.getRandomValues.bind(webcrypto)};
  for(const length of [0,1,55,56,63,64,65,1000,1000000]){
    const value=new Uint8Array(length).fill(97);
    assert.equal(await app.call('digest')(value.buffer),createHash('sha256').update(value).digest('hex'));
  }
  const identifiers=new Set();
  for(let index=0;index<100;index++){
    const id=app.call('randomId')();
    assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    identifiers.add(id);
  }
  assert.equal(identifiers.size,100);
  app.context.crypto={};
  assert.throws(()=>app.call('randomId')(),/Secure random generation is unavailable/);
}
async function sharedQueue() {
  const other={job_id:null,owner:'bob',mine:false,cpus:2,memory_mb:1024,priority:0};
  const app=await application(async url=>{
    if(url==='/api/v1/queue')return response({paused:false,
      running:[{...other,name:'bob running',started_at:1}],
      waiting:[{...other,name:'bob waiting',position:1,display_state:'QUEUED',force_run:false},
        {job_id:'mine',owner:'alice',mine:true,name:'my calculation',cpus:1,memory_mb:512,priority:3,position:2,display_state:'QUEUED',force_run:false}]});
    if(url==='/api/v1/system/resources')return response({running:1,pending:2});
    if(url.startsWith('/api/v1/jobs?'))return response([{...job('mine'),state:'PENDING',display_state:'QUEUED',queue_ahead:1}]);
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);await app.call('refresh')();
  assert.equal(app.get('#queue-summary').textContent,'1 running · 2 waiting · your next job: 1 ahead');
  const rows=app.get('#queue-body').children;
  assert.equal(rows.length,3);
  assert.deepEqual(rows.map(row=>row.children[1].textContent),['bob running','bob waiting','my calculation']);
  assert.equal(rows[2].className,'mine');assert.equal(rows[2].children[2].textContent,'alice (you)');
  // Only one's own job has an Open button.
  assert.equal(rows[1].children[5].children.length,0);assert.equal(rows[2].children[5].children.length,1);
  assert.match(app.get('#jobs-body').children[0].children[1].children[1].textContent,/1 ahead$/);
}
const JOB_A='00000000-0000-0000-0000-00000000000a', JOB_B='00000000-0000-0000-0000-00000000000b';
function steadyServer(records,files={}) {
  return async url=>{
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url==='/api/v1/system/resources')return response({running:0,pending:0});
    if(url.startsWith('/api/v1/jobs?'))return response(records());
    const found=records().find(record=>url==='/api/v1/jobs/'+record.id);
    if(found)return response(found);
    if(url.includes('/log?'))return response({text:'',next_offset:0});
    for(const [id,list] of Object.entries(files))if(url==='/api/v1/jobs/'+id+'/files')return response(list);
    if(url==='/api/v1/auth/logout')return response({signed_out:true});
    throw new Error('Unexpected URL '+url);
  };
}
async function browserHistory() {
  const app=await application(steadyServer(()=>[job(JOB_A),job(JOB_B)]));
  signedIn(app);
  const history=app.context.history,location=app.context.location;
  app.call('panel')('overview');
  assert.equal(location.hash,'#overview');assert.equal(history.entries.length,1,'The first page replaces the bare address');
  app.call('panel')('submit');assert.equal(location.hash,'#submit');
  await app.call('detail')(JOB_A);assert.equal(location.hash,'#job/'+JOB_A);
  history.back();await new Promise(done=>setImmediate(done));
  assert.equal(location.hash,'#submit');assert.equal(app.state.job,null,'Back leaves the job page');
  history.forward();await new Promise(done=>setTimeout(done,0));
  assert.equal(app.state.job,JOB_A,'Forward opens the same job again');
  assert.equal(history.entries.length,3,'Following history adds no entries');
  // The page's own back button behaves like the browser's.
  app.get('#back').listeners.click();await new Promise(done=>setImmediate(done));
  assert.equal(location.hash,'#submit');
  // Opening a job from an address, as after a reload or a shared link.
  history.pushState(null,'','#job/'+JOB_B);app.call('followRoute')();await new Promise(done=>setTimeout(done,0));
  assert.equal(app.state.job,JOB_B);
  location.hash='#job/not-a-job';app.call('followRoute')();
  assert.equal(app.state.job,null,'A malformed job address shows the overview');
  app.get('#signout').listeners.click();
  assert.equal(location.hash,'#overview','The next person to sign in does not start on this user\'s page');
  history.back();assert.equal(app.state.me,null,'Signed out, history changes nothing');
}
async function steadyLists() {
  let records=[{...job(JOB_A),state:'DONE',display_state:'DONE'}];
  const files={[JOB_A]:[{path:'result.out',size:10,sha256:'0'.repeat(64)}]};
  const app=await application(steadyServer(()=>records,files));
  signedIn(app);
  await app.call('refresh')();
  const row=app.get('#jobs-body').children[0];
  await app.call('refresh')();
  assert.equal(app.get('#jobs-body').children[0],row,'An unchanged list keeps its rows');
  await app.call('detail')(JOB_A);
  const file=app.get('#result-files').children[0];
  assert.equal(file.children[0].textContent,'result.out');
  let emptied=false;const list=app.get('#result-files'),replace=list.replaceChildren.bind(list);
  list.replaceChildren=(...values)=>{if(!values.length)emptied=true;replace(...values);};
  await app.call('refresh')();
  assert.equal(emptied,false,'Result files are never blanked while the job is refreshed');
  assert.equal(app.get('#result-files').children[0],file);
  records=[{...records[0],display_state:'DONE',priority:5}];
  await app.call('refresh')();
  assert.notEqual(app.get('#jobs-body').children[0],row,'A changed job is drawn again');
  // A new log line is appended; an empty poll leaves the text alone.
  const log=app.get('#log');log.textContent='kept';
  await app.call('detail')(JOB_A,false);
  assert.equal(log.textContent,'kept');
}
async function jobRows() {
  const records=[{...job(JOB_A),state:'RUNNING',display_state:'RUNNING'},{...job(JOB_B),state:'FAILED',display_state:'FAILED'}];
  const app=await application(steadyServer(()=>records));
  signedIn(app);await app.call('refresh')();
  const [running,failed]=app.get('#jobs-body').children;
  app.get('#job-state-filter').value='problem';app.get('#job-state-filter').listeners.change();
  assert.equal(running.hidden,true);assert.equal(failed.hidden,false);
  app.get('#job-state-filter').value='active';app.get('#job-state-filter').listeners.change();
  assert.equal(running.hidden,false);assert.equal(failed.hidden,true);
  // A refresh keeps the filter applied.
  records.push({...job('00000000-0000-0000-0000-00000000000c'),state:'DONE',display_state:'DONE'});
  await app.call('refresh')();
  assert.deepEqual(app.get('#jobs-body').children.map(row=>row.hidden),[false,true,true]);
  running.listeners.click({target:{closest:()=>null}});await new Promise(done=>setTimeout(done,0));
  assert.equal(app.state.job,JOB_A,'Clicking a row opens its job');
  assert.equal(app.context.document.title,'Job details · Job Server');
}
async function overviewMeters() {
  const resources={running:1,pending:2,reserved_cpus:6,cpus:8,reserved_memory_mb:512,memory_mb:1024,cpu_percent:93,memory_percent:40};
  const now=Math.floor(Date.now()/1000),asked=[];
  let history=[[now-100,4,0,10,20,30,40],[now-10,1,2,93,40,75,50],[now,1,2,93,40,75,50]];
  const app=await application(async url=>{
    if(url==='/api/v1/system/resources')return response(resources);
    if(url.startsWith('/api/v1/system/resources/history?since=')){
      const since=Number(url.split('=')[1]);asked.push(since);
      if(!history)throw new Error('no history');
      return response({interval:10,keep_seconds:21600,samples:history.filter(row=>row[0]>since)});
    }
    if(url==='/api/v1/queue')return response({running:[],waiting:[],paused:false});
    if(url.startsWith('/api/v1/jobs?'))return response([]);
    throw new Error('Unexpected URL '+url);
  });
  signedIn(app);await app.call('refresh')();
  const state=app.call('trendState');
  assert.ok(asked[0]>now-301&&asked[0]<=now,'The first request asks only for the five minutes shown');
  assert.equal(state.rows.length,3);
  assert.equal(app.get('#dial-text-cpu').textContent,'75%');
  assert.equal(app.get('#dial-text-cpu-now').textContent,'93%');
  const points=app.call('trendPoints')('running',100,30,now);
  assert.equal(points.length,4,'A silence longer than three samples breaks the line');
  assert.equal(points[1].x,null);assert.equal(Math.round(points[3].x),100);
  assert.ok(Math.abs(points[0].y-7.2)<1e-9,'The largest count shown stays below the top line');
  assert.equal(app.call('trendTop')('running'),5);assert.equal(app.call('trendTop')('cpu'),100);
  await app.call('refresh')();
  assert.equal(asked[1],now,'Later requests ask only for what is new');
  assert.equal(state.rows.length,3,'Samples already held are not added twice');
  history=null;await app.call('refresh')();
  assert.equal(app.get('#dial-text-cpu').textContent,'75%','A server without history still shows its figures');
  app.get('#signout').listeners.click();
  assert.equal(state.rows.length,0,'Signing out forgets the readings');
  assert.match(app.call('dialArc')(0.5),/^M9\.98 34\.02A17 17 0 0 1 22\.00 5\.00$/);
}
const cases={overviewMeters,jobRows,browserHistory,steadyLists,userChip,insecureContext,sessionRestore,submittedAndCompleted,queuedRefresh,queueControls,expiredDownload,keylessLogin,authentication,refresh,details,accountsPolling,accountsOrdering,loginView,connectionFailure,connectionTimeout,sharedQueue};
async function runCases() {
  const selected=process.argv[3]==='all'?Object.keys(cases):[process.argv[3]];
  for(const name of selected){
    console.log('RUN '+name);
    let timer;
    try {
      await Promise.race([
        cases[name](),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Scenario timed out: '+name)),15000);}),
      ]);
    } finally {clearTimeout(timer);}
    console.log('PASS '+name);
  }
}
runCases().catch(error=>{console.error(error);process.exitCode=1;});
