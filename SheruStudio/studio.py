"""Local production studio. Run through Studio.cmd after setup."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.exceptions import HTTPException
import diagnostics
from PIL import Image
from direction import check, enrich, prompt, writer_prompt
from finishing import model_json, review, generate, dub_and_finish, make_voice, safe_file
from sheru_studio import assemble

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('SHERU_DATA') or ROOT/'studio-data')
DATA.mkdir(parents=True,exist_ok=True)
# Hold an operating-system lock for the lifetime of this Studio process.
# A second launch opens the existing UI before it can alter job state.
studio_lock=(DATA/'.studio.lock').open('a+b')
if studio_lock.seek(0,2)==0:
    studio_lock.write(b'0')
    studio_lock.flush()
studio_lock.seek(0)
try:
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(studio_lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(studio_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError:
    import webbrowser
    webbrowser.open('http://127.0.0.1:'+os.getenv('SHERU_PORT','8765'))
    raise SystemExit('Sheru Studio is already running. Open its existing browser window.')
TOKEN=secrets.token_urlsafe(32)
PORT=int(os.getenv('SHERU_PORT','8765'))
app=Flask(__name__,static_folder=str(ROOT/'web'),static_url_path='/web')
app.config['MAX_CONTENT_LENGTH']=300*1024*1024
executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)
mutation=threading.RLock()
publishing=False

def connect():
    connection=sqlite3.connect(DATA/'studio.sqlite',timeout=15)
    connection.row_factory=sqlite3.Row
    return connection

with connect() as connection:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, doc TEXT NOT NULL, revision INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project TEXT, revision INTEGER, kind TEXT, state TEXT, output TEXT);
    CREATE TABLE IF NOT EXISTS growth_plans(id TEXT PRIMARY KEY, project TEXT, revision INTEGER, checked REAL, snapshot TEXT, plan TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT, stage TEXT, message TEXT, at REAL);
    CREATE INDEX IF NOT EXISTS events_run ON events(run,id);
    UPDATE runs SET state='interrupted' WHERE state IN ('queued','running');
    """)
    columns={row[1] for row in connection.execute('PRAGMA table_info(events)')}
    for name,definition in [('level',"TEXT DEFAULT 'INFO'"),('details',"TEXT DEFAULT '{}'")]:
        if name not in columns:connection.execute('ALTER TABLE events ADD COLUMN '+name+' '+definition)

def document(pid):
    with connect() as connection:
        row=connection.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
    if not row: raise ValueError('Project does not exist.')
    return enrich(json.loads(row['doc'])),row['revision']

def project_dir(pid):
    document(pid)
    return DATA/pid

class Busy(ValueError):
    """Another local task owns this episode. Automatic retries recheck on the next tick."""

def idle(pid):
    with connect() as connection:
        if connection.execute("SELECT 1 FROM runs WHERE project=? AND state IN ('queued','running')",(pid,)).fetchone():
            raise Busy('This episode already has an active task. Wait for it to finish.')

def save_doc(pid,doc,revision):
    with connect() as connection:
        row=connection.execute('UPDATE projects SET doc=?,revision=revision+1 WHERE id=? AND revision=?',(json.dumps(doc,ensure_ascii=False),pid,revision))
        if row.rowcount!=1: raise ValueError('Episode changed in another window. Refresh before saving.')

def event(rid,stage,message,level='INFO',**details):
    diagnostics.set_context(run=rid,stage=stage)
    entry=diagnostics.record(level,message,**details)
    with connect() as connection:
        connection.execute('INSERT INTO events(run,stage,message,at,level,details) VALUES(?,?,?,?,?,?)',(rid,stage,entry['message'],time.time(),level,json.dumps(entry['details'],ensure_ascii=False)))

def update_run(rid,state,output):
    with connect() as connection:
        connection.execute('UPDATE runs SET state=?,output=? WHERE id=?',(state,json.dumps(output,ensure_ascii=False),rid))

def new_project():
    pid=uuid.uuid4().hex
    folder=DATA/pid
    folder.mkdir()
    shutil.copy2(ROOT/'assets'/'sheru-reference.png',folder/'reference.png')
    doc=enrich(json.loads((ROOT/'episode01.json').read_text(encoding='utf8')))
    from pilot_assets import apply,apply_voices
    apply(doc,folder)
    apply_voices(doc,folder)
    with connect() as connection:
        connection.execute('INSERT INTO projects VALUES(?,?,1)',(pid,json.dumps(doc,ensure_ascii=False)))
    return pid

with connect() as connection:
    first=connection.execute('SELECT id FROM projects LIMIT 1').fetchone()
PID=first['id'] if first else new_project()
from pilot_assets import apply as apply_pilot_frames
migration_doc,migration_revision=document(PID)
migrated=apply_pilot_frames(migration_doc,project_dir(PID))
from pilot_assets import apply_voices
migrated=sorted(set(migrated+apply_voices(migration_doc,project_dir(PID))))
if migrated:
    save_doc(PID,migration_doc,migration_revision)
    diagnostics.record('INFO','Assigned bundled pilot first frames.',project=PID,shots=migrated)


# Old builds treated HH:MM:SS waits as GPU-second numbers. Correct them using
# timestamped evidence retained in the run, without claiming a live quota query.
with connect() as connection:
    quota_history=[dict(row) for row in connection.execute("SELECT id,output FROM runs WHERE project=? ORDER BY rowid DESC LIMIT 30",(PID,))]
for item in quota_history:item['output']=json.loads(item['output'])
__import__('provider_pool').repair_legacy_wait(quota_history)

@app.before_request
def local_only():
    if request.host not in (f'127.0.0.1:{PORT}',f'localhost:{PORT}'):
        return jsonify(error='Use the local studio address.'),403
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('X-Studio-Token')!=TOKEN:
        return jsonify(error='Reload Studio before trying again.'),403

@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; media-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    return response

@app.errorhandler(ValueError)
def value_error(error):
    diagnostics.context.set({'stage':'API '+request.path,'project':PID})
    detail=diagnostics.capture(error)
    return jsonify(error=detail['message'],diagnostic=detail),400

@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error,HTTPException):return error
    diagnostics.context.set({'stage':'API '+request.path,'project':PID})
    detail=diagnostics.capture(error)
    return jsonify(error=detail['summary']+' Error ID '+detail['id'],diagnostic=detail),500

