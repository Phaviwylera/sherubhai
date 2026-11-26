'use strict';
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name=studio-token]').content;
let firstPilotShown=false;
let state=null,doc=null,selected=0,dirty=false,displayRevision=0,approvalRun=null,growthDirty=false,growthId=null,providerDirty=false;
const shotFields=['line','speaker','listener','blocking','gaze','camera','action','hands','start','end','transition','seconds','dialogue_start','sfx','corrections'];
const settingsFields=['text_model','vision_model','asr_model','attempts','grade','seed','review_samples','negative_prompt'];
function tell(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,body){
 const options=body===undefined?{}:{method:'POST',headers:{'X-Studio-Token':token}};
 if(body instanceof FormData) options.body=body;
 else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);
 const result=await response.json();
 if(!response.ok)throw new Error((result.error||'The request did not complete.')+(result.diagnostic?' ['+result.diagnostic.code+' · '+result.diagnostic.id+']':''));
 return result;
}
function preserve(){
 if(!doc)return;
 for(const key of shotFields)doc.shots[selected][key]=['seconds','dialogue_start'].includes(key)?Number($(key).value):$(key).value;
 doc.shots[selected].sound_cues=JSON.parse($('sound_cues').value||'[]');
 for(const key of settingsFields)doc.settings[key]=['attempts','seed','review_samples'].includes(key)?Number($(key).value):$(key).value;
}
function changed(){dirty=true;$('save-status').textContent='Unsaved changes';}
for(const id of [...shotFields,...settingsFields,'sound_cues'])$(id).addEventListener('input',changed);
function media(name){return '/media/'+state.project+'/'+name;}
function showShot(index){
 if(doc&&dirty)preserve();
 selected=index;
 const shot=doc.shots[index];
 for(const key of shotFields)$(key).value=shot[key];
 $('sound_cues').value=JSON.stringify(shot.sound_cues,null,2);
 $('shot-counter').textContent=String(index+1).padStart(2,'0')+' / '+String(doc.shots.length).padStart(2,'0');
 $('shot-title').textContent=shot.speaker+' → '+shot.listener;
 $('shot-length').textContent=shot.seconds+' sec';
 $('asset-status').textContent=(shot.first_frame?'First frame ready. ':'First frame required for image-to-video. ')+(shot.imported_clip?'Imported footage ready. ':'')+(shot.voice?'Native voice recording ready. ':'')+shot.sound_cues.length+' sound cues.';
 $('reference').src=media(shot.first_frame||'reference.png')+'?r='+state.revision;
 $('reference').hidden=false;$('player').hidden=true;$('player').pause();
 $('frame-tag').textContent=shot.first_frame?'Shot first frame':'Character reference';
 document.querySelectorAll('.shot').forEach((element,i)=>element.classList.toggle('active',i===index));
}
function timeline(){
 $('timeline').replaceChildren();
 doc.shots.forEach((shot,index)=>{
  const button=document.createElement('button');button.className='shot'+(index===selected?' active':'');
  const number=document.createElement('small');number.textContent='SHOT '+String(index+1).padStart(2,'0');
  const speaker=document.createElement('b');speaker.textContent=shot.speaker+' → '+shot.listener;
  const timing=document.createElement('span');timing.textContent=shot.seconds+'s · '+(shot.first_frame?'Frame ready':'Awaiting frame');
  button.append(number,speaker,timing);button.onclick=()=>{try{showShot(index);}catch(error){tell(error.message);}};
  $('timeline').append(button);
 });
}
function formatted(parent,value){const pre=document.createElement('pre');pre.textContent=JSON.stringify(value,null,2);parent.append(pre);}
function showReports(){
 $('reports').replaceChildren();
 for(const run of state.runs.filter(item=>['render','illustrated'].includes(item.kind))){
  const section=document.createElement('article');section.className='report';
  const heading=document.createElement('div');heading.className='report-head';
  const label=document.createElement('b');label.textContent=(run.kind==='illustrated'?'Illustrated / hybrid episode ':'Render ')+run.id.slice(0,6)+' · '+run.state;
  const rev=document.createElement('span');rev.className='pill';rev.textContent='Revision '+run.revision;
  heading.append(label,rev);section.append(heading);
  if(run.output.export_file){const location=document.createElement('p');location.className='helper';location.textContent='Exported file: '+run.output.export_file;section.append(location);}
  if(run.output.master){
   const video=document.createElement('video');video.src=media(run.output.master);video.controls=true;video.preload='metadata';video.playsInline=true;section.append(video);
  }
  for(const shot of run.output.shots||[]){
   for(const [index,attempt] of shot.attempts.entries()){
    const details=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent='Shot '+(shot.index+1)+' · attempt '+(index+1)+' · '+(attempt.report?.status||'blocked');details.append(summary);
    const button=document.createElement('button');button.className='outline';button.textContent='Preview this attempt';
    button.onclick=()=>{document.querySelector('[data-tab=direct]').click();$('reference').hidden=true;$('player').hidden=false;$('player').src=media(attempt.clip);$('frame-tag').textContent='Shot '+(shot.index+1)+' · attempt '+(index+1);$('player').play().catch(()=>{});};
    if(attempt.clip)details.append(button);
    formatted(details,attempt.report||{error:attempt.error});
    if(attempt.generation)formatted(details,attempt.generation);
    if(attempt.audio_repairs?.length){const history=document.createElement('details'),label=document.createElement('summary');label.textContent='Automatic audio repair history';history.append(label);formatted(history,attempt.audio_repairs);details.append(history);}
    if(attempt.prompt){const contract=document.createElement('pre');contract.textContent=attempt.prompt;details.append(contract);}
    section.append(details);
   }
  }
  if(run.output.error){const error=document.createElement('p');error.textContent=run.output.error;section.append(error);}
  $('reports').append(section);
 }
 const current=state.runs.find(run=>run.state==='review'&&run.output.master&&run.revision===state.revision);
 if(approvalRun!==current?.id){document.querySelectorAll('#approval input').forEach(box=>box.checked=false);approvalRun=current?.id;}
 $('approve').disabled=!current;
}
let viewedRun=null;
function renderLog(){
 const detailOpen=$('diagnostic').querySelector('details')?.open||false;
 const previousScroll=$('events').scrollTop;
 const atBottom=$('events').scrollHeight-$('events').clientHeight-previousScroll<30;
 const run=state.runs.find(item=>item.id===viewedRun)||state.runs[0];
 const current=run?.revision===state.revision;
 const ready=state.document.shots.filter(shot=>shot.first_frame||shot.imported_clip).length;
 $('run-status').textContent=run?run.kind.replaceAll('_',' ')+' · '+(state.automation?.run===run.id&&state.automation?.state==='waiting'&&['quality','quota'].includes(run.output.retry_reason)?(run.output.retry_reason==='quota'?'waiting for provider allowance':'automatic repair queued'):run.state)+(current?'':' · earlier revision'):'Ready to direct';
 $('download-run').hidden=!run;
 if(run)$('download-run').href='/api/logs/run/'+run.id;
 const completed=(run?.output.shots||[]).filter(shot=>shot.attempts?.at(-1)?.report?.status&&shot.attempts.at(-1).report.status!=='fail').length;
 $('production-progress').textContent='Frames '+ready+'/'+state.document.shots.length+' ready'+(run?.kind==='render'?' · '+completed+'/'+state.document.shots.length+' shots processed':'')+(run&&!current?' · This log belongs to revision '+run.revision+'. Current revision is '+state.revision+'.':'');
 const quota=run?.output.quota||state.provider_wait?.quota||{};
 const reset=state.provider_wait?.until;
 $('quota-status').textContent=(quota.requested_gpu_seconds!==undefined?'Next request: '+quota.requested_gpu_seconds+' GPU-seconds; reported remaining: '+quota.remaining_gpu_seconds+' GPU-seconds. ':'')+(reset&&reset*1000>Date.now()?'Provider retry window ends '+new Date(reset*1000).toLocaleString()+'. ':'')+(completed?'Open completed clips to preview retained footage.':'');
 $('production-bar').value=run?.state==='review'||run?.state==='approved'?100:completed/state.document.shots.length*100;
 const error=run?.output.diagnostic;
 $('diagnostic').classList.toggle('repairing',error?.code==='AUTOMATIC_QUALITY_REPAIR'||(error?.code==='PROVIDER_LIMIT'&&state.automation?.state==='waiting'));
 $('diagnostic').replaceChildren();$('diagnostic').hidden=!error&&!run?.output.error;
 if(error){
  const title=document.createElement('b');title.textContent=error.code+' · '+error.summary;
  const next=document.createElement('p');next.textContent=error.next_step;
  const meta=document.createElement('p');meta.className='helper';meta.textContent='Error '+error.id+' · '+error.stage+' · '+error.at;
  const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Technical details';details.open=detailOpen;details.append(summary);formatted(details,error);
  $('diagnostic').append(title,next,meta,details);
  for(const number of error.affected_shots||[]){
   if(number<1||number>doc.shots.length)continue;
   const button=document.createElement('button');button.className='outline';button.textContent='Open shot '+number;button.onclick=()=>{document.querySelector('[data-tab=direct]').click();showShot(number-1);};$('diagnostic').append(button);
  }
 }else if(run?.output.error){$('diagnostic').textContent=run.output.error+' Earlier application versions did not record a traceback. Download this log for the recorded events.';}
 $('events').replaceChildren();
 const level=$('log-level').value,query=$('log-search').value.toLowerCase();
 for(const item of run?.events||[]){
  const severity=item.stage==='Needs attention'?'ERROR':item.level||'INFO';
  if(level!=='all'&&severity!==level||query&&!((item.stage+' '+item.message).toLowerCase().includes(query)))continue;
  const row=document.createElement('div');row.className='event '+severity.toLowerCase();
  const stamp=document.createElement('time');stamp.textContent=new Date(item.at*1000).toLocaleTimeString();
  const stage=document.createElement('span');stage.textContent=severity+' · '+item.stage;
  row.append(stamp,stage,document.createTextNode(item.message));$('events').append(row);
 }
 if(!run)$('events').textContent='Your production steps will appear here as they run.';
 $('events').scrollTop=atBottom?$('events').scrollHeight:previousScroll;
}
function activity(){
 const auto=state.automation;
 $('automation-status').textContent=auto.state.replaceAll('_',' ')+' · '+auto.message+(auto.retry_at?' Next retry '+new Date(auto.retry_at*1000).toLocaleString():'');
 $('automation-destinations').textContent='Cloud destinations: '+[state.provider.space,...(state.provider.auto_fallback?state.provider.catalog.filter(item=>item.space!==state.provider.space).map(item=>item.space):[])].join(', ');
 $('log-run').replaceChildren();
 for(const item of state.runs){const option=document.createElement('option');option.value=item.id;option.textContent=item.kind.replaceAll('_',' ')+' · '+(state.automation?.run===item.id&&state.automation?.state==='waiting'&&['quality','quota'].includes(item.output.retry_reason)?(item.output.retry_reason==='quota'?'waiting for provider allowance':'automatic repair queued'):item.state)+' · rev '+item.revision+' · '+item.id.slice(0,6);$('log-run').append(option);}
 if(viewedRun&&state.runs.some(item=>item.id===viewedRun))$('log-run').value=viewedRun;
 else viewedRun=state.runs[0]?.id;
 renderLog();
 const reviewRun=state.runs.find(item=>item.kind==='script_review'&&item.state==='done');
 $('script-result').replaceChildren();if(reviewRun)formatted($('script-result'),reviewRun.output);
 $('youtube-status').textContent=state.accounts.YouTube;
 $('instagram-status').textContent=state.accounts.Instagram;
 growthView();
 providerView();
 $('publisher-state').textContent=state.publishing?'Uploader running':'Uploader stopped';
 $('release-queue').replaceChildren();
 for(const release of state.queue){
  const row=document.createElement('p');
  row.className='helper';
  row.textContent=release.episode+' · '+release.platform+' · '+release.state+' · '+new Date(release.due*1000).toLocaleString()+(release.remote?' · '+release.remote:'')+(release.growth?' · Growth plan':'')+(release.refresh_status?' · '+release.refresh_status:'');
  const details=document.createElement('details'),summary=document.createElement('summary');
  summary.textContent='Publication copy';details.append(summary);formatted(details,release.publication_copy||{});
  if(release.diagnostic?.id){const failure=document.createElement('details'),label=document.createElement('summary');label.textContent='Upload error '+release.diagnostic.id;failure.append(label);formatted(failure,release.diagnostic);$('release-queue').append(failure);}
  $('release-queue').append(row,details);
 }
}
async function refresh(){
 const next=await api('/api/state');
 const active=next.runs.some(run=>['queued','running'].includes(run.state));
 // Keep video playback and open report details intact until run content changes.
 const reportChanged=JSON.stringify(state?.runs)!==JSON.stringify(next.runs);
 state=next;
 if(!doc||(!dirty&&displayRevision!==state.revision)){
  doc=structuredClone(state.document);displayRevision=state.revision;
  selected=Math.min(selected,doc.shots.length-1);
  $('title').textContent=doc.title;
  $('total-duration').replaceChildren(document.createTextNode(String(doc.shots.reduce((n,s)=>n+s.seconds,0))));
  const suffix=document.createElement('span');suffix.textContent='s';$('total-duration').append(suffix);
  for(const key of settingsFields)$(key).value=doc.settings[key];
  timeline();showShot(selected);
 }
 if(!dirty)$('save-status').textContent='Revision '+state.revision+' · saved locally';
 activity();
 if(reportChanged)showReports();
 if(!firstPilotShown&&new URLSearchParams(location.search).get('first')==='1'&&state.runs.some(run=>run.kind==='illustrated'&&run.state==='review'&&run.revision===state.revision)){firstPilotShown=true;document.querySelector('[data-tab=review]').click();}
 for(const id of ['render','render-shot','write','script-review','install-vision','research-growth','test-provider','start-auto','resume-render','recheck-clips','finish-illustrated','refresh-quota'])$(id).disabled=active;
}
async function save(){
 preserve();
 const result=await api('/api/save',{document:doc,revision:state.revision});
 dirty=false;displayRevision=0;await refresh();return result;
}
async function execute(fn){
 try{tell('');await fn();}catch(error){tell(error.message);}
}
$('save').onclick=()=>execute(save);
for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{
 document.querySelectorAll('.page').forEach(page=>page.hidden=page.id!==button.dataset.tab);
 document.querySelectorAll('[data-tab]').forEach(item=>item.classList.toggle('active',item===button));
};
async function task(kind,shot=null){
 if(dirty)await save();
 const result=await api('/api/task',{kind,shot,idea:$('idea').value});viewedRun=result.run;await refresh();
}
$('render').onclick=()=>execute(()=>task('render'));
$('render-shot').onclick=()=>execute(()=>task('render',selected));
$('write').onclick=()=>execute(()=>task('write'));
$('script-review').onclick=()=>execute(()=>task('script_review'));
$('install-vision').onclick=()=>execute(()=>task('visual_model'));
for(const [id,kind]of[['reference-upload','reference'],['first-frame-upload','first_frame'],['sfx-upload','sfx'],['voice-upload','voice'],['ambience-upload','ambience'],['music-upload','music'],['clip-upload','clip']]){
 $(id).onchange=()=>execute(async()=>{
  const file=$(id).files[0];if(!file)return;
  if(dirty)await save();
  const form=new FormData();form.append('file',file);form.append('kind',kind);form.append('index',String(selected));
  await api('/api/upload',form);$(id).value='';displayRevision=0;await refresh();
 });
}
for(const button of document.querySelectorAll('[data-account]'))button.onclick=()=>execute(async()=>{
 await api('/api/account/'+button.dataset.account,{});
 tell('The account window is opening on this computer. Complete authorization there, then return to Studio.');
});
$('prompt-button').onclick=()=>execute(async()=>{
 if(dirty)await save();
 $('prompt-content').textContent=state.prompts[selected];$('prompt-dialog').showModal();
});
$('close-dialog').onclick=()=>$('prompt-dialog').close();
$('copy-prompt').onclick=()=>execute(async()=>{
 await navigator.clipboard.writeText($('prompt-content').textContent);$('copy-prompt').textContent='Copied';
 setTimeout(()=>$('copy-prompt').textContent='Copy prompt',1500);
});
$('approve').onclick=()=>execute(async()=>{
 if(dirty)throw new Error('Save changes and render the updated episode before approval.');
 await api('/api/approve',{run:approvalRun,checks:[...document.querySelectorAll('#approval input')].map(box=>box.checked)});
 await refresh();tell('Master approved for export. Publishing still uses the connected-account release queue.');
});
execute(refresh);
setInterval(()=>refresh().catch(error=>tell(error.message)),3000);

