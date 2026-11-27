"""Compatible cloud routing. Shared quotas are never multiplied by routing."""
import os,time,json,threading
from pathlib import Path
from provider import config,open_provider,ProviderError,preflight_episode
CATALOG=[
 {'space':'zerogpu-aoti/wan2-2-fp8da-aoti-faster','endpoint':'/generate_video','label':'Wan 2.2 fast','model':'Wan 2.2'},
 {'space':'Lightricks/LTX-2.5','endpoint':'/run','label':'LTX 2.5 official','model':'LTX 2.5'},
 {'space':'Lightricks/LTX-Video-distilled','endpoint':'/image_to_video','label':'LTX Video distilled official','model':'LTX Video distilled'},
]

WAIT=Path(os.getenv('SHERU_WAIT') or Path(__file__).resolve().parent/'provider-wait.json')
wait_lock=threading.RLock()
def cooldown():
    with wait_lock:
        return json.loads(WAIT.read_text(encoding='utf8')) if WAIT.exists() else {}
def set_cooldown(error):
    with wait_lock:
        data={'until':time.time()+(error.retry_after or 21600),'group':'huggingface-shared','reason':'quota','quota':error.quota}
        temp=WAIT.with_suffix('.tmp');temp.write_text(json.dumps(data));temp.replace(WAIT)
def check_cooldown():
    if cooldown().get('until',0)>time.time():refresh_quota()
    waiting=cooldown()
    remaining=waiting.get('until',0)-time.time()
    if remaining>0:raise ProviderError('Waiting for the saved Hugging Face retry window. This is not a new generation failure. Automatic production resumes when allowance is available; retained clips remain in Review room.',reason='quota',retry_after=remaining,quota=waiting.get('quota'))

def repair_legacy_wait(runs):
    """Correct old HH:MM:SS parsing from retained provider evidence only."""
    from datetime import datetime
    from provider import quota_details
    for run in runs:
        output=run.get('output',{})
        detail=output.get('diagnostic',{})
        for cause in detail.get('chain',[]):
            quota=quota_details(cause.get('message',''))
            if 'retry_seconds' not in quota:continue
            try:at=datetime.fromisoformat(detail['at']).timestamp()
            except (KeyError,ValueError):continue
            until=at+quota['retry_seconds']+60
            if until<=time.time():continue
            with wait_lock:
                old=cooldown()
                if old.get('live_checked_at',0)>at and old.get('wait_status')=='cleared_by_live_allowance':continue
                if old.get('until',0)>=until:continue
                data={'until':until,'group':'huggingface-shared','reason':'quota','quota':quota,'corrected_from_run':run.get('id')}
                temp=WAIT.with_suffix('.tmp');temp.write_text(json.dumps(data));temp.replace(WAIT)
    return cooldown()

def candidates():
    data=config()
    chosen={'space':data['space'],'endpoint':data['endpoint'],'use_auth':data['use_auth']}
    result=[chosen]
    if data.get('auto_fallback',True):
        for item in CATALOG:
            if item['space']!=data['space']:
                result.append(dict(item,use_auth=data['use_auth']))
    return result

class Pool:
    def __init__(self,folder,ep,project,indexes,on_progress=None):
        check_cooldown()
        self.folder=Path(folder);self.ep=ep;self.project=project;self.indexes=list(indexes)
        self.options=candidates();self.connections={};self.failed={};self.reasons={};self.on_progress=on_progress
        self.primary=self.select(0)
    def note(self,text):
        if self.on_progress:self.on_progress(text)
    def select(self,start):
        problems=[]
        for offset in range(len(self.options)):
            index=(start+offset)%len(self.options)
            data=self.options[index];space=data['space']
            if space in self.failed:
                problems.append(self.failed[space]);continue
            if space in self.connections:return self.connections[space]
            try:
                self.note('Checking '+space+' for image input, duration and API compatibility.')
                connection=open_provider(self.folder,data)
                preflight_episode(self.ep,self.project,connection,self.indexes)
                self.connections[space]=connection
                return connection
            except ProviderError as error:
                import diagnostics
                diagnostics.capture(error,space=space,routing='availability check')
                self.failed[space]=str(error);self.reasons[space]=getattr(error,'reason','input');problems.append(str(error))
                if getattr(error,'reason','')=='quota':raise
                # Missing project frames cannot be solved by selecting another model.
                if 'first-frame' in str(error).lower():raise
        raise ProviderError('No configured cloud provider passed preflight. '+' | '.join(problems),reason='unavailable' if self.reasons and all(value in ('unavailable','network') for value in self.reasons.values()) else 'input')
    def __getitem__(self,index):return self.primary[index]
    def generate(self,ep,index,folder,project,on_progress=None):
        check_cooldown()
        from finishing import _generate_single
        attempt=ep['shots'][index].get('render_attempt',1)
        start=(attempt-1)%len(self.options)
        connection=self.select(start)
        for _ in range(len(self.options)):
            try:
                self.note('Rendering shot '+str(index+1)+' with '+connection[3]['space']+'.')
                return _generate_single(ep,index,folder,project,connection,on_progress)
            except ProviderError as error:
                if getattr(error,'reason','') not in ('unavailable','network'):raise
                space=connection[3]['space'];self.failed[space]=str(error);self.reasons[space]=error.reason;self.connections.pop(space,None)
                self.note(space+' unavailable. Trying the next compatible configured provider.')
                connection=self.select(start)
        raise ProviderError('Compatible cloud providers are unavailable.')