@app.get('/api/logs/run/<rid>')
def download_run_log(rid):
    with connect() as connection:
        row=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(rid,PID)).fetchone()
        if not row:return '',404
        run=dict(row);run['output']=json.loads(run['output'])
        events=[dict(item) for item in connection.execute('SELECT stage,message,at,level FROM events WHERE run=? ORDER BY id',(rid,))]
    return Response(diagnostics.run_report(rid,run,events),mimetype='text/plain',headers={'Content-Disposition':'attachment; filename="sheru-run-'+rid+'.txt"'})

@app.get('/api/logs/application')
def download_app_log():
    path=diagnostics.LOGS/'studio.log'
    content=path.read_text(encoding='utf8',errors='replace') if path.exists() else 'No application events recorded yet.'
    return Response(diagnostics.redact(content),mimetype='text/plain',headers={'Content-Disposition':'attachment; filename="sheru-studio.log"'})

@app.errorhandler(413)
def too_large(error):
    return jsonify(error='Upload is larger than 300 MB.'),413

@app.get('/')
def index():
    return (ROOT/'web'/'index.html').read_text(encoding='utf8').replace('__TOKEN__',TOKEN)

ACCOUNT_CACHE={'at':0.0,'value':None}
def account_status():
    """Credential-vault reads are slow, so the UI poll reuses them briefly."""
    if ACCOUNT_CACHE['value'] is not None and time.time()-ACCOUNT_CACHE['at']<30:return ACCOUNT_CACHE['value']
    accounts={}
    try:
        from accounts import stored
        for provider,key in [('YouTube','youtube'),('Instagram','instagram')]:
            data=stored(key)
            accounts[provider]='Reconnect' if data and data.get('expires',float('inf'))<=time.time() else 'Connected' if data else 'Connect'
    except Exception: accounts={'YouTube':'Connect','Instagram':'Connect'}
    ACCOUNT_CACHE.update(at=time.time(),value=accounts)
    return accounts

@app.get('/api/state')
def state():
    doc,revision=document(PID)
    with connect() as connection:
        runs=[dict(row) for row in connection.execute('SELECT * FROM runs WHERE project=? ORDER BY rowid DESC LIMIT 12',(PID,))]
        for item in runs:
            item['output']=json.loads(item['output'])
            if not item['output'].get('quota'):
                from provider import quota_details
                for cause in item['output'].get('diagnostic',{}).get('chain',[]):
                    values=quota_details(cause.get('message',''))
                    if values:item['output']['quota']=values;break
            item['events']=[dict(row) for row in connection.execute('SELECT stage,message,at,level,details FROM (SELECT * FROM events WHERE run=? ORDER BY id DESC LIMIT 500) ORDER BY id',(item['id'],))]
    with connect() as connection:
        growth_row=connection.execute('SELECT * FROM growth_plans WHERE project=? AND revision=? ORDER BY rowid DESC LIMIT 1',(PID,revision)).fetchone()
    growth_plan=dict(growth_row) if growth_row else None
    if growth_plan:
        growth_plan['plan']=json.loads(growth_plan['plan'])
        growth_plan['snapshot']=json.loads(growth_plan['snapshot'])
    accounts=account_status()
    from releases import read_db
    with read_db() as connection:
        queue=[dict(row) for row in connection.execute('SELECT episode,platform,title,due,state,remote,metadata,last_error FROM jobs ORDER BY due DESC LIMIT 30')]
        for release in queue:
            release['diagnostic']=json.loads(release.pop('last_error'))
            metadata=json.loads(release.pop('metadata'))
            release['refresh_status']=metadata.get('refresh_status','')
            release['growth']=bool(metadata.get('growth_context'))
            release['publication_copy']={key:metadata[key] for key in ('title','description','caption','hashtags','tags','researched_at','timing_note','research_warnings','research_coverage') if key in metadata}
    from quality import render_prompt,controls
    generation_settings=controls(doc['settings'])
    return jsonify(automation=__import__('automation').state(),project=PID,document=doc,revision=revision,runs=runs,accounts=accounts,provider_wait=__import__('provider_pool').cooldown(),provider=__import__('provider').public_config(),growth=growth_plan,publishing=publishing,queue=queue,prompts=[prompt(doc,i)+'\n\nCOMPILED VISUAL PROMPT\n'+render_prompt(doc,i)+'\n\nNEGATIVE PROMPT (when supported by the endpoint)\n'+generation_settings['negative_prompt'] for i in range(len(doc['shots']))])

@app.post('/api/save')
def save():
    data=request.get_json()
    with mutation:
        idle(PID)
        doc=check(data['document'])
        folder=project_dir(PID)
        for shot in doc['shots']:
            for name in [shot[key] for key in ('first_frame','voice','imported_clip') if shot.get(key)]+[c['file'] for c in shot['sound_cues']]:
                safe_file(folder,name)
        for name in doc.get('mix',{}).values():
            safe_file(folder,name)
        save_doc(PID,doc,int(data['revision']))
    return jsonify(ok=True)

@app.post('/api/upload')
def upload():
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        kind=request.form.get('kind')
        index=int(request.form.get('index','0'))
        if kind not in ('reference','first_frame','sfx','voice','ambience','music','clip') or not 0<=index<len(doc['shots']):
            raise ValueError('Invalid upload destination.')
        asset=request.files.get('file')
        if not asset: raise ValueError('Choose a media file.')
        suffix=Path(asset.filename).suffix.lower()
        allowed=('.png','.jpg','.jpeg','.webp') if kind in ('reference','first_frame') else ('.wav','.mp3','.m4a') if kind in ('sfx','voice','ambience','music') else ('.mp4','.mov','.webm')
        if suffix not in allowed: raise ValueError('Unsupported media type.')
        folder=project_dir(PID)
        name='asset-'+uuid.uuid4().hex+suffix
        dest=folder/name
        asset.save(dest)
        try:
            if kind in ('reference','first_frame'):
                with Image.open(dest) as image:
                    if image.width*image.height>40_000_000: raise ValueError('Image exceeds 40 megapixels.')
                    if kind=='first_frame' and abs(image.width/image.height-9/16)>.01:
                        raise ValueError('First frames must be composed in 9:16. Crop the image before uploading.')
                    image.convert('RGB').save(dest.with_suffix('.png'))
                dest.unlink(missing_ok=True) if suffix!='.png' else None
                name=dest.with_suffix('.png').name
                if kind=='reference':
                    shutil.copy2(folder/name,folder/'reference.png')
                else: doc['shots'][index]['first_frame']=name
            elif kind in ('sfx','voice','ambience','music'):
                from finishing import probe
                if not any(s['codec_type']=='audio' for s in probe(dest)['streams']): raise ValueError('No audio track found.')
                if kind=='sfx': doc['shots'][index]['sound_cues'].append(dict(file=name,start=0,gain_db=-12))
                elif kind=='voice': doc['shots'][index]['voice']=name
                else: doc.setdefault('mix',{})[kind]=name
            else:
                from finishing import probe
                if not any(s['codec_type']=='video' for s in probe(dest)['streams']): raise ValueError('No video track found.')
                doc['shots'][index]['imported_clip']=name
            save_doc(PID,check(doc),revision)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    return jsonify(ok=True)

