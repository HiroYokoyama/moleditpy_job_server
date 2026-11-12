'use strict';
const $ = selector => document.querySelector(selector);
const state = {token:null, expires:0, authenticated:0,  me:null, capabilities:null, paused:false, job:null, jobInfo:null, queueBusy:false, offset:0, page:0, busy:false, refreshPending:false, generation:0, busyGeneration:-1, detailRequest:0, accountsRequest:0, accountRequest:0, activityRequest:0, browseRequest:0, browsePath:'', resetUser:null, names:null};
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
// Memory is stored in MB but shown and entered in GiB (1024 MB, the unit Windows calls GB).
function gb(mb) { return typeof mb==='number' ? (mb/1024).toFixed(1) : '—'; }
function date(value) { return value ? new Date(value*1000).toLocaleString() : '—'; }
function cell(row,text,className='') { const td = document.createElement('td'); td.textContent=String(text); td.className=className; row.append(td); return td; }
// Lists are rebuilt only when what they show has changed: rebuilding identical rows on
// every refresh made them flicker and dropped the pointer's hover and keyboard focus.
function renderList(container,data,build) {
  const key=JSON.stringify(data);
  if(container.dataset.rendered===key)return false;
  container.replaceChildren(...build());container.dataset.rendered=key;return true;
}
function clearList(container) { container.replaceChildren(); delete container.dataset.rendered; }
// Rewriting unchanged text still drops a selection the user is making in it.
function setText(element,value) { if(element.textContent!==value)element.textContent=value; }
// Dials and trend lines for the overview figures. The server records its load every
// few seconds whether or not anyone is signed in; this tab fetches it a little at a time.
const TRENDS={running:1,pending:2,'cpu-now':3,'memory-now':4,cpu:5,memory:6};
// The web overview always shows the last five minutes.
const trendState={rows:[],gap:30,span:300};
const SVG='http://www.w3.org/2000/svg';
function share(used,total) { return typeof used==='number'&&typeof total==='number'&&total>0?Math.max(0,Math.min(100,100*used/total)):null; }
function svgElement(name,attributes) { const element=document.createElementNS(SVG,name); for(const [key,value] of Object.entries(attributes))element.setAttribute(key,String(value)); return element; }
// A 270-degree dial opening at the bottom, filled clockwise from its lower left.
function dialArc(fraction) {
  const point=angle=>(22+17*Math.cos(angle)).toFixed(2)+' '+(22+17*Math.sin(angle)).toFixed(2);
  const start=Math.PI*3/4,sweep=Math.PI*3/2*Math.min(fraction,0.9999);
  return 'M'+point(start)+'A17 17 0 '+(sweep>Math.PI?1:0)+' 1 '+point(start+sweep);
}
// Where every core is busy, as a share of all threads; null without hyper-threading.
const coreLine={share:null,cores:null};
function dial(key,value) {
  const known=typeof value==='number';
  const label=$('#dial-text-'+key);if(label)setText(label,known?Math.round(value)+'%':'—');
  const arc=$('#dial-'+key);if(typeof arc?.setAttribute!=='function')return;
  arc.setAttribute('d',known&&value>0?dialArc(value/100):'');
  // Coloured by the share of its limit: every core for CPU use on a hyper-threaded PC.
  const limit=key==='cpu-now'&&coreLine.share!==null?coreLine.share:100,ofLimit=known?100*value/limit:0;
  arc.setAttribute('class','dial-value'+(!known||value<=0?' unknown':ofLimit>100.5?' over':ofLimit>=90?' high':ofLimit>=70?' warn':''));
}
function mergeHistory(reply) {
  if(!reply||!Array.isArray(reply.samples))return;
  const last=trendState.rows.length?trendState.rows[trendState.rows.length-1][0]:0;
  for(const row of reply.samples)if(row[0]>last)trendState.rows.push(row);
  trendState.gap=3*(reply.interval||10);
  const horizon=Date.now()/1000-trendState.span;
  while(trendState.rows.length&&trendState.rows[0][0]<horizon)trendState.rows.shift();
  for(const key of Object.keys(TRENDS))drawTrend(key);
}
// The value the top of the plot stands for: 100% for a share, a round number above the counts shown for jobs.
function trendTop(key,rows=trendState.rows.filter(row=>row[0]>=Date.now()/1000-trendState.span)) {
  if(!['running','pending'].includes(key))return 100;
  // A round number above the largest count (1, 2, 5, 10, 20, 50...), so the line never lies on the top.
  const largest=Math.max(0,...rows.map(row=>row[TRENDS[key]]).filter(value=>typeof value==='number'));
  for(let step=1;;step*=10)for(const factor of [1,2,5])if(factor*step>largest)return factor*step;
}
function trendPoints(key,width,height,now=Date.now()/1000) {
  const column=TRENDS[key],rows=trendState.rows.filter(row=>row[0]>=now-trendState.span);
  const top=trendTop(key,rows),points=[];
  let previous=null;
  for(const row of rows){
    // A silence longer than a few samples is time the server was not running: break the line.
    if(previous!==null&&row[0]-previous>trendState.gap)points.push({when:previous,value:null,x:null,y:null});
    previous=row[0];const value=typeof row[column]==='number'?row[column]:null;
    points.push({when:row[0],value,x:value===null?null:width*(1-(now-row[0])/trendState.span),y:value===null?null:2+(height-4)*(1-Math.min(value,top)/top)});
  }
  return points;
}
function drawTrend(key,hover=null) {
  const svg=$('#trend-'+key);if(typeof svg?.setAttribute!=='function'||typeof document.createElementNS!=='function')return;
  const width=svg.clientWidth||200,height=svg.clientHeight||30;
  svg.setAttribute('viewBox','0 0 '+width+' '+height);
  let path='',area='',drawing=false,last=null;
  const close=()=>{if(last!==null)area+='L'+last+' '+height+'Z';last=null;};
  for(const point of trendPoints(key,width,height)){
    if(point.x===null){drawing=false;close();continue;}
    const x=point.x.toFixed(1),y=point.y.toFixed(1);
    path+=(drawing?'L':'M')+x+' '+y;area+=drawing?'L'+x+' '+y:'M'+x+' '+height+'L'+x+' '+y;drawing=true;last=x;
  }
  close();
  // A thin line marks the top of the scale; its right end says what it stands for.
  const top=trendTop(key),label=svgElement('text',{class:'trend-top-label',x:width-2,y:-3});
  label.textContent=(['running','pending'].includes(key)?top:top+'%');
  const parts=[svgElement('line',{class:'trend-top',x1:0,x2:width,y1:2,y2:2}),svgElement('line',{class:'trend-base',x1:0,x2:width,y1:height-0.5,y2:height-0.5}),svgElement('path',{class:'trend-area',d:area}),svgElement('path',{class:'trend-line',d:path}),label];
  if(hover)parts.push(svgElement('line',{class:'trend-cross',x1:hover.x,x2:hover.x,y1:0,y2:height}),svgElement('circle',{class:'trend-dot',cx:hover.x,cy:hover.y,r:4}));
  if(key==='cpu-now'&&coreLine.share!==null){
    // 100% is every thread; past the dotted line the hyper-threads are in use.
    const y=(2+(height-4)*(1-coreLine.share/100)).toFixed(1),note=svgElement('text',{class:'trend-top-label',x:width-2,y:Number(y)-4});
    note.textContent=coreLine.cores+' cores';
    parts.splice(1,0,svgElement('line',{class:'trend-cores',x1:0,x2:width,y1:y,y2:y}));parts.push(note);
  }
  svg.replaceChildren(...parts);
}
function describeSample(key,point) {
  const amount=['running','pending'].includes(key)?String(Math.round(point.value)):Math.round(point.value)+'%';
  return new Date(point.when*1000).toLocaleTimeString()+' — '+amount;
}
for(const key of Object.keys(TRENDS)){
  const svg=$('#trend-'+key);
  svg.addEventListener('pointermove',event=>{
    const box=svg.getBoundingClientRect?.();if(!box)return;
    const x=event.clientX-box.left,points=trendPoints(key,box.width,box.height).filter(point=>point.x!==null);
    const nearest=points.reduce((best,point)=>!best||Math.abs(point.x-x)<Math.abs(best.x-x)?point:best,null);
    const tip=$('#trend-tip');
    // Only a reading close to the pointer is described: across a gap there is nothing to read.
    if(!nearest||Math.abs(nearest.x-x)>12){tip.hidden=true;drawTrend(key);return;}
    tip.textContent=describeSample(key,nearest);tip.hidden=false;
    const room=(globalThis.innerWidth||Infinity)-(tip.offsetWidth||0)-8;tip.style.left=Math.min(event.clientX+12,room)+'px';tip.style.top=(event.clientY-38)+'px';
    drawTrend(key,nearest);
  });
  svg.addEventListener('pointerleave',()=>{$('#trend-tip').hidden=true;drawTrend(key);});
}
globalThis.addEventListener?.('resize',()=>{for(const key of Object.keys(TRENDS))drawTrend(key);});
function clearTrends() { trendState.rows.length=0; coreLine.share=coreLine.cores=null; for(const key of Object.keys(TRENDS)){drawTrend(key);if(!['running','pending'].includes(key))dial(key,null);} }

