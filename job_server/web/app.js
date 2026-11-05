'use strict';
const $ = selector => document.querySelector(selector);
const state = {token:null, expires:0, authenticated:0,  me:null, capabilities:null, paused:false, job:null, jobInfo:null, queueBusy:false, offset:0, page:0, busy:false, refreshPending:false, generation:0, busyGeneration:-1, detailRequest:0, accountsRequest:0};
const encoder = new TextEncoder();
async function digest(value) {
  if(globalThis.crypto?.subtle)return [...new Uint8Array(await crypto.subtle.digest('SHA-256',value))].map(v=>v.toString(16).padStart(2,'0')).join('');
  return sha256(value);
}
function randomId() {
  if(globalThis.crypto?.randomUUID)return crypto.randomUUID();
  if(!globalThis.crypto?.getRandomValues)throw new Error('Secure random generation is unavailable. Use a supported browser.');
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
  return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
function message(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
// Memory is stored in MB but shown and entered in GB.
function gb(mb) { return typeof mb==='number' ? String(Number((mb/1024).toFixed(1))) : '—'; }
function date(value) { return value ? new Date(value*1000).toLocaleString() : '—'; }
function cell(row,text,className='') { const td = document.createElement('td'); td.textContent=String(text); td.className=className; row.append(td); return td; }
function button(parent,text,call) { const element=document.createElement('button'); element.textContent=text; element.addEventListener('click',()=>safe(call)); parent.append(element); return element; }
async function safe(call) { try { await call(); } catch(error) { if(error.staleSession)return; if(error.status===401&&state.me)clearSession();message(error.message); if($('#login-dialog').open)$('#login-status').textContent=error.message; } }
function setSignedIn(value) {
  document.body.classList.toggle('signed-out',!value);
  $('#close-login').hidden=!value;
  if(value)$('#login-dialog').close();
  else if(!$('#login-dialog').open)$('#login-dialog').show();
}
function connection(text,online=false) {
  $('#connection-text').textContent=text;$('#login-connection').textContent=text;
  $('#connection-dot').classList.toggle('online',online);
}
function requireSession(generation) {
  if(generation!==state.generation){const error=new Error('Session changed');error.staleSession=true;throw error;}
}
const SESSION_KEY='job-server-web-session';
function saveSession() {
  try {sessionStorage.setItem(SESSION_KEY,JSON.stringify({token:state.token,expires:state.expires,authenticated:state.authenticated}));} catch {}
}
function clearSession() {
  try {sessionStorage.removeItem(SESSION_KEY);} catch {}
  state.generation++;state.detailRequest++;state.accountsRequest++;
  Object.assign(state,{token:null,expires:0,authenticated:0,me:null,capabilities:null,job:null,jobInfo:null,queueBusy:false,offset:0,page:0,paused:false,refreshPending:false});
  $('#signin').textContent='Sign in';$('#signout').hidden=true;$('#accounts-nav').hidden=true;$('#pause').hidden=true;
  for(const id of ['jobs-body','users-body','devices-body','result-files'])$('#'+id).replaceChildren();
  for(const id of ['log','detail-name','detail-id','detail-spec'])$('#'+id).textContent='';
  for(const id of ['running','pending','cpu','memory'])$('#'+id).textContent='—';
  $('#job-controls').hidden=true;panel('overview');setSignedIn(false);return state.generation;
}

async function api(method,path,body,options={}) {
  const generation=state.generation;
  if (!options.public && (!state.token || state.expires<Date.now()/1000+10 || (path.startsWith('/admin/') && Date.now()/1000-state.authenticated>240))) throw Object.assign(new Error('Sign in again to continue.'),{status:401});
  requireSession(generation);
  const headers={...options.headers};
  if (!options.public) headers.Authorization='Bearer '+state.token;
  if (options.token) headers.Authorization='Bearer '+options.token;
  let payload;
  if (body instanceof Blob || body instanceof ArrayBuffer) payload=body;
  else if (method==='POST'||method==='PUT') {headers['Content-Type']='application/json';payload=JSON.stringify(body||{});}
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  let response;
  try {
    response=await fetch('/api/v1'+path,{method,headers,body:payload,credentials:'omit',redirect:'error',cache:'no-store',signal:controller.signal});
    requireSession(generation);
    if (!response.ok) {
      const error=await response.json().catch(()=>({message:'Server request failed'}));
      requireSession(generation);
      if (response.status===401&&!options.public) state.token=null;
      const failure=new Error(error.code==='origin_forbidden'
        ? 'Remote access needs setup on the server PC. In Job Server, open Settings → Web access URLs and add '+location.origin+'. Save, then choose Server → Restart.'
        : error.message||'Server request failed');
      failure.status=response.status;failure.code=error.code;throw failure;
    }
    if (response.headers.get('X-API-Version')!=='1') throw new Error('Unsupported server API version');
    const result=await response.json();requireSession(generation);return result;
  } catch(error) {
    if(error.name==='AbortError')throw new Error('Connection timed out. Check the server and Tailscale Serve destination.');
    throw error;
  } finally {clearTimeout(timer);}
}
async function login(username,password) {
  const generation=clearSession();
  $('#login-status').textContent='Signing in…';
  try {
    const session=await api('POST','/auth/web-login',{username,password},{public:true});
    requireSession(generation);
    state.token=session.token;state.expires=session.expires_at;state.authenticated=Date.now()/1000;
    await openSession();
  } catch(error) {if(generation===state.generation)clearSession();throw error;}
}
async function openSession() {
  state.me=await api('GET','/me');state.capabilities=await api('GET','/capabilities');
  $('#signin').textContent=state.me.username;$('#signout').hidden=false;
  const admin=state.me.role==='admin';$('#accounts-nav').hidden=!admin;$('#pause').hidden=!admin;
  $('#login-status').textContent='';setSignedIn(true);
  $('#jobs-caption').textContent=admin?'All jobs on this server.':'Your jobs on this server.';
  saveSession();message('');await refresh();
}
async function restoreSession() {
  let saved;
  try {saved=JSON.parse(sessionStorage.getItem(SESSION_KEY)||'null');} catch {}
  const generation=clearSession();
  if(!saved||typeof saved.token!=='string'||!saved.token||saved.token.length>256||
     !Number.isFinite(saved.expires)||saved.expires<Date.now()/1000+10||
     !Number.isFinite(saved.authenticated)||saved.authenticated>Date.now()/1000+30) {
    await refresh();return;
  }
  state.token=saved.token;state.expires=saved.expires;state.authenticated=saved.authenticated;
  try {await openSession();} catch(error) {if(generation===state.generation)clearSession();throw error;}
}
$('#login-form').addEventListener('submit',event=>{
  event.preventDefault();const form=event.currentTarget,submit=form.querySelector('[type=submit]');
  if(submit.disabled)return;submit.disabled=true;
  const username=form.elements.username.value.trim(),password=form.elements.password.value;
  form.elements.password.value='';
  safe(()=>login(username,password)).finally(()=>{submit.disabled=false;});
});
$('#signin').addEventListener('click',()=>{$('#login-dialog').showModal();$('#login-status').textContent='';});
function cancelLogin(){if($('#login-form').querySelector('[type=submit]').disabled)clearSession();$('#login-form').elements.password.value='';if(state.me)$('#login-dialog').close();}
$('#close-login').addEventListener('click',cancelLogin);
$('#login-dialog').addEventListener('cancel',cancelLogin);
function signOut() {
  const token=state.token;clearSession();
  if(token)fetch('/api/v1/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:'{}',credentials:'omit',redirect:'error',cache:'no-store',keepalive:true}).catch(()=>{});
}
$('#signout').addEventListener('click',signOut);

// Remove credentials stored by older browser clients; never read or reuse them.
try {globalThis.indexedDB?.deleteDatabase('moleditpy-job-server');} catch {}

function panel(name) {
  if (name==='accounts'&&state.me?.role!=='admin') return;
  document.querySelectorAll('.panel').forEach(element=>element.hidden=element.id!==name);
  document.querySelectorAll('.nav').forEach(element=>element.classList.toggle('active',element.dataset.panel===name));
  $('#page-title').textContent={overview:'Overview',submit:'Submit a job',accounts:'Users & devices',detail:'Job details'}[name];
  if (name!=='detail') {state.job=null;state.detailRequest++;}
  if (name==='accounts') safe(accounts);
  if (name==='overview'&&state.me) safe(refresh);
}
document.querySelectorAll('[data-panel]').forEach(element=>element.addEventListener('click',()=>panel(element.dataset.panel)));
$('#back').addEventListener('click',()=>panel('overview'));
async function refresh() {
  const generation=state.generation;
  if(state.busy&&state.busyGeneration===generation){state.refreshPending=true;return;}
  state.busy=true;state.busyGeneration=generation;
  try {
    const health=await api('GET','/health',null,{public:true});
    connection('Server online · '+health.server_version,true);
    if(!state.me)return;
    if(state.me.role==='admin'&&!$('#accounts').hidden)await accounts();
    const resource=await api('GET','/system/resources');
    $('#running').textContent=resource.running;$('#pending').textContent=resource.pending;
    $('#cpu').textContent=resource.reserved_cpus+' / '+resource.cpus;
    $('#memory').textContent=gb(resource.reserved_memory_mb)+' / '+gb(resource.memory_mb);
    state.paused=resource.paused;$('#queue-label').textContent=resource.paused?'Queue paused':'Shared queue';$('#pause').textContent=resource.paused?'Resume queue':'Pause queue';
    const page=state.page;
    const jobs=await api('GET',(state.me.role==='admin'?'/admin/jobs':'/jobs')+'?limit=50&offset='+page*50);
    if(page!==state.page){state.refreshPending=true;return;}
    $('#jobs-body').replaceChildren();
    for(const job of jobs){
      const row=document.createElement('tr');const name=cell(row,job.spec.name,'job-name');
      const id=document.createElement('small');id.className='job-id';id.textContent=job.id;name.append(id);
      const status=cell(row,'');const badge=document.createElement('span');badge.className='badge '+job.display_state.toLowerCase();badge.textContent=job.display_state;status.append(badge);const priority=document.createElement('small');priority.className='job-id';priority.textContent='Priority '+(job.priority??0)+(job.force_run?' · Force requested':'');status.append(priority);
      cell(row,job.spec.resources.cpus_per_task+' cores · '+gb(job.spec.resources.memory_mb)+' GB');cell(row,date(job.created_at));button(cell(row,''),'Open',()=>detail(job.id));$('#jobs-body').append(row);
    }
    if(!jobs.length){const row=document.createElement('tr');const empty=cell(row,'No jobs on this page.','empty');empty.colSpan=5;$('#jobs-body').append(row);}
    $('#previous-page').disabled=state.page===0;$('#next-page').disabled=jobs.length<50;$('#page-number').textContent='Page '+(state.page+1);
    if(state.job)await detail(state.job,false);
  } catch(error) {if(!error.staleSession)connection('Connection failed');throw error;
  } finally {
    if(state.busyGeneration===generation){
      state.busy=false;
      if(state.refreshPending){state.refreshPending=false;safe(refresh);}
    }
  }
}
$('#refresh').addEventListener('click',()=>safe(refresh));
$('#previous-page').addEventListener('click',()=>{state.page=Math.max(0,state.page-1);safe(refresh);});
$('#next-page').addEventListener('click',()=>{state.page++;safe(refresh);});
$('#pause').addEventListener('click',()=>safe(async()=>{await api('POST','/admin/queue/pause',{paused:!state.paused});await refresh();}));

$('#submit-form').addEventListener('submit',event=>{
  event.preventDefault();const form=event.currentTarget;return safe(async()=>{
    if(!state.me)throw new Error('Sign in before submitting a job.');
    const generation=state.generation;
    const submit=form.querySelector('[type=submit]');submit.disabled=true;
    try{
      const fields=form.elements,files=[...fields.files.files],manifest=[];
      for(const file of files){$('#upload-progress').textContent='Verifying '+file.name+'…';manifest.push({path:file.name,size:file.size,sha256:await digest(await file.arrayBuffer())});}
      const spec={name:fields.name.value,command:fields.command.value,files:manifest,resources:{cpus_per_task:Number(fields.cpus.value),memory_mb:Math.max(1,Math.round(Number(fields.memory.value)*1024)),walltime_seconds:Number(fields.walltime.value)},after_job:fields.after.value||null,chain_any:fields.chain_any.checked,auto_download:true,fetch_globs:fields.globs.value.split(',').map(x=>x.trim()).filter(Boolean)};
      const fingerprint=await digest(encoder.encode(JSON.stringify(spec)));
      requireSession(generation);
      const prior=JSON.parse(sessionStorage.getItem('pending-submit')||'null');
      const key=prior?.fingerprint===fingerprint?prior.key:randomId();
      sessionStorage.setItem('pending-submit',JSON.stringify({key,fingerprint}));
      const reservation=await api('POST','/jobs',spec,{headers:{'Idempotency-Key':key}});
      const statuses=await api('GET','/jobs/'+reservation.id+'/uploads');
      for(const entry of statuses){if(entry.complete)continue;const file=files.find(file=>file.name===entry.path);let offset=entry.offset;
        while(offset<file.size){const end=Math.min(file.size,offset+state.capabilities.max_chunk_bytes);$('#upload-progress').textContent='Uploading '+file.name+' · '+Math.floor(offset/file.size*100)+'%';const result=await api('PUT','/jobs/'+reservation.id+'/files/'+encodeURIComponent(file.name),file.slice(offset,end),{headers:{'Content-Range':`bytes ${offset}-${end-1}/${file.size}`}});offset=result.offset;}
        if(!file.size)await api('PUT','/jobs/'+reservation.id+'/files/'+encodeURIComponent(file.name),new Blob([]));
      }
      await api('POST','/jobs/'+reservation.id+'/commit');sessionStorage.removeItem('pending-submit');$('#upload-progress').textContent='Job accepted.';message('');state.page=0;await refresh();await detail(reservation.id);
    }finally{submit.disabled=false;}
  });
});
async function detail(id,reset=true){
  if(!id)return;
  const request=++state.detailRequest;
  state.job=id;if(reset){state.jobInfo=null;$('#job-controls').hidden=true;state.offset=0;$('#log').textContent='';$('#detail-name').textContent='Loading…';$('#detail-id').textContent=id;$('#detail-spec').textContent='';$('#result-files').replaceChildren();panel('detail');}
  const current=()=>state.job===id&&state.detailRequest===request;
  const job=await api('GET','/jobs/'+id);if(!current())return;state.jobInfo=job;
  $('#job-controls').hidden=state.me?.role!=='admin'||job.state!=='PENDING';
  if(document.activeElement!==$('#job-priority'))$('#job-priority').value=job.priority??0;
  $('#force-job').textContent=job.force_run?'Cancel force request':'Force run…';
  $('#force-job').disabled=$('#set-priority').disabled=state.queueBusy;
  $('#detail-name').textContent=job.spec.name+' · '+job.display_state;$('#detail-id').textContent=id;
  $('#detail-spec').textContent=job.spec.command+'\n\n'+job.spec.resources.cpus_per_task+' CPU cores · '+gb(job.spec.resources.memory_mb)+' GB · exit code '+(job.exit_code??'—')+(job.reason?'\n'+job.reason:'');
  $('#cancel-job').disabled=['DONE','FAILED','CANCELLED','LOST'].includes(job.state);
  const log=await api('GET',`/jobs/${id}/log?offset=${state.offset}&limit=65536`);if(!current())return;$('#log').textContent=($('#log').textContent+log.text).slice(-1000000);state.offset=log.next_offset;
  $('#result-files').replaceChildren();
  if(['DONE','FAILED','CANCELLED','LOST'].includes(job.state)){
    const files=await api('GET','/jobs/'+id+'/files');if(!current())return;
    for(const entry of files){const row=document.createElement('li');const name=document.createElement('span');name.textContent=entry.path;const size=document.createElement('small');size.textContent=entry.size.toLocaleString()+' bytes';name.append(size);row.append(name);button(row,'Download',()=>download(id,entry));$('#result-files').append(row);}
  }
}
$('#refresh-detail').addEventListener('click',()=>safe(()=>detail(state.job,false)));
$('#cancel-job').addEventListener('click',()=>safe(async()=>{if(!confirm('Stop this job and its child processes?'))return;await api('POST','/jobs/'+state.job+'/cancel');await detail(state.job,false);}));
async function queueControl(action) {
  const job=state.jobInfo,id=state.job,generation=state.generation;
  if(state.queueBusy||state.me?.role!=='admin'||!job||job.id!==id||job.state!=='PENDING')return;
  let path,body;
  if(action==='priority') {
    const priority=Number($('#job-priority').value);
    if(!Number.isInteger(priority)||priority < -1000||priority > 1000)throw new Error('Priority must be an integer from -1000 to 1000.');
    path='priority';body={priority};
  } else {
    const force_run=!job.force_run;
    if(force_run&&!confirm('Force this job to run? This overrides queue pause, waiting order, CPU/memory budgets and per-user active limits. It may overcommit this PC. Dependency conditions still apply.'))return;
    path='force-run';body={force_run};
  }
  state.queueBusy=true;$('#force-job').disabled=$('#set-priority').disabled=true;
  try {
    await api('POST','/admin/jobs/'+encodeURIComponent(id)+'/'+path,body);
    requireSession(generation);
    if(state.job===id)await detail(id,false);
  } finally {
    if(generation===state.generation){state.queueBusy=false;$('#force-job').disabled=$('#set-priority').disabled=false;}
  }
}
$('#set-priority').addEventListener('click',()=>safe(()=>queueControl('priority')));
$('#force-job').addEventListener('click',()=>safe(()=>queueControl('force')));
async function download(id,entry){
  const generation=state.generation;
  if(!state.token||state.expires<Date.now()/1000+10)throw Object.assign(new Error('Sign in again to download results.'),{status:401});
  requireSession(generation);
  const response=await fetch('/api/v1/jobs/'+id+'/files/'+entry.path.split('/').map(encodeURIComponent).join('/'),{headers:{Authorization:'Bearer '+state.token},credentials:'omit',redirect:'error'});
  if(!response.ok)throw new Error('Download failed. Refresh your session and try again.');
  requireSession(generation);
  const blob=await response.blob();requireSession(generation);
  const checksum=await digest(await blob.arrayBuffer());
  requireSession(generation);
  if(blob.size!==entry.size||checksum!==entry.sha256)throw new Error('Downloaded checksum differs. Please retry.');
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=entry.path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function accounts(){
  const request=++state.accountsRequest,generation=state.generation;
  const users=await api('GET','/admin/users');
  const devices=await api('GET','/admin/devices');
  if(request!==state.accountsRequest||generation!==state.generation)return;
  $('#users-body').replaceChildren();
  for(const user of users){const row=document.createElement('tr');cell(row,user.username);cell(row,user.role);cell(row,user.enabled?'Enabled':'Disabled');const action=cell(row,'');if(user.enabled&&user.id!==state.me.user_id)button(action,'Disable',async()=>{if(confirm('Disable '+user.username+' and its active credentials?')){await api('POST','/admin/users/'+user.id+'/disable');await accounts();}});$('#users-body').append(row);}
  $('#devices-body').replaceChildren();
  for(const device of devices){const row=document.createElement('tr');cell(row,device.id);cell(row,users.find(u=>u.id===device.user_id)?.username||device.user_id);cell(row,date(device.last_access));const action=cell(row,'');if(device.revoked_at)action.textContent='Revoked';else button(action,'Revoke',async()=>{if(confirm('Revoke this device and its current tokens?')){await api('POST','/admin/devices/'+device.id+'/revoke');await accounts();}});$('#devices-body').append(row);}
}
$('#user-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;safe(async()=>{await api('POST','/admin/users',{username:form.elements.username.value,password:form.elements.password.value,role:form.elements.role.value});form.reset();await accounts();});});
setSignedIn(false);$('#login-submit').disabled=false;
safe(restoreSession);setInterval(()=>{if(!document.hidden)safe(refresh);},5000);
document.addEventListener?.('visibilitychange',()=>{if(!document.hidden)safe(refresh);});