@app.get('/media/<pid>/<path:name>')
def media(pid,name):
    folder=project_dir(pid)
    # Only project-owned media and previews can be served.
    if not name.lower().endswith(('.png','.jpg','.mp4','.wav','.mp3','.m4a','.webm','.mov')):
        return '',404
    return send_from_directory(folder,name,conditional=True)


@app.post('/api/provider/save')
def save_provider():
    from provider import save_config
    data=request.get_json()
    with mutation:
        idle(PID)
        result=save_config(str(data.get('space','')),str(data.get('endpoint','')),str(data.get('token','')),bool(data.get('use_auth')),bool(data.get('auto_fallback',True)))
    return jsonify(ok=True,provider=result)

@app.post('/api/account/<provider>')
def account(provider):
    if provider not in ('youtube','instagram'): raise ValueError('Unknown account provider.')
    subprocess.Popen([sys.executable,str(ROOT/'accounts.py'),'--platform',provider],cwd=ROOT)
    return jsonify(ok=True)

@app.post('/api/task')
def task():
    data=request.get_json()
    kind=data.get('kind')
    if kind not in ('write','script_review','render','visual_model','growth','provider_check','quota_check','illustrated'): raise ValueError('Unknown production task.')
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        if kind=='render':
            from quality import preflight
            preflight(doc)
        selected=data.get('shot')
        if selected is not None and (type(selected) is not int or not 0<=selected<len(doc['shots'])):
            raise ValueError('Choose a valid shot.')
        if kind=='illustrated':
            import automation
            automation.save(enabled=False,state='paused',retry_at=None,message='Cloud-video retries paused while the illustrated/hybrid episode is completed.')
        rid=start_task(kind,doc,revision,selected,str(data.get('idea',''))[:4000],automatic=(kind=='render' and selected is None))
    return jsonify(ok=True,run=rid)

def start_task(kind,doc,revision,selected=None,idea='',automatic=False):
    rid=uuid.uuid4().hex
    folder=project_dir(PID)/'runs'/rid
    folder.mkdir(parents=True)
    shutil.copy2(project_dir(PID)/'reference.png',folder/'reference.png')
    (folder/'script.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf8')
    resume_data=None
    if kind=='render':
        recovered={}
        with connect() as connection:
            prior=connection.execute("SELECT output FROM runs WHERE project=? AND revision=? AND kind='render' AND state IN ('blocked','interrupted') ORDER BY rowid DESC LIMIT 20",(PID,revision)).fetchall()
        for row in prior:
            for index,entry in recoverable(json.loads(row['output']),selected).items():
                recovered.setdefault(index,entry)
        if recovered:resume_data={'shots':[recovered[i] for i in sorted(recovered)],'selected':selected}
    with connect() as connection:
        connection.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,PID,revision,kind,'queued',json.dumps(resume_data or {},ensure_ascii=False)))
    if automatic:
        import automation
        automation.save(enabled=True,state='running',run=rid,project=PID,revision=revision,retry_at=None,retry_count=0,message='Automatic production is running.')
    executor.submit(work,rid,doc,revision,kind,selected,idea,resume_data)
    return rid

def newest_clip(entry):
    """The newest attempt that produced footage; error records carry no clip."""
    return next((attempt for attempt in reversed(entry.get('attempts') or []) if attempt.get('clip')),None)

def checkpoints(output,completed=False):
    """Entries whose newest clip is still on disk and unmodified since it was reviewed."""
    kept={};rejected=set()
    for entry in output.get('shots',[]) or []:
        attempts=entry.get('attempts') or []
        if completed and (not attempts or attempts[-1].get('report',{}).get('status') not in ('review','pass')):continue
        newest=newest_clip(entry)
        clip=newest.get('clip') if newest else None
        if not clip:continue
        try:
            path=safe_file(project_dir(PID),clip)
        except ValueError:
            rejected.add(entry.get('index'));continue
        if entry.get('clip_sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=entry['clip_sha256']:
            rejected.add(entry.get('index'));continue
        kept[entry.get('index')]=entry
    return kept,rejected

def resumable(run,doc):
    return checkpoints(run.get('output',{}),completed=True)[0]

def recoverable(output,selected=None):
    """Failed candidates are retained for local repair, but only while their file is intact."""
    kept,_=checkpoints(output)
    return {index:entry for index,entry in kept.items() if selected is None or index==selected}

@app.post('/api/automation/start')
def start_automation():
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        from quality import preflight
        preflight(doc)
        rid=start_task('render',doc,revision,automatic=True)
    return jsonify(ok=True,run=rid)

@app.post('/api/automation/pause')
def pause_automation():
    import automation
    automation.save(enabled=False,state='paused',retry_at=None,message='Automatic retries paused. An already submitted task finishes and retains its result.')
    return jsonify(ok=True)

@app.post('/api/recheck/<rid>')
def recheck_clips(rid):
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        with connect() as connection:
            row=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(rid,PID)).fetchone()
        if not row or row['kind']!='render' or row['revision']!=revision:raise ValueError('Select a render from the current episode revision.')
        output=json.loads(row['output'])
        if not any(attempt.get('clip') for entry in output.get('shots',[]) for attempt in entry.get('attempts',[])):raise ValueError('This run has no generated clips to recheck.')
        new_rid=start_task('recheck',doc,revision,idea=rid)
    return jsonify(ok=True,run=new_rid)