$('queue-release').onclick=()=>execute(async()=>{
 if(dirty)throw new Error('Save changes, render and approve the current master before scheduling.');
 if(growthDirty&&$('use-growth').checked)throw new Error('Save the edited publication copy before scheduling.');
 await api('/api/queue',{episode:$('episode-id').value,when:$('release-time').value,url:$('release-url').value,use_growth:$('use-growth').checked,auto_refresh:$('refresh-growth').checked});
 await refresh();tell('Both releases are queued. Start the uploader and keep Studio open.');
});
$('start-publisher').onclick=()=>execute(async()=>{
 await api('/api/publisher',{});await refresh();
});

const growthInputs=['growth-yt-title','growth-yt-description','growth-yt-hashtags','growth-yt-tags','growth-ig-caption','growth-ig-hashtags'];
for(const id of growthInputs)$(id).addEventListener('input',()=>{growthDirty=true;});
function splitTags(value,comma=false){return value.split(comma?',':/\s+/).map(x=>x.trim()).filter(Boolean);}
function growthView(){
 const record=state.growth;
 $('growth-editor').hidden=!record;
 if(!record){growthId=null;growthDirty=false;$('growth-status').textContent='Run research for this episode revision to see current evidence.';return;}
 const plan=record.plan;
 const stamp=new Date(plan.checked_at);
 $('growth-status').textContent='Research checked '+stamp.toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})+' IST · '+record.snapshot.evidence.length+' sampled sources';
 $('growth-warnings').replaceChildren();
 for(const warning of record.snapshot.warnings){
  const row=document.createElement('p');row.className='helper';row.textContent=warning;$('growth-warnings').append(row);
 }
 if(growthId!==record.id||!growthDirty){
  $('growth-yt-title').value=plan.youtube.title;
  $('growth-yt-description').value=plan.youtube.description;
  $('growth-yt-hashtags').value=plan.youtube.hashtags.join(' ');
  $('growth-yt-tags').value=plan.youtube.tags.join(', ');
  $('growth-ig-caption').value=plan.instagram.caption;
  $('growth-ig-hashtags').value=plan.instagram.hashtags.join(' ');
  growthId=record.id;growthDirty=false;
 }
 $('growth-times').replaceChildren();
 for(const platform of ['youtube','instagram']){
  const slot=plan.schedule[platform],row=document.createElement('p');row.className='helper';
  row.textContent=platform+' · '+new Date(slot.when).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})+' IST · '+slot.confidence+' — '+slot.basis;
  $('growth-times').append(row);
 }
 $('growth-reasoning').replaceChildren();
 for(const value of [plan.reasoning,plan.hook_note,...plan.title_variants]){
  const row=document.createElement('p');row.className='helper';row.textContent=value;$('growth-reasoning').append(row);
 }
 $('growth-evidence').replaceChildren();
 const keywords=document.createElement('p');keywords.className='helper';keywords.textContent='Related terms in this sample: '+(record.snapshot.candidate_keywords||[]).map(item=>item.keyword+' ('+item.sample_mentions+' mentions)').join(', ');$('growth-evidence').append(keywords);
 for(const source of record.snapshot.evidence){
  const row=document.createElement('article');row.className='evidence';
  const title=document.createElement('a');title.textContent=source.title;
  try{const url=new URL(source.url);if(url.protocol==='https:'){title.href=url.href;title.target='_blank';title.rel='noopener noreferrer';}}catch{}
  const meta=document.createElement('p');meta.className='helper';
  meta.textContent=source.source+(source.views!==undefined?' · '+source.views.toLocaleString()+' observed views':'')+(source.observed_view_change_per_hour!==undefined?' · '+source.observed_view_change_per_hour+' views/hour observed between checks':'')+' · '+source.meaning+(source.hashtags?.length?' · Source hashtags: '+source.hashtags.join(' '):'');
  row.append(title,meta);$('growth-evidence').append(row);
 }
}
$('research-growth').onclick=()=>execute(()=>task('growth'));
$('save-growth').onclick=()=>execute(async()=>{
 if(!growthId)throw new Error('Run research first.');
 const youtube={...state.growth.plan.youtube,title:$('growth-yt-title').value,description:$('growth-yt-description').value,hashtags:splitTags($('growth-yt-hashtags').value),tags:splitTags($('growth-yt-tags').value,true)};
 const instagram={...state.growth.plan.instagram,caption:$('growth-ig-caption').value,hashtags:splitTags($('growth-ig-hashtags').value)};
 await api('/api/growth/save',{id:growthId,youtube,instagram});growthDirty=false;await refresh();tell('Publication copy saved for this episode.');
});
$('analytics-upload').onchange=()=>execute(async()=>{
 const file=$('analytics-upload').files[0];if(!file)return;
 const form=new FormData();form.append('file',file);
 const result=await api('/api/growth/metrics',form);
 $('analytics-upload').value='';tell(result.rows+' analytics rows imported. Run research again to update timing suggestions.');
});

