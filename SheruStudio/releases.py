"""Queue reviewed masters and publish due jobs. Run from this installation folder."""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB=Path(os.getenv('SHERU_RELEASES') or Path(__file__).with_name('releases.sqlite'))

def digest(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def db():
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    with c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS jobs (episode TEXT, platform TEXT, path TEXT, hash TEXT, title TEXT, url TEXT, due REAL, state TEXT, remote TEXT, PRIMARY KEY(episode,platform))')
        if 'last_error' not in {row['name'] for row in c.execute('PRAGMA table_info(jobs)')}:
            c.execute("ALTER TABLE jobs ADD COLUMN last_error TEXT NOT NULL DEFAULT '{}'")
        columns={row['name'] for row in c.execute('PRAGMA table_info(jobs)')}
        if 'metadata' not in columns:
            c.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        if 'attempts' not in columns:
            c.execute('ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        if 'retry_at' not in columns:
            c.execute('ALTER TABLE jobs ADD COLUMN retry_at REAL')
    return c

def read_db():
    """A plain connection for readers, so the UI poll never waits on the uploader's write lock."""
    if not DB.exists():return db()
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        c.close();return db()
    return c

def google():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from accounts import stored
    cfg = None
    try:
        cfg = stored('youtube')
    except RuntimeError:
        if not Path('youtube-token.json').exists(): raise
    auth = Credentials.from_authorized_user_info(cfg) if cfg else Credentials.from_authorized_user_file('youtube-token.json')
    return build('youtube','v3',credentials=auth)

def credentials(platform):
    """Fail before any upload attempt when the account is not usable."""
    from accounts import stored
    try:
        if platform=='youtube':
            if stored('youtube'):return None
        else:
            ig=dict(version=os.getenv('IG_GRAPH_VERSION'),user=os.getenv('IG_USER_ID'),token=os.getenv('IG_TOKEN'))
            if not all(ig.values()): ig=stored('instagram')
            if ig and ig.get('expires',float('inf'))>time.time(): return ig
    except Exception:
        pass
    if platform=='youtube' and Path('youtube-token.json').exists(): return None
    raise ValueError('Connect YouTube in Accounts.cmd' if platform=='youtube' else 'Connect Instagram in Accounts.cmd')

def prepare(row):
    """Offline checks. A job is only marked 'check' once these pass, so a local error cannot strand it."""
    if digest(row['path'])!=row['hash']: raise ValueError('Master changed since queueing')
    from growth import validate_metadata
    payload=json.loads(row['metadata']) if 'metadata' in row.keys() else {}
    fallback={'title':row['title'],'description':'','caption':row['title']}
    metadata=validate_metadata(row['platform'],payload or fallback)
    text=metadata['description'] if row['platform']=='youtube' else metadata['caption']
    if metadata['hashtags']: text+='\n\n'+' '.join(metadata['hashtags'])
    return dict(metadata=metadata,text=text,instagram=credentials(row['platform']))

def outcome(error):
    """'failed' means the platform definitely did not publish. Anything else may be half done."""
    status=getattr(getattr(error,'resp',None),'status',None)
    if status is None:status=getattr(getattr(error,'response',None),'status_code',None)
    if isinstance(status,int):return 'failed' if 400<=status<500 and status not in (408,409,425,429) else 'check'
    return 'failed' if isinstance(error,(ValueError,FileNotFoundError,KeyError)) else 'check'

def publish(row,prepared):
    metadata=prepared['metadata'];discovery_text=prepared['text']
    if digest(row['path'])!=row['hash']: raise ValueError('Master changed since queueing')
    if row['platform']=='youtube':
        from googleapiclient.http import MediaFileUpload
        video=google().videos().insert(part='snippet,status',body={'snippet':{'title':metadata['title'],'description':discovery_text,'tags':metadata['tags'],'categoryId':'23','defaultAudioLanguage':'ta'},'status':{'privacyStatus':'public','containsSyntheticMedia':True}},media_body=MediaFileUpload(row['path'],mimetype='video/mp4',resumable=True)).execute()
        return video['id']
    import requests
    ig=prepared['instagram']
    base='https://graph.instagram.com/'+ig['version']+'/'+ig['user']
    headers={'Authorization':'Bearer '+ig['token']}
    def call(method,url,**kwargs):
        r=requests.request(method,url,headers=headers,timeout=90,**kwargs)
        r.raise_for_status()
        return r.json()
    # Verify hosted bytes match the reviewed master before handing the URL to Meta.
    h=hashlib.sha256()
    with requests.get(row['url'],stream=True,timeout=90) as response:
        response.raise_for_status()
        for chunk in response.iter_content(2**20): h.update(chunk)
    if h.hexdigest()!=row['hash']: raise ValueError('Hosted video differs from reviewed master')
    container=call('POST',base+'/media',data={'media_type':'REELS','video_url':row['url'],'caption':discovery_text})['id']
    for _ in range(15):
        status=call('GET',base.rsplit('/',1)[0]+'/'+container,params={'fields':'status_code'})['status_code']
        if status=='FINISHED': break
        if status in ('ERROR','EXPIRED'): raise ValueError('Container '+status)
        time.sleep(20)
    else: raise TimeoutError('Instagram processing timeout')
    return call('POST',base+'/media_publish',data={'creation_id':container})['id']

def record(c,row,error,note):
    import diagnostics
    diagnostics.context.set({'stage':'Publish '+row['platform'],'episode':row['episode']})
    detail=diagnostics.capture(error)
    attempts=(row['attempts'] or 0)+1
    with c:c.execute('UPDATE jobs SET last_error=?,attempts=? WHERE episode=? AND platform=?',(json.dumps(detail,ensure_ascii=False),attempts,row['episode'],row['platform']))
    print(row['episode'],row['platform'],note,type(error).__name__,flush=True)
    return detail,attempts

def run(c):
    now=time.time()
    for row in c.execute("SELECT rowid AS sequence,* FROM jobs WHERE state='queued' AND due<=? AND (retry_at IS NULL OR retry_at<=?) ORDER BY due,rowid",(now,now)).fetchall():
        # Preserve episode order per platform across retries, held and failed uploads.
        earlier=c.execute("SELECT 1 FROM jobs WHERE platform=? AND state IN ('queued','check','failed') AND (due<? OR (due=? AND rowid<?)) LIMIT 1",(row['platform'],row['due'],row['due'],row['sequence'])).fetchone()
        if earlier:continue
        try:
            from growth import refresh_metadata
            saved=json.loads(row['metadata'])
            if saved:
                data=refresh_metadata(saved)
                if data!=saved:
                    with c: c.execute('UPDATE jobs SET metadata=?,title=? WHERE episode=? AND platform=?',(json.dumps(data,ensure_ascii=False),data.get('title') or row['title'],row['episode'],row['platform']))
                row=dict(row)
                row['metadata']=json.dumps(data,ensure_ascii=False)
            prepared=prepare(row)
        except Exception as e:
            # Nothing has been uploaded, so the job stays queued and is retried with backoff.
            detail,attempts=record(c,row,e,'needs input:')
            held=attempts>=3
            with c:c.execute('UPDATE jobs SET state=?,retry_at=? WHERE episode=? AND platform=?',('failed' if held else 'queued',None if held else now+min(3600,300*2**(attempts-1)),row['episode'],row['platform']))
            continue
        with c:
            count=c.execute("UPDATE jobs SET state='check',attempts=attempts+1 WHERE episode=? AND platform=? AND state='queued'",(row['episode'],row['platform'])).rowcount
        if not count: continue
        try:
            remote=publish(row,prepared)
            with c: c.execute("UPDATE jobs SET state='submitted',remote=?,last_error='{}',retry_at=NULL WHERE episode=? AND platform=?",(remote,row['episode'],row['platform']))
        except Exception as e:
            detail,attempts=record(c,row,e,'needs checking:')
            with c:c.execute('UPDATE jobs SET state=? WHERE episode=? AND platform=?',(outcome(e),row['episode'],row['platform']))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    s=p.add_subparsers(dest='cmd',required=True)
    for n in ('login-youtube','list','run','watch'): s.add_parser(n)
    q=s.add_parser('queue')
    for k in ('episode','master','title','when','https_url'): q.add_argument(k)
    r=s.add_parser('resolve')
    for k in ('episode','platform','remote_id_or_retry'): r.add_argument(k)
    a=p.parse_args()
    c=db()
    if a.cmd=='login-youtube':
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow=InstalledAppFlow.from_client_secrets_file('youtube-client.json',__import__('accounts').SCOPES)
        token_file=Path('youtube-token.json')
        token_file.write_text(flow.run_local_server(port=0).to_json())
        # The refresh token is a credential: keep this fallback file private.
        try: token_file.chmod(0o600)
        except OSError: pass
        print('Saved',token_file,'with owner-only permissions. Prefer Connect YouTube in Accounts.cmd, which uses the credential vault.',flush=True)
    elif a.cmd=='queue':
        when=datetime.fromisoformat(a.when)
        if not when.tzinfo or not a.https_url.startswith('https://'): p.error('Specify UTC offset and an HTTPS link to the exact video.')
        path=Path(a.master).resolve(strict=True)
        with c:
            for target in ('youtube','instagram'):
                c.execute('INSERT INTO jobs(episode,platform,path,hash,title,url,due,state,remote) VALUES(?,?,?,?,?,?,?,?,?)',(a.episode,target,str(path),digest(path),a.title,a.https_url,when.timestamp(),'queued',None))
    elif a.cmd=='resolve':
        retry=a.remote_id_or_retry=='retry'
        with c: c.execute("UPDATE jobs SET state=?,remote=?,attempts=0,retry_at=NULL WHERE episode=? AND platform=? AND state IN ('check','failed')",('queued' if retry else 'submitted',None if retry else a.remote_id_or_retry,a.episode,a.platform))
    elif a.cmd=='list':
        for row in c.execute('SELECT episode,platform,state,remote FROM jobs ORDER BY due'): print(*row)
    else:
        while True:
            run(c)
            if a.cmd=='run': break
            time.sleep(30)