@app.post('/api/retry/<rid>')
def retry_run(rid):
    with mutation:
        idle(PID)
        with connect() as connection:
            row=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(rid,PID)).fetchone()
        if not row or row['kind']!='render' or row['state'] not in ('blocked','interrupted'):raise ValueError('Choose a blocked or interrupted render to resume.')
        run=dict(row);run['output']=json.loads(run['output'])
        doc,revision=document(PID)
        if revision!=run['revision']:raise ValueError('The script has changed. Start a new render so older clips cannot be mixed with a changed contract.')
        retry_at=run['output'].get('retry_not_before',0)
        from local_wan import enabled as local_wan_ready
        if local_wan_ready():retry_at=0
        if retry_at>time.time() and run['output'].get('retry_reason')=='quota':
            from provider_pool import refresh_quota,cooldown
            refresh_quota()
            if cooldown().get('wait_status')=='cleared_by_live_allowance':retry_at=0
        if retry_at>time.time():raise ValueError('The provider retry window has not elapsed. Automatic production will wait until '+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(retry_at))+'.')
        snapshot=json.loads((project_dir(PID)/'runs'/rid/'script.json').read_text(encoding='utf8'))
        update_run(rid,'queued',run['output'])
        executor.submit(work,rid,snapshot,revision,'render',run['output'].get('selected'),'',run['output'])
    return jsonify(ok=True,run=rid)


