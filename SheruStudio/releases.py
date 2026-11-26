"""Queue reviewed masters and publish due jobs. Run from this installation folder."""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

def digest(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def db():
    c=sqlite3.connect(Path(__file__).with_name('releases.sqlite'),timeout=15)
    c.row_factory=sqlite3.Row
    with c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS jobs (episode TEXT, platform TEXT, path TEXT, hash TEXT, title TEXT, url TEXT, due REAL, state TEXT, remote TEXT, PRIMARY KEY(episode,platform))')
        if 'last_error' not in {row['name'] for row in c.execute('PRAGMA table_info(jobs)')}:
            c.execute("ALTER TABLE jobs ADD COLUMN last_error TEXT NOT NULL DEFAULT '{}'")
        if 'metadata' not in {row['name'] for row in c.execute('PRAGMA table_info(jobs)')}:
            c.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
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
    credentials = Credentials.from_authorized_user_info(cfg) if cfg else Credentials.from_authorized_user_file('youtube-token.json')
    return build('youtube','v3',credentials=credentials)

def publish(row):
    if digest(row['path'])!=row['hash']: raise ValueError('Master changed since queueing')
    from growth import validate_metadata
    payload=json.loads(row['metadata']) if 'metadata' in row.keys() else {}
    fallback={'title':row['title'],'description':'','caption':row['title']}
    metadata=validate_metadata(row['platform'],payload or fallback)
    discovery_text=metadata['description'] if row['platform']=='youtube' else metadata['caption']
    discovery_text += ('\n\n'+' '.join(metadata['hashtags'])) if metadata['hashtags'] else ''
    if row['platform']=='youtube':
        from googleapiclient.http import MediaFileUpload
        video=google().videos().insert(part='snippet,status',body={'snippet':{'title':metadata['title'],'description':discovery_text,'tags':metadata['tags'],'categoryId':'23','defaultAudioLanguage':'ta'},'status':{'privacyStatus':'public','containsSyntheticMedia':True}},media_body=MediaFileUpload(row['path'],mimetype='video/mp4',resumable=True)).execute()
        return video['id']
    import requests
    from accounts import stored
    ig = dict(version=os.getenv('IG_GRAPH_VERSION'),user=os.getenv('IG_USER_ID'),token=os.getenv('IG_TOKEN'))
    if not all(ig.values()): ig = stored('instagram')
    if not ig or ig.get('expires',float('inf')) <= time.time(): raise ValueError('Connect Instagram in Accounts.cmd')
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

def run(c):
    for row in c.execute("SELECT rowid AS sequence,* FROM jobs WHERE state='queued' AND due<=? ORDER BY due,rowid",(time.time(),)).fetchall():
        # Preserve episode order per platform across retries and uncertain uploads.
        earlier=c.execute("SELECT 1 FROM jobs WHERE platform=? AND state IN ('queued','check') AND (due<? OR (due=? AND rowid<?)) LIMIT 1",(row['platform'],row['due'],row['due'],row['sequence'])).fetchone()
        if earlier:continue
        with c:
            count=c.execute("UPDATE jobs SET state='check' WHERE episode=? AND platform=? AND state='queued'",(row['episode'],row['platform'])).rowcount
        if not count: continue
        try:
            from growth import refresh_metadata
            data=json.loads(row['metadata'])
            if data:
                data=refresh_metadata(data)
                with c: c.execute('UPDATE jobs SET metadata=?,title=? WHERE episode=? AND platform=?',(json.dumps(data,ensure_ascii=False),data.get('title') or row['title'],row['episode'],row['platform']))
                row=dict(row)
                row['metadata']=json.dumps(data,ensure_ascii=False)
            remote=publish(row)
            with c: c.execute("UPDATE jobs SET state='submitted',remote=? WHERE episode=? AND platform=?",(remote,row['episode'],row['platform']))
        except Exception as e:
            import diagnostics
            diagnostics.context.set({'stage':'Publish '+row['platform'],'episode':row['episode']})
            detail=diagnostics.capture(e)
            with c:c.execute('UPDATE jobs SET last_error=? WHERE episode=? AND platform=?',(json.dumps(detail,ensure_ascii=False),row['episode'],row['platform']))
            print(row['episode'],row['platform'],'needs checking:',type(e).__name__,flush=True)

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
        Path('youtube-token.json').write_text(flow.run_local_server(port=0).to_json())
    elif a.cmd=='queue':
        when=datetime.fromisoformat(a.when)
        if not when.tzinfo or not a.https_url.startswith('https://'): p.error('Specify UTC offset and an HTTPS link to the exact video.')
        path=Path(a.master).resolve(strict=True)
        with c:
            for target in ('youtube','instagram'):
                c.execute('INSERT INTO jobs(episode,platform,path,hash,title,url,due,state,remote) VALUES(?,?,?,?,?,?,?,?,?)',(a.episode,target,str(path),digest(path),a.title,a.https_url,when.timestamp(),'queued',None))
    elif a.cmd=='resolve':
        retry=a.remote_id_or_retry=='retry'
        with c: c.execute("UPDATE jobs SET state=?,remote=? WHERE episode=? AND platform=? AND state='check'",('queued' if retry else 'submitted',None if retry else a.remote_id_or_retry,a.episode,a.platform))
    elif a.cmd=='list':
        for row in c.execute('SELECT episode,platform,state,remote FROM jobs ORDER BY due'): print(*row)
    else:
        while True:
            run(c)
            if a.cmd=='run': break
            time.sleep(30)