class LocalPool:
    def __init__(self,folder,ep,project,indexes,on_progress=None):
        self.folder=folder
        self.ep=ep
        self.project=project
        self.indexes=indexes
        self.on_progress=on_progress
        self.local_available=True

    def generate(self,ep,index,folder,project,on_progress=None):
        if self.local_available:
            try:
                from local_video import render
                return render(ep,index,folder,project,on_progress)
            except (RuntimeError,ValueError, OSError) as error:
                self.local_available=False
                import diagnostics
                diagnostics.capture(error,stage='Local LTX render')
                if on_progress:
                    on_progress('Local render failed. Checking cloud video providers.')
        return Pool(folder,ep,project,[index],self.on_progress).generate(
            ep,index,folder,project,on_progress)


def open_pool(folder,ep,project,indexes,on_progress=None):
    from local_video import enabled
    if enabled():
        return LocalPool(folder,ep,project,indexes,on_progress)
    return Pool(folder,ep,project,indexes,on_progress)


def refresh_quota(force=False):
    """Read the configured user's base allowance. No generation or credit purchase."""
    import hashlib,urllib.request
    from datetime import datetime
    from provider import token_for
    data=config();secret=token_for(data);previous=cooldown()
    if not secret:return {'status':'needs_auth','note':'Anonymous access: only the saved provider retry time is known. Add your own Hugging Face token in Connections to check live account quota.'}
    identity=hashlib.sha256(secret.encode()).hexdigest()[:16]
    if not force and previous.get('quota_identity')==identity and time.time()-previous.get('live_checked_at',0)<600:
        return previous.get('live_quota',{})
    request=urllib.request.Request('https://huggingface.co/api/spaces/zero-gpu/quota',headers={'Authorization':'Bearer '+secret,'User-Agent':'SheruStudio/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:payload=json.load(response)
        remaining=float(payload['current']);base=float(payload['base'])
        if remaining<0 or base<0:raise ValueError('Invalid quota values')
        reset=payload.get('resetsAt');until=datetime.fromisoformat(reset.replace('Z','+00:00')).timestamp()+60 if reset else None
        live={'status':'checked','source':'authenticated Hugging Face quota API','remaining_gpu_seconds':remaining,'base_gpu_seconds':base,'reset_at':reset,'checked_at':time.time()}
    except Exception as error:
        import diagnostics
        detail=diagnostics.capture(error,stage='Live quota check')
        live={'status':'unavailable','note':'Live quota could not be read. A fine-grained token needs Billing · Read billing usage and payment method status. Saved waits remain in force.','error_id':detail['id']}
        with wait_lock:
            current=cooldown();current.update(live_checked_at=time.time(),quota_identity=identity,live_quota=live)
            temp=WAIT.with_suffix('.tmp');temp.write_text(json.dumps(current));temp.replace(WAIT)
        return live
    with wait_lock:
        current=cooldown();requested=current.get('quota',{}).get('requested_gpu_seconds')
        current.update(live_checked_at=time.time(),quota_identity=identity,live_quota=live)
        if requested is not None:
            if remaining>=requested:
                current['until']=0
                current['wait_status']='cleared_by_live_allowance'
            elif until:
                current['until']=until
                current['wait_status']='confirmed_insufficient'
        elif current.get('until',0)<=time.time():
            # Without a saved request size, a live reading alone must not create a new wait.
            current['wait_status']='live_allowance_available'
        temp=WAIT.with_suffix('.tmp');temp.write_text(json.dumps(current));temp.replace(WAIT)
    return live

