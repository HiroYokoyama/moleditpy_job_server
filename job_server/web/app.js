const $ = selector => document.querySelector(selector);
const state = {token:null, expires:0, authenticated:0, profile:null, me:null, capabilities:null, paused:false, job:null, offset:0, page:0, busy:false};
const encoder = new TextEncoder();
const b64 = value => btoa(String.fromCharCode(...new Uint8Array(value)));
const digest = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256',value))].map(v=>v.toString(16).padStart(2,'0')).join('');
function message(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
function date(value) { return value ? new Date(value*1000).toLocaleString() : '—'; }
function cell(row,text,className='') { const td = document.createElement('td'); td.textContent=String(text); td.className=className; row.append(td); return td; }
function button(parent,text,call) { const element=document.createElement('button'); element.textContent=text; element.addEventListener('click',()=>safe(call)); parent.append(element); return element; }
async function safe(call) { try { await call(); } catch(error) { message(error.message); if($('#login-dialog').open)$('#login-status').textContent=error.message; } }

function database() {
  return new Promise((resolve,reject)=>{const request=indexedDB.open('moleditpy-job-server',1);request.onupgradeneeded=()=>request.result.createObjectStore('devices');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
}
async function credential(username,value) {
  const db=await database();
  return new Promise((resolve,reject)=>{
    const transaction=db.transaction('devices',value?'readwrite':'readonly');
    const store=transaction.objectStore('devices');
    const request=value?store.put(value,location.origin+'|'+username):store.get(location.origin+'|'+username);
    transaction.oncomplete=()=>{resolve(request.result);db.close();};transaction.onerror=()=>{reject(transaction.error);db.close();};
  });
}
async function api(method,path,body,options={}) {
  if (!options.public && (!state.token || state.expires<Date.now()/1000+10 || (path.startsWith('/admin/') && Date.now()/1000-state.authenticated>240))) await authenticate();
  const headers={...options.headers};
  if (!options.public) headers.Authorization='Bearer '+state.token;
  if (options.token) headers.Authorization='Bearer '+options.token;
  let payload;
  if (body instanceof Blob || body instanceof ArrayBuffer) payload=body;
  else if (method==='POST'||method==='PUT') {headers['Content-Type']='application/json';payload=JSON.stringify(body||{});}
  const response=await fetch('/api/v1'+path,{method,headers,body:payload,credentials:'omit',redirect:'error',cache:'no-store'});
  if (!response.ok) {
    const error=await response.json().catch(()=>({message:'Server request failed'}));
    if (response.status===401&&!options.public) state.token=null;
    throw new Error(error.message||'Server request failed');
  }
  if (response.headers.get('X-API-Version')!=='1') throw new Error('Unsupported server API version');
  return response.json();
}
async function authenticate(challenge=null) {
  if (!state.profile) throw new Error('Sign in to register or unlock this browser’s device.');
  const profile=state.profile;
  challenge=challenge||await api('POST','/auth/challenge',{device_id:profile.deviceId},{public:true});
  if (challenge.server_id!==profile.serverId||challenge.protocol_version!=='1') throw new Error('Server identity changed. Verify the server before registering again.');
  const fields={device_id:profile.deviceId,nonce:challenge.nonce,protocol_version:'1',purpose:'moleditpy-job-server-access',server_id:profile.serverId};
  const signature=await crypto.subtle.sign('Ed25519',profile.privateKey,encoder.encode(JSON.stringify(fields)));
  const result=await api('POST','/auth/token',{device_id:profile.deviceId,nonce:challenge.nonce,signature:b64(signature)},{public:true});
  state.token=result.token;state.expires=result.expires_at;state.authenticated=Date.now()/1000;
}
$('#login-form').addEventListener('submit',event=>{
  event.preventDefault();const submit=event.currentTarget.querySelector('[type=submit]');if(submit.disabled)return;submit.disabled=true;safe(async()=>{
    const form=event.currentTarget,username=form.elements.username.value.trim(),password=form.elements.password.value;
    if (!crypto.subtle) throw new Error('Open this page over HTTPS or same-machine localhost.');
    $('#login-status').textContent='Authenticating…';
    let profile=await credential(username);
    if (!profile) {
      if (!password) throw new Error('An initial password is required to register this browser.');
      const session=await api('POST','/auth/login',{username,password},{public:true});
      const key=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']);
      const deviceId=crypto.randomUUID();
      const publicKey=await crypto.subtle.exportKey('raw',key.publicKey);
      await api('POST','/auth/devices',{device_id:deviceId,public_key:b64(publicKey)},{public:true,token:session.token});
      const challenge=await api('POST','/auth/challenge',{device_id:deviceId},{public:true});
      profile={deviceId,serverId:challenge.server_id,privateKey:key.privateKey,username};
      await credential(username,profile);
      state.profile=profile;await authenticate(challenge);
    } else {state.profile=profile;await authenticate();}
    form.elements.password.value='';$('#login-status').textContent='';$('#login-dialog').close();
    state.me=await api('GET','/me');state.capabilities=await api('GET','/capabilities');
    $('#signin').textContent=state.me.username;$('#signout').hidden=false;
    const admin=state.me.role==='admin';$('#accounts-nav').hidden=!admin;$('#pause').hidden=!admin;
    $('#jobs-caption').textContent=admin?'All jobs on this server.':'Your jobs on this server.';
    message('');await refresh();
  }).finally(()=>{submit.disabled=false;});
});
$('#signin').addEventListener('click',()=>{$('#login-dialog').showModal();$('#login-status').textContent='';});
$('#close-login').addEventListener('click',()=>{$('#login-form').elements.password.value='';$('#login-dialog').close();});
$('#signout').addEventListener('click',()=>{state.token=null;state.profile=null;state.me=null;state.job=null;$('#signin').textContent='Sign in';$('#signout').hidden=true;$('#accounts-nav').hidden=true;$('#pause').hidden=true;$('#jobs-body').replaceChildren();panel('overview');message('Signed out. Your device key remains registered in this browser.');});

function panel(name) {
  if (name==='accounts'&&state.me?.role!=='admin') return;
  document.querySelectorAll('.panel').forEach(element=>element.hidden=element.id!==name);
  document.querySelectorAll('.nav').forEach(element=>element.classList.toggle('active',element.dataset.panel===name));
  $('#page-title').textContent={overview:'Overview',submit:'Submit a job',accounts:'Users & devices',detail:'Job details'}[name];
  if (name!=='detail') state.job=null;
  if (name==='accounts') safe(accounts);
}
document.querySelectorAll('[data-panel]').forEach(element=>element.addEventListener('click',()=>panel(element.dataset.panel)));
$('#back').addEventListener('click',()=>panel('overview'));
async function refresh() {
  if(state.busy) return;
  state.busy=true;
  try {
    const health=await api('GET','/health',null,{public:true});
    $('#connection-dot').classList.add('online');$('#connection-text').textContent='Server online · '+health.server_version;
    if(!state.me)return;
    if(state.job){await detail(state.job,false);return;}
    const resource=await api('GET','/system/resources');
    $('#running').textContent=resource.running;$('#pending').textContent=resource.pending;
    $('#cpu').textContent=resource.reserved_cpus+' / '+resource.cpus;
    $('#memory').textContent=resource.reserved_memory_mb+' / '+resource.memory_mb;
    state.paused=resource.paused;$('#queue-label').textContent=resource.paused?'Queue paused':'Shared queue';$('#pause').textContent=resource.paused?'Resume queue':'Pause queue';
    const jobs=await api('GET',(state.me.role==='admin'?'/admin/jobs':'/jobs')+'?limit=50&offset='+state.page*50);
    $('#jobs-body').replaceChildren();
    for(const job of jobs){
      const row=document.createElement('tr');const name=cell(row,job.spec.name,'job-name');
      const id=document.createElement('small');id.className='job-id';id.textContent=job.id;name.append(id);
      const status=cell(row,'');const badge=document.createElement('span');badge.className='badge '+job.display_state.toLowerCase();badge.textContent=job.display_state;status.append(badge);
      cell(row,job.spec.resources.cpus_per_task+' cores · '+job.spec.resources.memory_mb+' MB');cell(row,date(job.created_at));button(cell(row,''),'Open',()=>detail(job.id));$('#jobs-body').append(row);
    }
    if(!jobs.length){const row=document.createElement('tr');const empty=cell(row,'No jobs on this page.','empty');empty.colSpan=5;$('#jobs-body').append(row);}
    $('#previous-page').disabled=state.page===0;$('#next-page').disabled=jobs.length<50;$('#page-number').textContent='Page '+(state.page+1);
  } finally {state.busy=false;}
}
$('#refresh').addEventListener('click',()=>safe(refresh));
$('#previous-page').addEventListener('click',()=>{state.page=Math.max(0,state.page-1);safe(refresh);});
$('#next-page').addEventListener('click',()=>{state.page++;safe(refresh);});
$('#pause').addEventListener('click',()=>safe(async()=>{await api('POST','/admin/queue/pause',{paused:!state.paused});await refresh();}));

$('#submit-form').addEventListener('submit',event=>{
  event.preventDefault();const form=event.currentTarget;safe(async()=>{
    if(!state.me)throw new Error('Sign in before submitting a job.');
    const submit=form.querySelector('[type=submit]');submit.disabled=true;
    try{
      const fields=form.elements,files=[...fields.files.files],manifest=[];
      for(const file of files){$('#upload-progress').textContent='Verifying '+file.name+'…';manifest.push({path:file.name,size:file.size,sha256:await digest(await file.arrayBuffer())});}
      const spec={name:fields.name.value,command:fields.command.value,files:manifest,resources:{cpus_per_task:Number(fields.cpus.value),memory_mb:Number(fields.memory.value),walltime_seconds:Number(fields.walltime.value)},after_job:fields.after.value||null,chain_any:fields.chain_any.checked,auto_download:true,fetch_globs:fields.globs.value.split(',').map(x=>x.trim()).filter(Boolean)};
      const fingerprint=await digest(encoder.encode(JSON.stringify(spec)));
      const prior=JSON.parse(sessionStorage.getItem('pending-submit')||'null');
      const key=prior?.fingerprint===fingerprint?prior.key:crypto.randomUUID();
      sessionStorage.setItem('pending-submit',JSON.stringify({key,fingerprint}));
      const reservation=await api('POST','/jobs',spec,{headers:{'Idempotency-Key':key}});
      const statuses=await api('GET','/jobs/'+reservation.id+'/uploads');
      for(const entry of statuses){if(entry.complete)continue;const file=files.find(file=>file.name===entry.path);let offset=entry.offset;
        while(offset<file.size){const end=Math.min(file.size,offset+state.capabilities.max_chunk_bytes);$('#upload-progress').textContent='Uploading '+file.name+' · '+Math.floor(offset/file.size*100)+'%';const result=await api('PUT','/jobs/'+reservation.id+'/files/'+encodeURIComponent(file.name),file.slice(offset,end),{headers:{'Content-Range':`bytes ${offset}-${end-1}/${file.size}`}});offset=result.offset;}
        if(!file.size)await api('PUT','/jobs/'+reservation.id+'/files/'+encodeURIComponent(file.name),new Blob([]));
      }
      await api('POST','/jobs/'+reservation.id+'/commit');sessionStorage.removeItem('pending-submit');$('#upload-progress').textContent='Job accepted.';message('');await detail(reservation.id);
    }finally{submit.disabled=false;}
  });
});
async function detail(id,reset=true){
  const job=await api('GET','/jobs/'+id);state.job=id;if(reset){state.offset=0;$('#log').textContent='';panel('detail');state.job=id;}
  $('#detail-name').textContent=job.spec.name+' · '+job.display_state;$('#detail-id').textContent=id;
  $('#detail-spec').textContent=job.spec.command+'\n\n'+job.spec.resources.cpus_per_task+' CPU cores · '+job.spec.resources.memory_mb+' MB · exit code '+(job.exit_code??'—')+(job.reason?'\n'+job.reason:'');
  $('#cancel-job').disabled=['DONE','FAILED','CANCELLED','LOST'].includes(job.state);
  const log=await api('GET',`/jobs/${id}/log?offset=${state.offset}&limit=65536`);$('#log').textContent=($('#log').textContent+log.text).slice(-1000000);state.offset=log.next_offset;
  $('#result-files').replaceChildren();
  if(['DONE','FAILED','CANCELLED','LOST'].includes(job.state)){
    for(const entry of await api('GET','/jobs/'+id+'/files')){const row=document.createElement('li');const name=document.createElement('span');name.textContent=entry.path;const size=document.createElement('small');size.textContent=entry.size.toLocaleString()+' bytes';name.append(size);row.append(name);button(row,'Download',()=>download(id,entry));$('#result-files').append(row);}
  }
}
$('#refresh-detail').addEventListener('click',()=>safe(()=>detail(state.job,false)));
$('#cancel-job').addEventListener('click',()=>safe(async()=>{if(!confirm('Stop this job and its child processes?'))return;await api('POST','/jobs/'+state.job+'/cancel');await detail(state.job,false);}));
async function download(id,entry){
  if(!state.token||state.expires<Date.now()/1000+10)await authenticate();
  const response=await fetch('/api/v1/jobs/'+id+'/files/'+entry.path.split('/').map(encodeURIComponent).join('/'),{headers:{Authorization:'Bearer '+state.token},credentials:'omit',redirect:'error'});
  if(!response.ok)throw new Error('Download failed. Refresh your session and try again.');
  const blob=await response.blob();if(blob.size!==entry.size||await digest(await blob.arrayBuffer())!==entry.sha256)throw new Error('Downloaded checksum differs. Please retry.');
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=entry.path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function accounts(){
  const users=await api('GET','/admin/users');$('#users-body').replaceChildren();
  for(const user of users){const row=document.createElement('tr');cell(row,user.username);cell(row,user.role);cell(row,user.enabled?'Enabled':'Disabled');const action=cell(row,'');if(user.enabled&&user.id!==state.me.user_id)button(action,'Disable',async()=>{if(confirm('Disable '+user.username+' and its active credentials?')){await api('POST','/admin/users/'+user.id+'/disable');await accounts();}});$('#users-body').append(row);}
  const devices=await api('GET','/admin/devices');$('#devices-body').replaceChildren();
  for(const device of devices){const row=document.createElement('tr');cell(row,device.id);cell(row,users.find(u=>u.id===device.user_id)?.username||device.user_id);cell(row,date(device.last_access));const action=cell(row,'');if(device.revoked_at)action.textContent='Revoked';else button(action,'Revoke',async()=>{if(confirm('Revoke this device and its current tokens?')){await api('POST','/admin/devices/'+device.id+'/revoke');await accounts();}});$('#devices-body').append(row);}
}
$('#user-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;safe(async()=>{await api('POST','/admin/users',{username:form.elements.username.value,password:form.elements.password.value,role:form.elements.role.value});form.reset();await accounts();});});
safe(refresh);setInterval(()=>{if(state.me&&!document.hidden)safe(refresh);},5000);
