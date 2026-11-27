"""Credential-redacted application and per-run diagnostics."""
import contextvars
from datetime import datetime,timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading
import traceback
import uuid

ROOT=Path(__file__).resolve().parent
LOGS=Path(os.getenv('SHERU_LOGS') or ROOT/'logs')
LOGS.mkdir(parents=True,exist_ok=True)
context=contextvars.ContextVar('studio_diagnostics',default={})
write_lock=threading.Lock()
logger=logging.getLogger('sheru.studio')
if not logger.handlers:
    handler=RotatingFileHandler(LOGS/'studio.log',maxBytes=5*1024*1024,backupCount=3,encoding='utf8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate=False
SENSITIVE={'token','access_token','refresh_token','api_key','client_secret','authorization','password','secret','code_verifier','oauth_code'}

def redact(value):
    if isinstance(value,dict):
        return {key:'[REDACTED]' if str(key).lower() in SENSITIVE else redact(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [redact(item) for item in value]
    if not isinstance(value,str):return value
    value=re.sub(r'\bhf_[A-Za-z0-9]{6,}\b','[REDACTED_HF_TOKEN]',value)
    value=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b','[REDACTED_API_KEY]',value)
    value=re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+','Bearer [REDACTED]',value)
    value=re.sub(r'(?i)((?:token|access_token|refresh_token|api_key|client_secret|password|oauth_code)[\"\']?\s*[:=]\s*[\"\']?)[^\s&,\"\']+',r'\1[REDACTED]',value)
    value=re.sub(r'(https?://[^\s"\'<>?]+)\?[^\s"\'<>]+',r'\1?[REDACTED_QUERY]',value)
    for key in ('HF_TOKEN','IG_TOKEN','OPENAI_API_KEY','YOUTUBE_API_KEY','IG_APP_SECRET'):
        secret=os.getenv(key)
        if secret and len(secret)>=6:value=value.replace(secret,'[REDACTED]')
    return value

def set_context(**values):
    combined=dict(context.get())
    combined.update(values)
    context.set(combined)

def record(level,message,**details):
    entry=redact(dict(at=datetime.now(timezone.utc).isoformat(),level=level,message=str(message)[:16000],**context.get(),details=details))
    line=json.dumps(entry,ensure_ascii=False,default=str)
    logger.log(getattr(logging,level,logging.INFO),line)
    rid=entry.get('run')
    if isinstance(rid,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',rid):
        try:
            with write_lock,(LOGS/('run-'+rid+'.jsonl')).open('a',encoding='utf8') as file:file.write(line+'\n')
        except OSError:
            logger.error(json.dumps({'level':'ERROR','message':'Could not append the run diagnostics file.'}))
    return entry

def classify(error,stage=''):
    message=str(error)
    lower=message.lower()
    code='UNEXPECTED_ERROR'
    summary='A production step failed.'
    action='Download the run log and inspect the exception chain and failed step.'
    affected=[]
    match=re.search(r'shot(?:\(s\)|s)?\s+([\d, ]+)',message,re.I)
    if match:affected=[int(value) for value in re.findall(r'\d+',match[1])]
    if getattr(error,'reason','')=='quality':
        code='AUTOMATIC_QUALITY_REPAIR';summary='Automatic quality repair is queued.'
        action='The worker will retry from retained footage. Audio-only failures do not request another silent video. Follow the repair stages and retry time in Automatic Production.'
    elif 'first-frame' in lower and ('missing' in lower or 'need' in lower):
        code='MISSING_FIRST_FRAMES';summary='Shot-opening images are missing.'
        action='Use the bundled pilot frames when this is the stock pilot, or upload the opening image in each affected shot inspector.'
    elif getattr(error,'reason','')=='quota' or any(word in lower for word in ('quota','rate-limit','rate limit')):
        code='PROVIDER_LIMIT';summary='The provider cannot fit the next request into the remaining GPU allowance.'
        action='Preview retained shots in Review room. Resume this run after the displayed provider reset; a new full render would start over.'
    elif any(word in lower for word in ('cannot be accessed','cannot access','repositorynotfound','denied access','401','403')):
        code='PROVIDER_ACCESS';summary='The configured provider could not be accessed.'
        action='Open Connections → Video provider, verify the Space and token settings, then use Save & test provider.'
    elif 'endpoint' in lower and any(word in lower for word in ('input','compatible','found')):
        code='PROVIDER_SCHEMA';summary='The video API does not match the required inputs.'
        action='Check the endpoint shown by Test provider and select an image-to-video endpoint.'
    elif 'endpoint exposes' in lower or ('shot' in lower and 'seconds' in lower and ('limit' in lower or 'retime' in lower)):
        code='SHOT_DURATION';summary='A shot exceeds the supported duration.'
        action='Retime or split the affected shot, keeping the full episode duration correct.'
    elif 'tamil' in lower and any(word in lower for word in ('exceeds','longer','slot')):
        code='VOICE_TIMING';summary='Recorded Tamil does not fit its shot.'
        action='Split the dialogue or adjust the approved shot timing before spending video quota.'
    elif 'media operation failed' in lower or 'ffmpeg' in lower or 'ffprobe' in lower:
        code='MEDIA_PROCESSING';summary='A media-processing command failed.'
        action='Open the technical details for the exact command error and verify the source media and FFmpeg installation.'
    elif isinstance(error,(ConnectionError,TimeoutError)) or any(word in lower for word in ('connection refused','timed out','timeout')):
        code='CONNECTION';summary='A service connection failed or timed out.'
        action='Check the stage and service host in the technical details, then verify the local model service or remote provider.'
    elif isinstance(error,ValueError):
        code='INVALID_INPUT';summary='A required input or review check needs attention.'
        action=message[:1600]
    return dict(code=code,summary=summary,next_step=action,affected_shots=affected,stage=stage)

def capture(error,**extra):
    base=context.get()
    result=classify(error,base.get('stage',''))
    result.update(id=uuid.uuid4().hex[:12],exception=type(error).__name__,message=str(error)[:16000],at=datetime.now(timezone.utc).isoformat(),**extra)
    chain=[]
    current=error
    seen=set()
    while current and id(current) not in seen and len(chain)<8:
        seen.add(id(current))
        response=getattr(current,'response',None)
        status=getattr(response,'status_code',None) or getattr(current,'code',None)
        url=getattr(getattr(current,'request',None),'url',None) or getattr(current,'url',None)
        item=dict(exception=type(current).__name__,message=str(current)[:8000])
        if isinstance(status,int):item['http_status']=status
        if url:item['url']=str(url)
        chain.append(item)
        current=current.__cause__ or current.__context__
    if getattr(error,'quota',None):result['quota']=error.quota
    if base.get('shot') and not result['affected_shots']:result['affected_shots']=[base['shot']]
    result['chain']=chain
    result['traceback']=''.join(traceback.format_exception(type(error),error,error.__traceback__))[-24000:]
    result=redact(result)
    record('ERROR',result['summary'],diagnostic=result)
    return result

def run_report(rid,run,events):
    header={'run':rid,'kind':run['kind'],'state':run['state'],'revision':run['revision']}
    output=run.get('output',{})
    text='SHERU STUDIO RUN DIAGNOSTICS\n'+json.dumps(header,ensure_ascii=False,indent=2)
    if output.get('diagnostic'):
        text+='\n\nERROR DETAILS\n'+json.dumps(output['diagnostic'],ensure_ascii=False,indent=2)
    elif output.get('error'):
        text+='\n\nLEGACY ERROR\n'+str(output['error'])+'\nDetailed traceback was not recorded by the earlier application version.'
    text+='\n\nTIMESTAMPED EVENTS\n'
    for item in events:
        stamp=datetime.fromtimestamp(item['at'],timezone.utc).isoformat()
        level=item.get('level') or ('ERROR' if item['stage']=='Needs attention' else 'INFO')
        text+=stamp+' '+level+' '+item['stage']+' | '+item['message']+'\n'
    path=LOGS/('run-'+rid+'.jsonl')
    if path.exists():text+='\n\nSTRUCTURED RUN LOG\n'+path.read_text(encoding='utf8',errors='replace')
    return redact(text)
