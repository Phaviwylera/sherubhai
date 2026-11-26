"""Video-provider configuration, schema validation and actionable failures."""
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'video-provider.json'
DEFAULT_SPACE='zerogpu-aoti/wan2-2-fp8da-aoti-faster'
OLD_SPACE='Wan-AI/Wan-2.2-5B'
TOKEN_KEY='huggingface-video'

class ProviderError(ValueError):
    """A provider issue must not consume repeated render attempts blindly."""
    def __init__(self,message,reason='input',retry_after=None,quota=None):
        super().__init__(message)
        self.reason=reason
        self.retry_after=retry_after
        self.quota=quota or {}

def normalize_space(value):
    value=value.strip()
    if value.startswith('https://huggingface.co/spaces/'):
        value=urllib.parse.urlsplit(value).path.removeprefix('/spaces/').strip('/')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value):
        raise ProviderError('Enter a Hugging Face Space ID such as owner/space-name, or its huggingface.co/spaces URL.')
    return value

def write_config(data):
    temporary=CONFIG.with_name(CONFIG.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf8')
    temporary.replace(CONFIG)

def config():
    if CONFIG.exists():
        data=json.loads(CONFIG.read_text(encoding='utf8'))
    else:
        data={'space':os.getenv('SHERU_SPACE',DEFAULT_SPACE),'endpoint':os.getenv('SHERU_ENDPOINT',''),'use_auth':bool(os.getenv('HF_TOKEN'))}
    # Migrate the obsolete bundled default without changing custom provider choices.
    if data.get('space')==OLD_SPACE:
        data['space']=DEFAULT_SPACE
        data['endpoint']=''
        data.pop('last_check',None)
    data.setdefault('space',DEFAULT_SPACE)
    data.setdefault('endpoint','')
    data.setdefault('use_auth',False)
    data.setdefault('auto_fallback',True)
    return data


def duration_limit():
    data=config()
    bounds=(data.get('last_check') or {}).get('capabilities',{}).get('duration_range')
    if bounds:return min(6,float(bounds[1]))
    return 5 if data['space']==DEFAULT_SPACE else 6

def token_for(data):
    if not data.get('use_auth'):return None
    try:
        from accounts import stored
        secret=stored(TOKEN_KEY)
    except Exception:
        secret=None
    return (secret or {}).get('token') or os.getenv('HF_TOKEN') or None

def public_config():
    data=config()
    return dict(auto_fallback=data.get('auto_fallback',True),catalog=__import__('provider_pool').CATALOG,space=data['space'],endpoint=data['endpoint'],use_auth=data['use_auth'],has_token=bool(token_for(data)),last_check=data.get('last_check'))

def save_config(space,endpoint='',token='',use_auth=False,auto_fallback=True):
    space=normalize_space(space)
    if endpoint and not re.fullmatch(r'/[A-Za-z0-9_/-]+',endpoint):
        raise ProviderError('An explicit Gradio endpoint must start with a slash.')
    if token:
        if not re.fullmatch(r'hf_[A-Za-z0-9]+',token):
            raise ProviderError('Enter a Hugging Face access token beginning hf_.')
        from accounts import save
        try:save(TOKEN_KEY,{'token':token})
        except Exception as error:raise ProviderError('Windows credential storage could not save the token ('+type(error).__name__+').') from None
    data={'space':space,'endpoint':endpoint,'use_auth':bool(use_auth),'auto_fallback':bool(auto_fallback)}
    write_config(data)
    return public_config()

def quota_details(error):
    message=str(error)
    values={}
    match=re.search(r'(\d+(?:\.\d+)?)\s*s\s+requested\s+vs\.?\s*(\d+(?:\.\d+)?)\s*s\s+left',message,re.I)
    if match:values.update(requested_gpu_seconds=float(match[1]),remaining_gpu_seconds=float(match[2]))
    clock=re.search(r'try again in\s+(\d{1,3}):(\d{2}):(\d{2})',message,re.I)
    if clock:
        hours,minutes,seconds=map(int,clock.groups())
        if minutes<60 and seconds<60:
            values.update(retry_seconds=hours*3600+minutes*60+seconds,provider_retry_text=clock[0])
    return values

def retry_window(error):
    details=quota_details(error)
    if 'retry_seconds' in details:return max(60,details['retry_seconds']+60)
    message=str(error).lower()
    # Read only the wait instruction, not '68s requested vs 65s left'.
    wait=re.search(r'(?:try again in|retry after|wait)\s+(.+)',message)
    total=0
    if wait:
        for value,unit in re.findall(r'(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b',wait[1]):
            total+=float(value)*(3600 if unit.startswith('h') else 60 if unit.startswith('m') else 1)
    response=getattr(error,'response',None)
    header=getattr(response,'headers',{}).get('Retry-After','')
    try:total=max(total,float(header))
    except (TypeError,ValueError):pass
    return max(60,total+60) if total else 21600

def explain(error):
    if isinstance(error,ProviderError):return error
    name=type(error).__name__
    message=str(error).lower()
    status=getattr(getattr(error,'response',None),'status_code',None) or getattr(error,'code',None)
    if isinstance(error,ImportError):
        return ProviderError('The Hugging Face client has a missing or incompatible dependency. Run Setup.cmd to repair its Python packages; proxy configurations may also need the optional SOCKS transport dependency.')
    if status==429:
        return ProviderError('The video provider is rate-limiting requests. Wait for its retry window before submitting another render.',reason='quota',retry_after=retry_window(error))
    if 'repositorynotfound' in name.lower() or status in (401,404):
        return ProviderError('Video Space cannot be accessed. Its ID may be wrong, removed or private, or the saved token may lack access. Open Connections → Video provider, choose an accessible Space and test it.')
    if status==403 or 'gatedrepo' in name.lower():
        return ProviderError('Video provider denied access. Check the selected Space and Hugging Face account permissions, then run Test provider.')
    if any(term in message for term in ('exceeded your zerogpu quota','quota exhausted','quota exceeded','exceeded your gpu','gpu time limit','insufficient quota','not enough gpu')) or ('quota' in message and 'try again' in message):
        details=quota_details(error)
        if 'requested_gpu_seconds' in details:
            summary=f"Next shot needs {details['requested_gpu_seconds']:g} GPU-seconds, but the provider reports {details['remaining_gpu_seconds']:g} GPU-seconds remaining. This is compute time, not video length."
        else:summary='The provider reports insufficient GPU quota for this request.'
        if details.get('provider_retry_text'):summary+=' '+details['provider_retry_text']+'.'
        summary+=' Existing clips are retained. Open this run in Review room to preview completed shots.'
        return ProviderError(summary,reason='quota',retry_after=retry_window(error),quota=details)
    if any(term in message for term in ('paused','sleeping','runtime_error','building','not currently running','zerogpu is unavailable','no gpu available','no gpu is available')):
        return ProviderError('This Space is paused, starting or in a runtime error. Open its page to check availability, or select another compatible Space.',reason='unavailable')
    if any(term in name.lower() for term in ('timeout','connect','network','http')):
        return ProviderError('Video provider connection failed ('+name+'). Check network access to Hugging Face and test the selected Space again.',reason='network')
    return ProviderError('Video provider stopped ('+name+'). Test its endpoint in Connections. The request may require inputs or access this Space does not expose.')

def space_info(data,token):
    headers={'User-Agent':'SheruStudio/1.0'}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request('https://huggingface.co/api/spaces/'+data['space'],headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            info=json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401,404):
            raise ProviderError('Hugging Face cannot access '+data['space']+'. It may be unavailable or require a valid token. Check the Space ID and token settings in Connections.') from None
        raise explain(error) from error
    except Exception as error:raise explain(error) from error
    stage=info.get('runtime',{}).get('stage','UNKNOWN')
    if stage in ('PAUSED','RUNTIME_ERROR','BUILD_ERROR','STOPPED'):
        raise ProviderError('Selected Space reports '+stage+'. Its owner must restart it, or choose a running compatible Space.',reason='unavailable')
    return stage

def choose_endpoint(schema,selected=''):
    candidates=[(name,item) for name,item in schema.get('named_endpoints',{}).items()
                if any(p.get('parameter_name')=='prompt' for p in item.get('parameters',[]))
                and any('video' in str(value).lower() for value in item.get('returns',[]))]
    if selected:candidates=[item for item in candidates if item[0]==selected]
    if len(candidates)!=1:
        names=', '.join(name for name,_ in candidates) or 'none'
        raise ProviderError('No unique compatible video endpoint was found. Set its API endpoint in Connections. Candidates: '+names)
    name,endpoint=candidates[0]
    params={p['parameter_name']:p for p in endpoint['parameters']}
    image_key=next((key for key in ('input_image','image','start_image','image_input','image_path','input_image_filepath') if key in params),None)
    if not image_key and 'reference_image' not in params:
        raise ProviderError('This endpoint does not expose a supported image-conditioning input. Select an image-to-video Space.')
    known={'prompt','width','height','height_ui','width_ui','duration','duration_seconds','duration_s','duration_ui','seed','random_seed','seed_ui','randomize_seed','negative_prompt','n_prompt','reference_image'}
    if image_key:known.add(image_key)
    missing=[key for key,item in params.items() if not item.get('parameter_has_default') and key not in known]
    if missing:raise ProviderError('This endpoint needs additional required inputs: '+', '.join(missing)+'. Choose a supported endpoint.')
    duration_key=next((key for key in ('duration_seconds','duration','duration_s','duration_ui') if key in params),None)
    duration_range=None
    if duration_key:
        description=params[duration_key].get('type',{}).get('description','')
        match=re.search(r'between ([\d.]+) and ([\d.]+)',description)
        if match:duration_range=[float(match[1]),float(match[2])]
    return name,params,dict(image_parameter=image_key,duration_parameter=duration_key,duration_range=duration_range,separate_reference=('reference_image' in params),negative_prompt=any(key in params for key in ('negative_prompt','n_prompt')),seed=any(key in params for key in ('seed','random_seed','seed_ui')))

def open_provider(folder,data=None):
    from gradio_client import Client
    data=dict(data or config())
    secret=token_for(data)
    if data.get('use_auth') and not secret:
        raise ProviderError('Token authentication is enabled, but no Hugging Face token is saved. Enter it in Connections or switch to anonymous access.')
    stage=space_info(data,secret)
    try:
        client=Client(data['space'],token=secret if secret else False,download_files=str(Path(folder)/'provider'),verbose=False,httpx_kwargs={'timeout':30})
        schema=client.view_api(return_format='dict',print_info=False)
        name,params,capabilities=choose_endpoint(schema,data.get('endpoint',''))
    except Exception as error:raise explain(error) from error
    report=dict(space=data['space'],endpoint=name,stage=stage,checked_at=time.time(),authenticated=bool(secret),capabilities=capabilities,
                note='API accessibility verified. GPU quota and actual generation are checked only when a render is submitted.')
    if data['space']==DEFAULT_SPACE:
        report['native_output']='Portrait output is 480×832. Final export centre-crops approximately 2.5% of its width to 9:16.'
    current=config()
    if all(current.get(key)==data.get(key) for key in ('space','endpoint','use_auth')):
        data['last_check']=report
        write_config(data)
    return client,name,params,report

def preflight_episode(ep,project,connection,indexes):
    from finishing import safe_file
    capabilities=connection[3]['capabilities']
    missing=[]
    for index in indexes:
        shot=ep['shots'][index]
        if shot.get('imported_clip'):continue
        bounds=capabilities['duration_range']
        if bounds and not bounds[0]<=shot['seconds']<=bounds[1]:
            raise ProviderError(f'Shot {index+1} needs {shot["seconds"]} seconds, but this endpoint exposes {bounds[0]}–{bounds[1]} seconds. Retime the shot or choose a compatible provider.')
        if capabilities['image_parameter']:
            if not shot.get('first_frame'):missing.append(str(index+1))
            else:safe_file(project,shot['first_frame'])
    if missing:
        raise ProviderError('Approved first-frame images are missing for shot(s) '+', '.join(missing)+'. Select each shot and use Sound & first frame → First frame. The character portrait defines identity; each shot frame must also establish its setting and positions.')

def submit(client,endpoint,args,on_progress=None):
    try:
        job=client.submit(api_name=endpoint,**args)
        started=time.monotonic();last=None;tick=0
        while not job.done():
            status=job.status()
            code=getattr(status,'code','Waiting')
            label=getattr(code,'value',str(code))
            rank=getattr(status,'rank',None)
            key=(str(label),rank)
            elapsed=int(time.monotonic()-started)
            if on_progress and (key!=last or elapsed-tick>=15):
                on_progress(str(label)+(f' · queue position {rank}' if rank is not None else '')+f' · elapsed {elapsed}s')
                last=key;tick=elapsed
            time.sleep(1)
        return job.result()
    except Exception as error:
        explained=explain(error)
        if explained.reason=='quota':
            from provider_pool import set_cooldown
            set_cooldown(explained)
        raise explained from error