def work(rid,doc,revision,kind,selected,idea,resume_output=None):
    diagnostics.context.set(dict(run=rid,project=PID,revision=revision,kind=kind,stage='Starting'))
    output=dict(resume_output or {})
    output.pop('error',None);output.pop('diagnostic',None)
    event(rid,'Starting','Production task started.',revision=revision,kind=kind)
    update_run(rid,'running',output)
    folder=project_dir(PID)/'runs'/rid
    heartbeat_done=threading.Event()
    def heartbeat():
        started=time.monotonic()
        while not heartbeat_done.wait(20):
            with connect() as connection:
                last=connection.execute("SELECT stage FROM events WHERE run=? AND stage!='Heartbeat' ORDER BY id DESC LIMIT 1",(rid,)).fetchone()
            event(rid,'Heartbeat','Still running '+(last['stage'] if last else kind)+' · elapsed '+str(int(time.monotonic()-started))+'s')
    threading.Thread(target=heartbeat,daemon=True).start()
    try:
        if kind in ('write','script_review','growth'):
            from model_runtime import ensure
            event(rid,'Script model','Preparing the local script model if it is not installed yet.')
            ensure(doc['settings']['text_model'],lambda message:event(rid,'Script model',message))
        if kind=='write':
            event(rid,'Tamil writer','Writing dialogue, blocking and continuity.')
            from provider import duration_limit
            limit=duration_limit()
            written=model_json(doc['settings']['text_model'],writer_prompt(idea,limit))
            if not isinstance(written,dict) or not isinstance(written.get('shots'),list):
                raise ValueError('The script model did not return a script object with shots. Retry or choose another Tamil-capable text model.')
            written['settings']=doc['settings']
            result=check(enrich(written))
            if any(shot['seconds']>limit for shot in result['shots']):
                raise ValueError(f'The writer exceeded the selected provider limit of {limit:g} seconds per shot. Rerun script writing or shorten the shot.')
            from quality import preflight
            preflight(result)
            with mutation: save_doc(PID,result,revision)
            output={'message':'New script saved. Review dialogue and shot prompts before rendering.'}
        elif kind=='script_review':
            event(rid,'Tamil editor','Reviewing oral rhythm, meaning, timing and forms of address.')
            output=model_json(doc['settings']['text_model'],
                "Act as a native Chennai Tamil dialogue editor and film continuity supervisor. Review every shot. "
                "Return JSON with verdict, issues (array with shot number, Tamil replacement and reasoning), and continuity_notes. "
                "Assess realistic action duration, explicit eyelines, stable props, mouth visibility and adjacent end/start states. "
                "Do not silently rewrite the approved dialogue.\n"+json.dumps(doc,ensure_ascii=False))
        elif kind=='quota_check':
            from provider_pool import refresh_quota
            event(rid,'Live quota check','Reading the configured account allowance without submitting a video.')
            output=refresh_quota(force=True)
            event(rid,'Quota result',output.get('note') or ('Live remaining allowance: '+str(output.get('remaining_gpu_seconds'))+' GPU-seconds.'))
        elif kind=='provider_check':
            from provider import open_provider
            event(rid,'Provider check','Checking Space access and its live video API without consuming a generation.')
            connection=open_provider(folder)
            output=connection[3]
            output['quota_status']=__import__('provider_pool').refresh_quota(force=True)
            output['missing_first_frames']=[index+1 for index,shot in enumerate(doc['shots']) if not shot.get('first_frame') and not shot.get('imported_clip')]
        elif kind=='illustrated':
            from illustrated import render as illustrated_render
            cached={}
            # Reuse only current-revision reviewed clips and their existing voices.
            with connect() as connection:
                source=connection.execute("SELECT * FROM runs WHERE project=? AND revision=? AND kind='render' ORDER BY rowid DESC LIMIT 1",(PID,revision)).fetchone()
            if source:
                saved=json.loads(source['output'])
                for index,entry in resumable({'output':saved},doc).items():
                    cached[index]=safe_file(project_dir(PID),entry['attempts'][-1]['clip'])
                for index,shot in enumerate(doc['shots']):
                    voice=project_dir(PID)/'runs'/source['id']/f'voice-{index+1:02}.mp3'
                    if not shot.get('voice') and voice.is_file() and voice.stat().st_size:
                        shot['voice']=str(voice.relative_to(project_dir(PID))).replace(os.sep,'/')
            result=illustrated_render(doc,project_dir(PID),folder,lambda stage,message:event(rid,stage,message),cached)
            output={'master':str(Path(result['master']).relative_to(project_dir(PID))).replace(os.sep,'/'),'mode':result['mode'],'message':result['limitations'],'shots':[]}
            if '--complete-pilot' in sys.argv:
                exports=ROOT/'exports';exports.mkdir(exist_ok=True)
                exported=exports/'Sheru-Bhai-E01-Illustrated.mp4'
                shutil.copy2(result['master'],exported)
                output['export_file']=str(exported)
            for item in result['shots']:
                output['shots'].append({'index':item['index'],'attempts':[{'clip':str(Path(item['clip']).relative_to(project_dir(PID))).replace(os.sep,'/'),'report':{'status':'review','format':item['source'],'note':'Illustrated/hybrid completion. Full-speed playback review required.'}}]})
        elif kind=='recheck':
            with connect() as connection:
                source=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(idea,PID)).fetchone()
            if not source or source['revision']!=revision:raise ValueError('The source render revision changed.')
            saved=json.loads(source['output'])
            snapshot=json.loads((project_dir(PID)/'runs'/idea/'script.json').read_text(encoding='utf8'))
            from model_runtime import ensure
            ensure(snapshot['settings'].get('vision_model'),lambda message:event(rid,'Review model',message))
            from finishing import review_cut
            previous=None;previous_shot=None;checked=[]
            for entry in saved.get('shots',[]):
                latest=newest_clip(entry)
                if not latest:continue
                index=entry['index'];shot=snapshot['shots'][index]
                diagnostics.set_context(shot=index+1)
                event(rid,'Saved clip review',f'Shot {index+1}: rechecking retained footage. No video generation request.')
                clip=safe_file(project_dir(PID),latest['clip'])
                result=review(clip,shot,snapshot['settings'],folder/'reference.png',folder/f'saved-review-{index+1}')
                if previous:
                    result['cut']=review_cut(previous,clip,previous_shot,shot,snapshot['settings'],folder/'reference.png',folder/f'saved-cut-{index+1}')
                    if result['cut']['verdict']=='fail':result['status']='fail'
                latest.setdefault('review_history',[]).append(latest.get('report',{}))
                latest['report']=result;checked.append(index+1)
                update_run(idea,source['state'],saved)
                previous=clip;previous_shot=shot
            output={'message':'Retained clips rechecked without cloud video generation. Results appear under the original render in Review room.','source_run':idea,'checked_shots':checked}
        elif kind=='growth':
            from growth import research,parse_metrics
            event(rid,'Trend research','Checking related India search trends and recent Tamil-comedy YouTube candidates.')
            metrics_file=project_dir(PID)/'analytics.csv'
            metrics=parse_metrics(metrics_file.read_text(encoding='utf8')) if metrics_file.exists() else []
            with connect() as connection:
                prior=connection.execute('SELECT snapshot FROM growth_plans WHERE project=? ORDER BY rowid DESC LIMIT 1',(PID,)).fetchone()
            snapshot,plan=research(doc,metrics,json.loads(prior['snapshot']) if prior else None)
            with connect() as connection:
                connection.execute('INSERT INTO growth_plans VALUES(?,?,?,?,?,?)',(rid,PID,revision,time.time(),json.dumps(snapshot,ensure_ascii=False),json.dumps(plan,ensure_ascii=False)))
            output={'message':'Discovery plan ready. Evidence, metadata and proposed release times are visible in Growth research.','plan':plan,'warnings':snapshot['warnings']}
        elif kind=='visual_model':
            event(rid,'Visual reviewer','Downloading the local visual review model. This uses several GB on the chosen drive.')
            try:
                result=subprocess.run(['ollama','pull',doc['settings']['vision_model']],capture_output=True,text=True,errors='replace',timeout=7200)
            except subprocess.TimeoutExpired:
                raise ValueError('The visual model download did not finish within two hours. Check internet access and retry.') from None
            if result.returncode: raise ValueError('Visual model download failed. Check internet access and retry.')
            output={'message':'Visual model is installed.'}
        else:
            pending,stale=checkpoints(output) if resume_output else ({},set())
            kept,_=checkpoints(output,completed=True) if resume_output else ({},set())
            for index in sorted(stale-set(kept)):
                event(rid,'Checkpoint',f'Shot {index+1}: retained footage is missing or was modified after review, so it is regenerated rather than reused.','WARNING')
            output={'shots':[kept[i] for i in sorted(kept)],'selected':selected}
            update_run(rid,'running',output)
            indexes=range(len(doc['shots'])) if selected is None else [selected]
            previous_clip=None
            previous_shot=None
            provider_connection=None
            # Provider connection is opened only when new motion footage is needed.
            # Existing failed clips can be repaired locally during a cloud cooldown.
            if doc['settings'].get('vision_model'):
                from model_runtime import ensure
                event(rid,'Review model','Checking local visual-review readiness before cloud video generation.')
                ensure(doc['settings']['vision_model'],lambda message:event(rid,'Review model',message))
            # Validate every remaining speech recording before any GPU submission.
            voices={}
            for voice_index in indexes:
                if voice_index in kept:continue
                voice_shot=doc['shots'][voice_index]
                diagnostics.set_context(shot=voice_index+1,attempt=None)
                imported=voice_shot.get('imported_clip')
                if voice_shot.get('voice'):
                    voice=safe_file(project_dir(PID),voice_shot['voice'])
                elif not imported:
                    event(rid,'Tamil voice',f'Shot {voice_index+1}: recording the exact line and checking its duration before video generation.')
                    voice=folder/f'voice-{voice_index+1:02}.mp3'
                    make_voice(voice_shot,voice)
                else:voice=None
                if voice:
                    from finishing import seconds
                    voice_shot['speech_seconds']=seconds(voice)
                    if voice_shot['speech_seconds']+voice_shot['dialogue_start']>voice_shot['seconds']-.1:
                        raise ValueError(f'Shot {voice_index+1}: recorded Tamil exceeds its slot. Split or retime the shot before video generation.')
                voices[voice_index]=voice
            for index in indexes:
                shot=doc['shots'][index]
                diagnostics.set_context(shot=index+1,attempt=None)
                if index in kept:
                    entry=kept[index]
                    previous_clip=project_dir(PID)/entry['attempts'][-1]['clip'];previous_shot=shot
                    event(rid,'Checkpoint',f'Shot {index+1}: reusing completed footage and its recorded review.')
                    update_run(rid,'running',output)
                    continue
                from auto_repair import repair_audio,audio_failed,corrections,QualityRetry
                prior=pending.get(index)
                candidate=newest_clip(prior) if prior else None
                entry={'index':index,'attempts':list(prior['attempts']) if prior else []}
                output['shots'].append(entry);output['shots'].sort(key=lambda item:item['index'])
                update_run(rid,'running',output)
                voice=voices.get(index)
                if candidate and candidate.get('voice'):
                    voice=safe_file(project_dir(PID),candidate['voice'])
                imported=shot.get('imported_clip')
                attempts=1 if imported else doc['settings']['attempts']
                offset=len(entry['attempts'])
                for cycle in range(1,attempts+1):
                    attempt=offset+cycle
                    diagnostics.set_context(attempt=attempt)
                    shot['render_attempt']=attempt
                    already_graded=False
                    if cycle==1 and candidate:
                        clip=safe_file(project_dir(PID),candidate['clip'])
                        raw=safe_file(project_dir(PID),candidate.get('raw') or candidate['clip'])
                        already_graded=not bool(candidate.get('raw'))
                        report=json.loads(json.dumps(candidate.get('report',{})))
                        generation=candidate.get('generation',{'source':'retained failed candidate'})
                        event(rid,'Automatic recovery',f'Shot {index+1}: diagnosing retained footage before requesting another video.')
                    else:
                        event(rid,'Video generation',f'Shot {index+1}, attempt {attempt}: '+('using imported footage.' if imported else 'generating actual motion footage.'))
                        try:
                            if imported:raw=safe_file(project_dir(PID),imported)
                            else:
                                if provider_connection is None:
                                    from provider_pool import open_pool
                                    event(rid,'Provider check','Opening a compatible cloud provider for new motion footage.')
                                    provider_connection=open_pool(folder,doc,project_dir(PID),[index],lambda message:event(rid,'Provider routing',message))
                                raw=generate(doc,index,folder,project_dir(PID),provider_connection,lambda message:event(rid,'GPU provider',message))
                            pinned=folder/f'raw-{index+1:02}-{attempt}.mp4'
                            if Path(raw).resolve()!=pinned.resolve():shutil.copy2(raw,pinned)
                            raw=pinned
                            clip=folder/f'shot-{index+1:02}-attempt-{attempt}.mp4'
                            event(rid,'Sound & colour',f'Shot {index+1}: mixing the exact Tamil line and applying colour.')
                            dub_and_finish(raw,voice,doc,index,clip,project_dir(PID))
                        except (ValueError,RuntimeError) as error:
                            entry['attempts'].append({'error':diagnostics.redact(str(error)),'diagnostic':diagnostics.capture(error),'prompt':prompt(doc,index)})
                            update_run(rid,'running',output)
                            from provider import ProviderError
                            if isinstance(error,ProviderError):raise
                            event(rid,'Automatic correction',str(error),'WARNING')
                            shot['corrections']=str(error)
                            if cycle==attempts:raise QualityRetry(f'Shot {index+1}: media repair needs another automatic cycle.') from error
                            continue
                        event(rid,'Output review',f'Shot {index+1}: checking actual footage, sampled frames and Tamil speech.')
                        report=review(clip,shot,doc['settings'],folder/'reference.png',folder/f'review-{index+1:02}-{attempt}')
                        audit=folder/f'provider-{index+1:02}-{attempt}.json'
                        generation=json.loads(audit.read_text(encoding='utf8')) if audit.exists() else {'source':'imported footage'}
                    repairs=[]
                    if audio_failed(report):
                        try:
                            clip,voice,report,repairs=repair_audio(doc,index,raw,voice,clip,report,folder,project_dir(PID),lambda stage,message:event(rid,stage,message),make_voice,already_graded)
                            voices[index]=voice
                        except Exception as error:
                            repairs.append({'action':'audio repair unavailable','diagnostic':diagnostics.capture(error)})
                            event(rid,'Automatic audio repair',f'Shot {index+1}: recording/review service failed; retained footage is saved for automatic recovery.','WARNING')
                    if previous_clip:
                        from finishing import review_cut
                        event(rid,'Cut continuity',f'Shot {index+1}: comparing its opening with the preceding shot.')
                        cut=review_cut(previous_clip,clip,previous_shot,shot,doc['settings'],folder/'reference.png',folder/f'cut-{index+1:02}-{attempt}')
                        report['cut']=cut
                        if cut['verdict']=='fail':
                            report['status']='fail'
                            report.setdefault('visual',{}).setdefault('issues',[]).append('Cross-shot continuity: '+cut['evidence'])
                    def relative(path):return str(Path(path).relative_to(project_dir(PID))).replace(os.sep,'/') if path else None
                    entry['clip_sha256']=__import__('hashlib').sha256(clip.read_bytes()).hexdigest()
                    entry['attempts'].append({'clip':relative(clip),'raw':relative(raw) if not already_graded else None,'voice':relative(voice),'report':report,'audio_repairs':repairs,'prompt':prompt(doc,index),'generation':generation})
                    update_run(rid,'running',output)
                    if report['status']!='fail':break
                    shot['corrections']=corrections(report)
                    if audio_failed(report) and not __import__('auto_repair').other_failed(report):
                        # More silent-video generations cannot fix an unresolved audio problem.
                        raise QualityRetry(f'Shot {index+1}: exact-line voice repair is still unresolved. Retained motion footage will be reused in the next automatic cycle.')
                    event(rid,'Automatic regeneration',f'Shot {index+1}: applying observed visual/cut corrections and trying another motion render.','WARNING')
                previous_clip=clip;previous_shot=shot
                if entry['attempts'][-1].get('report',{}).get('status')=='fail':
                    raise QualityRetry(f'Shot {index+1}: quality checks still fail after {attempts} attempts. Automatic recovery will continue from retained footage.')
            if selected is None:
                ordered=folder/'ordered'
                ordered.mkdir(exist_ok=True)
                for entry in output['shots']:
                    src=project_dir(PID)/entry['attempts'][-1]['clip']
                    shutil.copy2(src,ordered/f'{entry["index"]+1:02}.mp4')
                (folder/'assembly.mp4').unlink(missing_ok=True)
                assemble(doc,ordered,folder/'assembly.mp4')
                from finishing import finish_master
                finish_master(folder/'assembly.mp4',folder/'master.mp4',doc,project_dir(PID))
                output['master']=f'runs/{rid}/master.mp4'
                output['message']='Master assembled. Watch full-speed physics, native Tamil, lip sync and sound timing before approval.'
        update_run(rid,'review' if kind in ('render','illustrated') else 'done',output)
        event(rid,'Complete','Output is ready for review.')
    except Exception as error:
        output['retry_reason']=getattr(error,'reason',None)
        output['retry_after']=getattr(error,'retry_after',None)
        output['quota']=getattr(error,'quota',{})
        if output['retry_after']:output['retry_not_before']=time.time()+output['retry_after']
        output['diagnostic']=diagnostics.capture(error)
        output['error']=output['diagnostic']['message'][:1600]
        update_run(rid,'blocked',output)
        if output['retry_reason']=='quota':
            event(rid,'Waiting for provider allowance','Automatic production is waiting for the provider reset or a successful live account quota refresh. No new video was generated.','WARNING')
        elif output['retry_reason']=='quality':
            event(rid,'Automatic quality repair','Retained footage is saved. The next repair cycle is queued automatically.','WARNING',error_id=output['diagnostic']['id'],code=output['diagnostic']['code'])
        else:
            event(rid,'Needs attention',output['error'],'ERROR',error_id=output['diagnostic']['id'],code=output['diagnostic']['code'])
    finally:
        heartbeat_done.set()
        import automation
        with connect() as connection:
            row=connection.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
        finished=dict(row);finished['output']=json.loads(finished['output'])
        if kind=='growth':
            auto=automation.state()
            if auto['enabled'] and auto.get('project')==PID and auto.get('revision')==revision and auto['state']=='review':
                automation.save(message='Episode and growth plan are ready for final playback review.' if finished['state']=='done' else 'Episode is ready for review; growth research failed. Open the latest run diagnostic.')
        followup=automation.after_run(finished)
        if followup=='growth':
            with mutation:
                current,current_revision=document(PID)
                if current_revision==revision:
                    start_task('growth',current,revision)