for(const id of ['provider-space','provider-endpoint','provider-token','provider-auth','provider-fallback']){
 $(id).addEventListener('input',()=>{providerDirty=true;if(id==='provider-token'&&$(id).value)$('provider-auth').checked=true;});
}
function providerView(){
 const config=state.provider;
 const live=state.provider_wait?.live_quota;
 $('live-quota').textContent=live?.status==='checked'?'Live account quota: '+live.remaining_gpu_seconds+' / '+live.base_gpu_seconds+' GPU-seconds · checked '+new Date(live.checked_at*1000).toLocaleString():live?.note||'Quota figures shown from old errors are saved evidence, not a live account reading.';
 if(!config)return;
 if(!providerDirty){
  $('provider-space').value=config.space;
  $('provider-endpoint').value=config.endpoint;
  $('provider-auth').checked=config.use_auth;
  $('provider-fallback').checked=config.auto_fallback;
 }
 $('provider-catalog').textContent=config.catalog.map(item=>item.label+' · '+item.space+' '+item.endpoint).join(' | ');
 $('provider-page').href='https://huggingface.co/spaces/'+config.space;
 $('provider-result').replaceChildren();
 if(providerDirty){$('provider-result').textContent='Provider settings have unsaved changes. Save & test to verify them.';return;}
 if(config.last_check){
  const result=config.last_check;
  const line=document.createElement('p');
  line.textContent='API checked · '+result.stage+' · '+result.endpoint+' · '+new Date(result.checked_at*1000).toLocaleString();
  $('provider-result').append(line);
  const missing=state.document.shots.map((shot,index)=>!shot.first_frame&&!shot.imported_clip?index+1:null).filter(value=>value!==null);
  const frameNote=document.createElement('p');
  frameNote.textContent=result.capabilities.image_parameter&&missing.length?'First-frame images required for shots '+missing.join(', ')+'. Select a shot → Sound & first frame → First frame.':'Shot image inputs are ready.';
  $('provider-result').append(frameNote);
  formatted($('provider-result'),result);
 }else{
  $('provider-result').textContent='Provider has not been tested with the current settings.';
 }
}
$('test-provider').onclick=()=>execute(async()=>{
 await api('/api/provider/save',{space:$('provider-space').value,endpoint:$('provider-endpoint').value,token:$('provider-token').value,use_auth:$('provider-auth').checked,auto_fallback:$('provider-fallback').checked});
 $('provider-token').value='';providerDirty=false;
 await refresh();await task('provider_check');
});

