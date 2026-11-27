"""Persistent, bounded production retries; never fabricates review approval."""
import json,os,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PATH=Path(os.getenv('SHERU_DATA') or ROOT/'studio-data')/'automation.json'
lock=threading.RLock()
DEFAULT={'enabled':False,'state':'idle','run':None,'retry_at':None,'retry_count':0,'max_retries':8,'message':'Start automatic production to render the current episode and prepare its growth plan.'}

def state():
    with lock:
        if not PATH.exists():return dict(DEFAULT)
        return dict(DEFAULT,**json.loads(PATH.read_text(encoding='utf8')))

def save(**updates):
    with lock:
        data=state();data.update(updates)
        temporary=PATH.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(PATH)
        return data

def after_run(run):
    data=state()
    if not data['enabled'] or data['run']!=run['id']:return
    output=run['output']
    if run['state'] in ('review','approved'):
        if not output.get('master'):
            save(state='review',retry_at=None,message='Selected shot is ready for review. A full episode has not been assembled by this shot-only task.')
            return
        save(state='review',retry_at=None,message='Episode rendered. Growth research is being prepared. Full-speed review is still required before publication.')
        return 'growth'
    reason=output.get('retry_reason')
    if run['state']=='blocked' and reason in ('quota','network','unavailable','quality') and data['retry_count']<data['max_retries']:
        delay=output.get('retry_after') or (21600 if reason=='quota' else min(3600,300*2**data['retry_count']))
        save(state='waiting',retry_at=time.time()+max(60,delay),retry_count=data['retry_count']+1,message=('Automatic quality recovery is queued. Existing motion footage and completed shots are retained.' if reason=='quality' else 'Waiting for '+reason+'. Completed shots are retained. All Hugging Face routes share this wait; no quota switching.'))
    else:
        save(state='blocked',retry_at=None,message=output.get('error','Production stopped. Open the run diagnostic for the required input.'))

def due(data,now=None):
    return bool(data.get('enabled') and data.get('state')=='waiting' and data.get('retry_at') and data['retry_at']<=(time.time() if now is None else now))