@app.post('/api/approve')
def approve():
    data=request.get_json()
    checks=data.get('checks')
    if not isinstance(checks,list) or len(checks)!=5 or not all(x is True for x in checks):
        raise ValueError('Review and confirm all five final checks.')
    with mutation:
        idle(PID)
        _,revision=document(PID)
        with connect() as connection:
            row=connection.execute("SELECT * FROM runs WHERE id=? AND project=?",(data.get('run'),PID)).fetchone()
            if not row or row['revision']!=revision or row['state']!='review':
                raise ValueError('Approve a current full-episode preview.')
            output=json.loads(row['output'])
            if not output.get('master'): raise ValueError('Render the full episode before approval.')
            if any(entry.get('attempts') and entry['attempts'][-1].get('report',{}).get('status')=='fail'
                   for entry in output.get('shots',[])):
                raise ValueError('This episode contains failed shots. Regenerate and recheck them before approval.')
            output['approval']={'checks':checks,'at':time.time()}
            connection.execute("UPDATE runs SET state='approved',output=? WHERE id=?",(json.dumps(output,ensure_ascii=False),row['id']))
    return jsonify(ok=True)


@app.post('/api/queue')
def queue_release():
    import re
    from datetime import datetime
    from releases import db,digest
    data=request.get_json()
    episode=str(data.get('episode',''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',episode):
        raise ValueError('Use a unique episode ID such as S01E001.')
    using_growth=bool(data.get('use_growth'))
    requested_time=data.get('when','')
    try:
        due=datetime.fromisoformat(requested_time) if requested_time else None
    except ValueError:
        raise ValueError('Use a release date with a UTC offset, such as 2030-01-01T19:00:00+05:30.')
    if due and (not due.tzinfo or due.timestamp()<=time.time()): raise ValueError('Choose a future release time with a UTC offset.')
    if not due and not using_growth: raise ValueError('Choose a future release time or use a growth plan.')
    from urllib.parse import urlsplit
    url=str(data.get('url',''))
    if urlsplit(url).scheme!='https' or not urlsplit(url).hostname: raise ValueError('Provide a direct HTTPS link to the same approved video for Instagram.')
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        with connect() as connection:
            row=connection.execute("SELECT * FROM runs WHERE project=? AND revision=? AND state='approved' ORDER BY rowid DESC LIMIT 1",(PID,revision)).fetchone()
        if not row: raise ValueError('Approve the current full-episode master in the review room first.')
        master=project_dir(PID)/json.loads(row['output'])['master']
        plan=None
        if using_growth:
            with connect() as connection:
                growth_row=connection.execute('SELECT * FROM growth_plans WHERE project=? AND revision=? ORDER BY rowid DESC LIMIT 1',(PID,revision)).fetchone()
            if not growth_row: raise ValueError('Run growth research for this episode before using its plan.')
            plan=json.loads(growth_row['plan'])
            from growth import validate_metadata
            for platform in ('youtube','instagram'): validate_metadata(platform,plan[platform])
        from accounts import stored
        for provider in ('youtube','instagram'):
            credentials=stored(provider)
            if not credentials or credentials.get('expires',float('inf'))<=time.time(): raise ValueError('Connect '+provider+' before scheduling.')
        try:
            with db() as connection:
                for platform in ('youtube','instagram'):
                    metadata={}
                    release_due=due
                    release_title=doc['title']
                    if plan:
                        metadata=dict(plan[platform],researched_at=plan['checked_at'],auto_refresh=bool(data.get('auto_refresh')),growth_context={'platform':platform,'script':doc},evidence_refs=plan.get('evidence_refs',[]))
                        if not release_due:
                            release_due=datetime.fromisoformat(plan['schedule'][platform]['when'])
                        release_title=metadata.get('title') or doc['title']
                    if plan and not due:
                        from datetime import timedelta
                        latest=connection.execute("SELECT MAX(due) FROM jobs WHERE platform=? AND state IN ('queued','check')",(platform,)).fetchone()[0]
                        original_time=release_due
                        while release_due.timestamp()<=max(time.time()+3600,latest or 0):
                            release_due+=timedelta(days=1)
                        if release_due!=original_time:
                            metadata['timing_note']='Moved to the next available daily occurrence of the proposed hour to retain queued episode order.'
                    if not release_due or not release_due.tzinfo or release_due.timestamp()<=time.time():
                        raise ValueError('Proposed release time has passed. Refresh the growth plan or supply a future time.')
                    connection.execute('INSERT INTO jobs(episode,platform,path,hash,title,url,due,state,remote,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(episode,platform,str(master),digest(master),release_title,url,release_due.timestamp(),'queued',None,json.dumps(metadata,ensure_ascii=False)))
        except sqlite3.IntegrityError: raise ValueError('That episode ID is already scheduled. Choose another ID.')
    return jsonify(ok=True)


@app.post('/api/release/resolve')
def resolve_release():
    import re
    from releases import db
    data=request.get_json()
    episode=str(data.get('episode',''));platform=str(data.get('platform',''));action=str(data.get('action',''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',episode) or platform not in ('youtube','instagram') or action not in ('retry','submitted'):
        raise ValueError('Choose a held or failed upload and an action.')
    with db() as connection:
        row=connection.execute('SELECT state FROM jobs WHERE episode=? AND platform=?',(episode,platform)).fetchone()
        if not row or row['state'] not in ('queued','check','failed'):
            raise ValueError('Only a queued, held or failed upload can be resolved.')
        connection.execute("UPDATE jobs SET state=?,remote=?,last_error='{}',attempts=0,retry_at=NULL WHERE episode=? AND platform=?",('queued' if action=='retry' else 'submitted',None if action=='retry' else 'confirmed manually',episode,platform))
    return jsonify(ok=True)

@app.post('/api/growth/metrics')
def growth_metrics():
    from growth import parse_metrics
    with mutation:
        idle(PID)
        file=request.files.get('file')
        if not file:raise ValueError('Choose an analytics CSV.')
        raw=file.stream.read(500_001)
        if len(raw)>500_000:raise ValueError('Analytics CSV must be under 500 KB.')
        text=raw.decode('utf-8-sig')
        rows=parse_metrics(text)
        (project_dir(PID)/'analytics.csv').write_text(text,encoding='utf8')
    return jsonify(ok=True,rows=len(rows))

@app.post('/api/growth/save')
def save_growth():
    from growth import validate_metadata
    data=request.get_json()
    with mutation:
        idle(PID)
        _,revision=document(PID)
        with connect() as connection:
            row=connection.execute('SELECT * FROM growth_plans WHERE id=? AND project=? AND revision=?',(data.get('id'),PID,revision)).fetchone()
            if not row:raise ValueError('Research this episode revision before saving its publication copy.')
            plan=json.loads(row['plan'])
            for platform in ('youtube','instagram'):
                plan[platform]=validate_metadata(platform,data[platform])
            connection.execute('UPDATE growth_plans SET plan=? WHERE id=?',(json.dumps(plan,ensure_ascii=False),row['id']))
    return jsonify(ok=True)

@app.post('/api/publisher')
def start_publisher():
    global publishing
    with mutation:
        if not publishing:
            publishing=True
            threading.Thread(target=publish_loop,daemon=True).start()
    return jsonify(ok=True)

def publish_loop():
    global publishing
    from releases import db,run
    try:
        with db() as connection:
            while True:
                run(connection)
                time.sleep(30)
    except Exception as error:
        diagnostics.context.set({'stage':'Publication worker'})
        diagnostics.capture(error)
    finally: publishing=False

def automation_loop():
    import automation
    while True:
        try:
            data=automation.state()
            if data['enabled'] and data['state']=='running':
                with connect() as connection:
                    row=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(data['run'],PID)).fetchone()
                if row and row['state']=='interrupted':
                    automation.save(state='waiting',retry_at=time.time()+60,message='Studio restarted. Resuming the interrupted episode from its completed-shot checkpoints.')
            if data['enabled'] and data['state']=='waiting':
                from local_wan import enabled as local_wan_ready
                if local_wan_ready():
                    automation.save(retry_at=time.time(),message='Local Wan is ready. Resuming retained production.')
                else:
                    from provider_pool import cooldown,refresh_quota
                    waiting=cooldown()
                    if waiting.get('until',0)>time.time():
                        live=refresh_quota()
                        if live.get('status')=='checked' and cooldown().get('wait_status')=='cleared_by_live_allowance':
                            automation.save(retry_at=time.time(),message='Live account allowance is available. Resuming retained production.')
            if automation.due(automation.state()):
                data=automation.state()
                with mutation:
                    idle(PID)
                    doc,revision=document(PID)
                    if data.get('project')!=PID or data.get('revision')!=revision:
                        automation.save(state='blocked',retry_at=None,message='Episode changed. Start automatic production for the current revision.')
                    else:
                        with connect() as connection:
                            row=connection.execute('SELECT * FROM runs WHERE id=? AND project=?',(data['run'],PID)).fetchone()
                        if not row or row['state'] not in ('blocked','interrupted'):
                            automation.save(state='blocked',retry_at=None,message='Previous render cannot be resumed. Start automatic production again.')
                        else:
                            output=json.loads(row['output'])
                            snapshot=json.loads((project_dir(PID)/'runs'/row['id']/'script.json').read_text(encoding='utf8'))
                            update_run(row['id'],'queued',output)
                            automation.save(state='running',retry_at=None,message='Resuming production from retained shots.')
                            executor.submit(work,row['id'],snapshot,revision,'render',output.get('selected'),'',output)
        except Busy:
            pass # Another local task owns this episode. Recheck on the next tick.
        except Exception as error:
            diagnostics.capture(error,worker='automation')
            automation.save(state='blocked',retry_at=None,message='Automatic worker failed. Download the application log.')
        time.sleep(20)

def complete_pilot_on_start(kind='render'):
    """Fresh-setup completion is idempotent and explicitly illustrated."""
    with mutation:
        idle(PID)
        doc,revision=document(PID)
        with connect() as connection:
            rows=connection.execute("SELECT output FROM runs WHERE project=? AND revision=? AND kind=? AND state IN ('review','approved') ORDER BY rowid DESC",(PID,revision,kind)).fetchall()
        for row in rows:
            master=json.loads(row['output']).get('master')
            if master and (project_dir(PID)/master).is_file():return None
        import automation
        automation.save(enabled=False,state='paused',retry_at=None,message='Fresh setup is preparing the requested episode pipeline.')
        return start_task(kind,doc,revision,automatic=(kind=='render'))

if __name__=='__main__':
    import webbrowser
    if '--animate-pilot' in sys.argv:
        complete_pilot_on_start('render')
    elif '--complete-pilot' in sys.argv:
        complete_pilot_on_start('illustrated')
    threading.Thread(target=automation_loop,daemon=True).start()
    from releases import db as release_db
    with release_db() as connection:
        queued=connection.execute("SELECT 1 FROM jobs WHERE state='queued' LIMIT 1").fetchone()
    if queued:
        publishing=True
        threading.Thread(target=publish_loop,daemon=True).start()
    if '--no-browser' not in sys.argv:
        threading.Timer(1,lambda:webbrowser.open(f'http://127.0.0.1:{PORT}/'+('?first=1' if '--complete-pilot' in sys.argv else ''))).start()
    app.run(host='127.0.0.1',port=PORT,debug=False,use_reloader=False,threaded=True)