function cores(count) { return count+(count===1?' core':' cores'); }
function ahead(count) { return count===0 ? 'next in line' : count+' ahead'; }
function renderQueue(queue) {
  const rows=[];
  const add=(entry,place,stateText)=>{
    const row=document.createElement('tr');if(entry.mine)row.className='mine';
    cell(row,place);const name=cell(row,entry.name,'job-name');
    cell(row,entry.mine?entry.owner+' (you)':entry.owner);
    cell(row,cores(entry.cpus)+' · '+gb(entry.memory_mb)+' GiB');
    const status=cell(row,'');const badge=document.createElement('span');badge.className='badge '+stateText.toLowerCase();badge.textContent=stateText;status.append(badge);
    if(entry.priority){const note=document.createElement('small');note.className='job-id';note.textContent='Priority '+entry.priority;status.append(note);}
    const open=cell(row,'');if(entry.job_id)button(open,'Open',()=>detail(entry.job_id));
    rows.push(row);return name;
  };
  renderList($('#queue-body'),[queue.running,queue.waiting],()=>{
    for(const entry of queue.running)add(entry,'—','RUNNING');
    for(const entry of queue.waiting)add(entry,entry.position,entry.display_state);
    if(!queue.running.length&&!queue.waiting.length){const row=document.createElement('tr');const empty=cell(row,'Nothing is running or waiting.','empty');empty.colSpan=6;rows.push(row);}
    return rows;
  });
  const next=queue.waiting.find(entry=>entry.mine);
  $('#queue-summary').textContent=queue.running.length+' running · '+queue.waiting.length+' waiting'+(next?' · your next job: '+ahead(next.position-1):'')+(queue.paused?' · paused':'');
}
function button(parent,text,call) { const element=document.createElement('button'); element.textContent=text; element.addEventListener('click',()=>safe(call)); parent.append(element); return element; }
async function safe(call) { try { await call(); } catch(error) { if(error.staleSession)return; if(error.status===401&&state.me)clearSession();message(error.message); if($('#login-dialog').open)$('#login-status').textContent=error.message; } }
function setSignedIn(value) {
  document.body.classList.toggle('signed-out',!value);
  $('#close-login').hidden=!value;
  if(value)$('#login-dialog').close();
  else if(!$('#login-dialog').open)$('#login-dialog').show();
}
function clearPasswordForms() {
  for(const form of [$('#password-form'),$('#reset-form')])form.reset?.();
  $('#password-status').textContent='';$('#reset-status').textContent='';
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
  state.generation++;state.detailRequest++;state.accountsRequest++;state.accountRequest++;state.activityRequest++;state.browseRequest++;
  Object.assign(state,{token:null,expires:0,authenticated:0,me:null,capabilities:null,job:null,jobInfo:null,queueBusy:false,offset:0,page:0,paused:false,refreshPending:false,browsePath:'',resetUser:null,names:null});
  $('#signin').hidden=false;$('#user-chip').hidden=true;$('#signout').hidden=true;$('#accounts-nav').hidden=true;$('#account-nav').hidden=true;$('#pause').hidden=true;
  if($('#reset-dialog').open)$('#reset-dialog').close();clearPasswordForms();
  for(const id of ['jobs-body','queue-body','users-body','devices-body','result-files','my-devices-body','browser-body','activity-body'])clearList($('#'+id));
  for(const id of ['log','detail-name','detail-id','detail-spec','queue-summary'])$('#'+id).textContent='';
  for(const id of ['running','pending','cpu','memory','cpu-now','memory-now'])$('#'+id).textContent='—';
  clearTrends();
  $('#job-controls').hidden=true;panel('overview',{record:false});setSignedIn(false);return state.generation;
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
  const admin=state.me.role==='admin';
  // Who is signed in is shown as a chip leading to My account, unlike the Sign out action.
  $('#signin').hidden=true;$('#signout').hidden=false;$('#user-chip').hidden=false;
  $('#user-name').textContent=state.me.username;$('#user-role').textContent=admin?'Administrator':'User';
  $('#user-initial').textContent=state.me.username.slice(0,1).toUpperCase();$('#accounts-nav').hidden=!admin;$('#account-nav').hidden=false;$('#pause').hidden=!admin;
  $('#login-status').textContent='';setSignedIn(true);
  $('#jobs-caption').textContent=admin?'All jobs on this server.':'Your jobs on this server.';
  saveSession();message('');await refresh();followRoute();
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
  // The next person to sign in here starts from the overview, not from this user's page.
  try {globalThis.history?.replaceState?.({depth:0},'','#overview');} catch {}
  if(token)fetch('/api/v1/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:'{}',credentials:'omit',redirect:'error',cache:'no-store',keepalive:true}).catch(()=>{});
}
$('#signout').addEventListener('click',signOut);
$('#user-chip').addEventListener('click',()=>panel('account'));

// Remove credentials stored by older browser clients; never read or reuse them.
try {globalThis.indexedDB?.deleteDatabase('moleditpy-job-server');} catch {}

// Each page has its own address, so the browser's Back and Forward move between pages
// of this app instead of leaving it, and a reload stays on the same page.
const PAGES=['overview','submit','accounts','account'];
const JOB_ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function route() {
  const hash=String(globalThis.location?.hash||'').replace(/^#/,'');
  if(hash.startsWith('job/')&&JOB_ID.test(hash.slice(4)))return {name:'detail',job:hash.slice(4).toLowerCase()};
  return {name:PAGES.includes(hash)?hash:'overview'};
}
function remember(name,job) {
  const hash='#'+(name==='detail'?'job/'+job:name);
  if(!globalThis.history?.pushState||globalThis.location?.hash===hash)return;
  if(!globalThis.location?.hash&&name==='overview')history.replaceState({depth:0},'',hash);
  else history.pushState({depth:(history.state?.depth||0)+1},'',hash);
}
function followRoute() {
  const target=route();
  if(target.name==='detail'){if(state.job!==target.job)safe(()=>detail(target.job,true,{record:false}));}
  else if(target.name==='accounts'&&state.me?.role!=='admin')panel('overview',{record:false});
  else panel(target.name,{record:false});
}
globalThis.addEventListener?.('popstate',()=>{if(state.me)followRoute();});
function panel(name,{record=true,job=null}={}) {
  if (name==='accounts'&&state.me?.role!=='admin') return;
  if (record) remember(name,job);
  document.querySelectorAll('.panel').forEach(element=>element.hidden=element.id!==name);
  document.querySelectorAll('.nav').forEach(element=>element.classList.toggle('active',element.dataset.panel===name));
  const title={overview:'Overview',submit:'Submit a job',accounts:'Users & devices',account:'My account',detail:'Job details'}[name];
  $('#page-title').textContent=title;
  // Named per page, so the browser's history and tab list say where each entry leads.
  document.title=title+' · Job Server';
  if (name!=='detail') {state.job=null;state.detailRequest++;}
  if (name==='accounts') safe(accounts);
  if (name==='account'&&state.me) safe(accountPage);
  if (name==='overview'&&state.me) safe(refresh);
}
document.querySelectorAll('[data-panel]').forEach(element=>element.addEventListener('click',()=>panel(element.dataset.panel)));
// Back to where the job was opened from, as the browser's Back button would.
$('#back').addEventListener('click',()=>{if(globalThis.history?.state?.depth>0)history.back();else panel('overview');});
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
    $('#cpu-now').textContent=typeof resource.cpu_percent==='number'?Math.round(resource.cpu_percent)+'%':'—';
    $('#memory-now').textContent=typeof resource.memory_percent==='number'?gb(resource.memory_used_mb)+' / '+gb(resource.memory_total_mb):'—';
    const shares={'cpu-now':typeof resource.cpu_percent==='number'?resource.cpu_percent:null,'memory-now':typeof resource.memory_percent==='number'?resource.memory_percent:null,cpu:share(resource.reserved_cpus,resource.cpus),memory:share(resource.reserved_memory_mb,resource.memory_mb)};
    const hyper=Number.isInteger(resource.cpu_cores)&&Number.isInteger(resource.cpu_threads)&&resource.cpu_cores>0&&resource.cpu_cores<resource.cpu_threads;
    coreLine.share=hyper?100*resource.cpu_cores/resource.cpu_threads:null;coreLine.cores=hyper?resource.cpu_cores:null;
    for(const [key,value] of Object.entries(shares))dial(key,value);
    drawTrend('cpu-now');
    // Optional: a server from before 0.12 has no history, and the figures work without it.
    const since=trendState.rows.length?trendState.rows[trendState.rows.length-1][0]:Math.max(0,Date.now()/1000-trendState.span);
    try {mergeHistory(await api('GET','/system/resources/history?since='+since));} catch(error) {if(error.staleSession||error.status===401)throw error;}
    renderQueue(await api('GET','/queue'));
    state.paused=resource.paused;$('#queue-label').textContent=resource.paused?'Queue paused':'Shared queue';$('#pause').textContent=resource.paused?'Resume queue':'Pause queue';
    const page=state.page;
    const jobs=await api('GET',(state.me.role==='admin'?'/admin/jobs':'/jobs')+'?limit=50&offset='+page*50);
    if(page!==state.page){state.refreshPending=true;return;}
    const admin=state.me.role==='admin';
    if(admin&&(!state.names||jobs.some(job=>!state.names[job.owner_id]))){
      state.names=Object.fromEntries((await api('GET','/admin/users')).map(user=>[user.id,user.username]));
    }
    renderList($('#jobs-body'),jobs.map(job=>[job.id,job.spec.name,admin?state.names[job.owner_id]||null:null,job.display_state,job.priority,job.force_run,job.queue_ahead,job.spec.resources.cpus_per_task,job.spec.resources.memory_mb,job.created_at]),()=>{const rows=[];
    for(const job of jobs){
      const row=document.createElement('tr');row.className='clickable';row.dataset.state=job.state;
      // The whole row opens the job, unless the click was on a button or ends a text selection.
      row.addEventListener('click',event=>{if(event.target?.closest?.('button')||String(globalThis.getSelection?.()||''))return;safe(()=>detail(job.id));});
      const name=cell(row,job.spec.name,'job-name');
      const id=document.createElement('small');id.className='job-id';id.textContent=(admin?(state.names[job.owner_id]||'(deleted user)')+' · ':'')+job.id;name.append(id);
      const status=cell(row,'');const badge=document.createElement('span');badge.className='badge '+job.display_state.toLowerCase();badge.textContent=job.display_state;status.append(badge);const priority=document.createElement('small');priority.className='job-id';priority.textContent='Priority '+(job.priority??0)+(job.force_run?' · Force requested':'')+(typeof job.queue_ahead==='number'?' · '+ahead(job.queue_ahead):'');status.append(priority);
      cell(row,cores(job.spec.resources.cpus_per_task)+' · '+gb(job.spec.resources.memory_mb)+' GiB');cell(row,date(job.created_at));button(cell(row,''),'Open',()=>detail(job.id));rows.push(row);
    }
    if(!jobs.length){const row=document.createElement('tr');const empty=cell(row,'No jobs on this page.','empty');empty.colSpan=5;rows.push(row);}
    return rows;});
    filterJobs();
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
const STATE_FILTERS={active:['UPLOADING','PENDING','RUNNING'],finished:['DONE','FAILED','CANCELLED','LOST'],problem:['FAILED','CANCELLED','LOST']};
function filterJobs() {
  const words=($('#job-filter').value||'').toLowerCase().split(/\s+/).filter(Boolean);
  const states=STATE_FILTERS[$('#job-state-filter').value||''];
  for(const row of $('#jobs-body').children){
    if(!row.dataset?.state){row.hidden=false;continue;}
    const text=(row.textContent||'').toLowerCase();
    row.hidden=!words.every(word=>text.includes(word))||(states!==undefined&&!states.includes(row.dataset.state));
  }
}
$('#job-filter').addEventListener('input',filterJobs);
$('#job-state-filter').addEventListener('change',filterJobs);
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
async function detail(id,reset=true,{record=true}={}){
  if(!id)return;
  const request=++state.detailRequest;
  state.job=id;if(reset){state.browseRequest++;clearList($('#browser-body'));$('#browse-path').textContent='/';state.jobInfo=null;$('#job-controls').hidden=true;state.offset=0;$('#log').textContent='';$('#detail-name').textContent='Loading…';$('#detail-id').textContent=id;$('#detail-spec').textContent='';clearList($('#result-files'));panel('detail',{record,job:id});state.job=id;}
  const current=()=>state.job===id&&state.detailRequest===request;
  const job=await api('GET','/jobs/'+id);if(!current())return;state.jobInfo=job;
  if(reset&&job.state!=='UPLOADING')safe(()=>browse(id,''));
  $('#job-controls').hidden=state.me?.role!=='admin'||job.state!=='PENDING';
  if(document.activeElement!==$('#job-priority'))$('#job-priority').value=job.priority??0;
  $('#force-job').textContent=job.force_run?'Cancel force request':'Force run…';
  $('#force-job').disabled=$('#set-priority').disabled=state.queueBusy;
  setText($('#detail-name'),job.spec.name+' · '+job.display_state+(typeof job.queue_ahead==='number'?' · '+ahead(job.queue_ahead):''));setText($('#detail-id'),id);
  setText($('#detail-spec'),job.spec.command+'\n\n'+job.spec.resources.cpus_per_task+' CPU cores · '+gb(job.spec.resources.memory_mb)+' GiB · exit code '+(job.exit_code??'—')+(job.reason?'\n'+job.reason:''));
  $('#cancel-job').disabled=['DONE','FAILED','CANCELLED','LOST'].includes(job.state);
  $('#mark-lost').hidden=state.me?.role!=='admin'||job.state!=='RUNNING'||!String(job.reason||'').startsWith('NEEDS_RECHECK');
  const log=await api('GET',`/jobs/${id}/log?offset=${state.offset}&limit=65536`);if(!current())return;
  if(log.text){
    // Follow new output only while the reader is at the end; otherwise leave them where they are.
    const box=$('#log'),following=!(box.scrollHeight>box.clientHeight+box.scrollTop+8);
    box.textContent=(box.textContent+log.text).slice(-1000000);
    if(following&&typeof box.scrollHeight==='number')box.scrollTop=box.scrollHeight;
  }
  state.offset=log.next_offset;
  const files=['DONE','FAILED','CANCELLED','LOST'].includes(job.state)?await api('GET','/jobs/'+id+'/files'):[];
  if(!current())return;
  renderList($('#result-files'),[id,files],()=>files.map(entry=>{const row=document.createElement('li');const name=document.createElement('span');name.textContent=entry.path;const size=document.createElement('small');size.textContent=entry.size.toLocaleString()+' bytes';name.append(size);row.append(name);button(row,'Download',()=>download(id,entry));return row;}));
}
$('#refresh-detail').addEventListener('click',()=>safe(()=>detail(state.job,false)));
$('#cancel-job').addEventListener('click',()=>safe(async()=>{if(!confirm('Stop this job and its child processes?'))return;await api('POST','/jobs/'+state.job+'/cancel');await detail(state.job,false);}));
$('#mark-lost').addEventListener('click',()=>safe(async()=>{if(!confirm('The server can no longer follow this job, for example after a restart of the server PC. Marking it lost releases its CPU and memory reservation. If its calculation might still be running, check first. Mark it lost?'))return;await api('POST','/admin/jobs/'+state.job+'/mark-lost');await detail(state.job,false);}));
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
  // The folder browser has no manifest entry, so it checks the server's digest of this file.
  const expected=entry.sha256||response.headers.get('X-SHA256');
  if((entry.size!==undefined&&blob.size!==entry.size)||checksum!==expected)throw new Error('Downloaded checksum differs. Please retry.');
  saveBlob(blob,entry.path.split('/').pop());
}
function saveBlob(blob,name){
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function size(bytes){
  if(typeof bytes!=='number')return '';
  if(bytes<1024)return bytes+' B';
  const units=['KB','MB','GB','TB'];let value=bytes,unit=-1;
  while(value>=1024&&unit<units.length-1){value/=1024;unit++;}
  return value.toFixed(1)+' '+units[unit];
}
// One folder at a time. Paths come from the server's listing, and every name is shown as text.
async function browse(job,path,offset=0){
  const request=++state.browseRequest;
  const result=await api('GET','/browse?path='+encodeURIComponent(path?job+'/'+path:job)+'&offset='+offset+'&limit=500');
  if(request!==state.browseRequest||state.job!==job)return;
  state.browsePath=path;$('#browse-path').textContent='/'+path;$('#browse-up').disabled=!path;
  if(!offset)$('#browser-body').replaceChildren();
  for(const entry of result.entries){
    const row=document.createElement('tr'),relative=entry.path.slice(job.length+1);
    const name=cell(row,entry.type==='directory'?'':entry.name,'job-name');
    if(entry.type==='directory')button(name,entry.name+'/',()=>browse(job,relative)).className='folder-link';
    cell(row,size(entry.size));
    const action=cell(row,'');
    if(entry.type==='directory')button(action,'ZIP',()=>downloadFolder(job,relative));
    else button(action,'Download',()=>download(job,{path:relative}));
    $('#browser-body').append(row);
  }
  if(result.next_offset!==null){
    const row=document.createElement('tr'),more=cell(row,'');more.colSpan=3;
    button(more,'Load more',()=>{row.remove();return browse(job,path,result.next_offset);});
    $('#browser-body').append(row);
  }else if(!offset&&!result.entries.length){
    const row=document.createElement('tr'),empty=cell(row,'This folder is empty.','empty');empty.colSpan=3;$('#browser-body').append(row);
  }
}
async function downloadFolder(job,path){
  const generation=state.generation;
  if(!state.token||state.expires<Date.now()/1000+10)throw Object.assign(new Error('Sign in again to download results.'),{status:401});
  message('Preparing ZIP…');
  const response=await fetch('/api/v1/jobs/'+job+'/archive'+(path?'?path='+encodeURIComponent(path):''),{headers:{Authorization:'Bearer '+state.token},credentials:'omit',redirect:'error'});
  requireSession(generation);
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.message||'The folder could not be downloaded.');}
  const blob=await response.blob();requireSession(generation);
  saveBlob(blob,(path?path.split('/').pop():'job-'+job.slice(0,8))+'.zip');message('');
}
$('#browse-up').addEventListener('click',()=>safe(()=>browse(state.job,state.browsePath.split('/').slice(0,-1).join('/'))));
$('#browse-refresh').addEventListener('click',()=>safe(()=>browse(state.job,state.browsePath)));
$('#browse-zip').addEventListener('click',()=>safe(()=>downloadFolder(state.job,state.browsePath)));
async function accounts(){
  const request=++state.accountsRequest,generation=state.generation;
  const users=await api('GET','/admin/users');
  const devices=await api('GET','/admin/devices');
  if(request!==state.accountsRequest||generation!==state.generation)return;
  state.names=Object.fromEntries(users.map(user=>[user.id,user.username]));
  renderList($('#users-body'),[state.me.user_id,users.map(user=>[user.id,user.username,user.role,user.enabled])],()=>{const rows=[];
  for(const user of users){
    const own=user.id===state.me.user_id,row=document.createElement('tr');
    cell(row,user.username+(own?' (you)':''));cell(row,user.role==='admin'?'Administrator':'User');cell(row,user.enabled?'Enabled':'Disabled');
    const action=cell(row,'','row-actions');
    if(own)button(action,'Change my password',()=>panel('account'));
    else{
      const change=(text,path,body)=>async()=>{if(confirm(text)){await api('POST','/admin/users/'+user.id+path,body);await accounts();}};
      if(user.enabled)button(action,'Disable',change('Disable '+user.username+'? Their sign-ins and devices stop working at once. Their queued and running jobs are not cancelled.','/disable'));
      else button(action,'Enable',change('Enable '+user.username+'? Their password and devices start working again.','/enable'));
      button(action,'Reset password',()=>openReset(user));
      if(user.role==='admin')button(action,'Make user',change('Make '+user.username+' a regular user? They lose administration at once.','/role',{role:'user'}));
      else button(action,'Make administrator',change('Make '+user.username+' an administrator? Administrators can manage every user and job on this server.','/role',{role:'admin'}));
      button(action,'Delete',async()=>{
        const typed=prompt('Delete '+user.username+' permanently? Their sign-in and devices are removed and this cannot be undone. Finished jobs and files stay on the server under a deleted owner. To keep the account recoverable, disable it instead.\n\nType '+user.username+' to confirm:');
        if(typed===null)return;
        if(typed.trim()!==user.username){message('Not deleted: the name did not match.');return;}
        await api('DELETE','/admin/users/'+user.id);message('');await accounts();
      });
    }
    rows.push(row);
  }
  return rows;});
  renderList($('#devices-body'),[users,devices],()=>{const rows=[];
  for(const device of devices){const row=document.createElement('tr');cell(row,device.id);cell(row,users.find(u=>u.id===device.user_id)?.username||device.user_id);cell(row,date(device.last_access));const action=cell(row,'');if(device.revoked_at)action.textContent='Revoked';else button(action,'Revoke',async()=>{if(confirm('Revoke this device and its current tokens?')){await api('POST','/admin/devices/'+device.id+'/revoke');await accounts();}});rows.push(row);}
  return rows;});
}
function openReset(user){
  state.resetUser=user;clearPasswordForms();
  $('#reset-user').textContent='Set a new password for '+user.username+'. Their current sign-ins end. Give it to them over a private channel; they can change it afterwards.';
  $('#reset-dialog').showModal();
}
function closeReset(){state.resetUser=null;clearPasswordForms();if($('#reset-dialog').open)$('#reset-dialog').close();}
$('#reset-cancel').addEventListener('click',closeReset);
$('#reset-dialog').addEventListener('cancel',closeReset);
$('#reset-form').addEventListener('submit',event=>{
  event.preventDefault();const fields=event.currentTarget.elements,user=state.resetUser;
  safe(async()=>{
    if(!user)return;
    if(fields.new.value!==fields.repeat.value){$('#reset-status').textContent='The passwords differ.';return;}
    const body={new_password:fields.new.value,revoke_devices:fields.revoke.checked};
    await api('POST','/admin/users/'+user.id+'/password',body);
    closeReset();message('Password reset for '+user.username+'.');await accounts();
  });
});
async function accountPage(){
  const request=++state.accountRequest,me=await api('GET','/me');
  if(request!==state.accountRequest)return;
  $('#account-name').textContent=me.username+' · '+(me.role==='admin'?'Administrator':'User');
  $('#password-form').elements.username.value=me.username;
  $('#password-warning').hidden=location.protocol!=='http:'||['127.0.0.1','localhost','[::1]'].includes(location.hostname);
  $('#my-devices-body').replaceChildren();
  for(const device of me.devices){
    const row=document.createElement('tr');cell(row,device.id);cell(row,date(device.created_at));cell(row,date(device.last_access));
    const action=cell(row,'');
    if(device.revoked_at)action.textContent='Revoked';
    else button(action,'Revoke',async()=>{if(confirm('Revoke this device and its current tokens? It must register again.')){await api('POST','/me/devices/'+device.id+'/revoke');await accountPage();}});
    $('#my-devices-body').append(row);
  }
  if(!me.devices.length){const row=document.createElement('tr'),empty=cell(row,'No registered devices. Browser sign-in does not register one.','empty');empty.colSpan=4;$('#my-devices-body').append(row);}
}
$('#password-form').addEventListener('submit',event=>{
  event.preventDefault();const form=event.currentTarget,fields=form.elements,status=$('#password-status');
  safe(async()=>{
    status.textContent='';
    if(fields.new.value!==fields.repeat.value){status.textContent='The new passwords differ.';return;}
    if(fields.new.value===fields.current.value){status.textContent='Choose a password different from the current one.';return;}
    const username=fields.username.value;
    try{
      const result=await api('POST','/me/password',{current_password:fields.current.value,new_password:fields.new.value,revoke_devices:fields.revoke.checked});
      form.reset();fields.username.value=username;
      status.textContent='Password changed.'+(result.devices_revoked?' '+result.devices_revoked+' device(s) revoked.':'');
      await accountPage();
    }catch(error){
      fields.current.value='';status.textContent=error.message;
      if(error.status===401)throw error;
    }
  });
});
async function activity(){
  const request=++state.activityRequest,events=await api('GET','/admin/events?newest=100');
  if(request!==state.activityRequest)return;
  $('#activity-body').replaceChildren();
  for(const event of events){
    const row=document.createElement('tr');cell(row,date(event.timestamp));cell(row,event.event.replaceAll('_',' '));
    cell(row,event.actor?(state.names?.[event.actor]||'(deleted user)'):'local / system');
    let details={};try{details=JSON.parse(event.details);}catch{}
    if(event.job_id)details.job=event.job_id;
    if(state.names?.[details.user_id]){details.user=state.names[details.user_id];delete details.user_id;}
    cell(row,Object.entries(details).map(([key,value])=>key+': '+value).join(' · '));
    $('#activity-body').append(row);
  }
}
$('#activity').addEventListener('toggle',()=>{if($('#activity').open)safe(activity);});
$('#user-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;safe(async()=>{await api('POST','/admin/users',{username:form.elements.username.value,password:form.elements.password.value,role:form.elements.role.value});form.reset();await accounts();});});
setSignedIn(false);$('#login-submit').disabled=false;
safe(restoreSession);setInterval(()=>{if(!document.hidden)safe(refresh);},5000);
document.addEventListener?.('visibilitychange',()=>{if(!document.hidden)safe(refresh);});