$('log-run').onchange=()=>{viewedRun=$('log-run').value;renderLog();};
$('log-level').onchange=renderLog;
$('log-search').oninput=renderLog;

$('start-auto').onclick=()=>execute(async()=>{
 if(dirty)await save();
 if(providerDirty)throw new Error('Save provider changes first so the displayed destinations match the saved routing.');
 const result=await api('/api/automation/start',{});viewedRun=result.run;await refresh();
});
$('pause-auto').onclick=()=>execute(async()=>{await api('/api/automation/pause',{});await refresh();});
$('resume-render').onclick=()=>execute(async()=>{
 if(dirty)throw new Error('Save script changes first. Changed contracts require a new render.');
 const run=state.runs.find(item=>item.id===viewedRun)||state.runs[0];
 if(!run)throw new Error('Select a stopped render in the activity log.');
 await api('/api/retry/'+run.id,{});await refresh();
});

$('open-clips').onclick=()=>document.querySelector('[data-tab=review]').click();
$('recheck-clips').onclick=()=>execute(async()=>{
 if(dirty)throw new Error('Save changes first. Recheck uses a current-revision render.');
 const run=state.runs.find(item=>item.id===viewedRun)||state.runs[0];
 if(!run)throw new Error('Select the stopped render to recheck.');
 const result=await api('/api/recheck/'+run.id,{});viewedRun=result.run;await refresh();
});

$('finish-illustrated').onclick=()=>execute(()=>task('illustrated'));

$('refresh-quota').onclick=()=>execute(()=>task('quota_check'));
