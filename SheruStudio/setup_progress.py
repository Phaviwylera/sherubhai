"""Dependency-free setup progress, verified downloads, extraction and process heartbeats."""
import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

UNSET=object()

class Progress:
    def __init__(self,label,status=None):
        self.label=label
        self.status=Path(status) if status else None
        self.started=time.monotonic()
        self.updated=self.started
        self.done=0
        self.total=None
        self.phase='Connecting'
        self.finished=threading.Event()
        self.lock=threading.Lock()
        self.thread=None
        self.previous=None

    def update(self,done=None,total=UNSET,phase=None):
        with self.lock:
            if done is not None:
                if done!=self.done:self.updated=time.monotonic()
                self.done=done
            if total is not UNSET:self.total=total
            if phase is not None:self.phase=phase

    def snapshot(self):
        with self.lock:
            elapsed=time.monotonic()-self.started
            return dict(label=self.label,phase=self.phase,bytes_done=self.done,bytes_total=self.total,elapsed_seconds=round(elapsed,1),idle_seconds=round(time.monotonic()-self.updated,1),updated_at=time.time())

    def draw(self):
        last_bytes=self.done
        last_tick=time.monotonic()
        while not self.finished.wait(1):
            item=self.snapshot()
            stamp=time.monotonic()
            speed=max(0,(item['bytes_done']-last_bytes)/max(.001,stamp-last_tick))
            last_tick=stamp;last_bytes=item['bytes_done']
            total=item['bytes_total']
            percent=min(100,item['bytes_done']/total*100) if total else None
            if percent is None:
                bar='[working]'
                sizes=f"{item['bytes_done']/1048576:.1f} MB" if item['bytes_done'] else ''
            else:
                filled=min(24,int(percent*24/100))
                bar='['+'='*filled+' '*(24-filled)+f'] {percent:5.1f}%'
                sizes=f"{item['bytes_done']/1048576:.1f}/{total/1048576:.1f} MB"
            eta=(total-item['bytes_done'])/speed if total and speed>0 else None
            waiting=f" | waiting {item['idle_seconds']:.0f}s for data/output" if item['idle_seconds']>=15 else ''
            line=f"{bar} {self.label} | {item['phase']} | {sizes} | {speed/1048576:.2f} MB/s | elapsed {item['elapsed_seconds']:.0f}s"
            if not total and not item['bytes_done']:
                line=f"[working] {self.label} | {item['phase']} | elapsed {item['elapsed_seconds']:.0f}s"
            if eta is not None:line+=f' | ETA {eta:.0f}s'
            line+=waiting
            if sys.stdout.isatty():
                # Stay within one terminal row, including on narrow consoles.
                width=max(40,shutil.get_terminal_size((120,20)).columns-1)
                if item['idle_seconds']>=15 and self.previous!='waiting':
                    print('\nWaiting for new bytes/output; elapsed time continues below.',flush=True)
                    self.previous='waiting'
                elif item['idle_seconds']<15:self.previous=None
                print('\r'+line[:width].ljust(width),end='',flush=True)
            elif item['elapsed_seconds']<2 or int(item['elapsed_seconds'])%5==0:
                print(line,flush=True)
            if self.status:
                try:
                    self.status.parent.mkdir(parents=True,exist_ok=True)
                    temp=self.status.with_suffix('.writing')
                    temp.write_text(json.dumps(dict(item,speed_bytes_per_second=round(speed),estimated_seconds_remaining=round(eta) if eta is not None else None)),encoding='utf8')
                    temp.replace(self.status)
                except OSError:
                    pass  # Status telemetry must not break a healthy installation.

    def __enter__(self):
        print(self.label,flush=True)
        self.thread=threading.Thread(target=self.draw,daemon=True)
        self.thread.start()
        return self

    def __exit__(self,kind,error,trace):
        self.finished.set()
        self.thread.join(timeout=2)
        if sys.stdout.isatty():print()
        self.phase='Failed' if error else 'Complete'
        if self.status:
            try:self.status.write_text(json.dumps(self.snapshot()),encoding='utf8')
            except OSError:pass
        print(self.phase+': '+self.label,flush=True)


def sha256(path,progress=None):
    result=hashlib.sha256()
    total=path.stat().st_size
    done=0
    with path.open('rb') as file:
        while data:=file.read(1024*1024):
            result.update(data);done+=len(data)
            if progress:progress.update(done,total,'Checking downloaded file')
    return result.hexdigest()


def download(url,target,expected=None,status=None,refresh=False):
    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    pending=target.with_name(target.name+'.partial')
    checkpoint=target.with_name(target.name+'.partial.json')
    receipt=target.with_name(target.name+'.download.json')
    with Progress('Download '+target.name,status) as progress:
        if target.is_file() and not refresh:
            saved={}
            if receipt.exists():
                try:saved=json.loads(receipt.read_text())
                except (OSError,ValueError):pass
            wanted=expected or (saved.get('sha256') if saved.get('url')==url else None)
            if wanted and sha256(target,progress).lower()==wanted.lower():
                print('Reusing verified completed download.',flush=True)
                return
        for attempt in range(1,4):
            progress.update(0,None,phase=f'Attempt {attempt}/3: connecting')
            try:
                saved={}
                if checkpoint.exists():
                    try:saved=json.loads(checkpoint.read_text())
                    except (OSError,ValueError):pass
                validator=saved.get('etag') or saved.get('last_modified')
                offset=pending.stat().st_size if pending.is_file() and saved.get('url')==url and validator else 0
                headers={'User-Agent':'SheruStudio-Setup/1.0','Accept-Encoding':'identity'}
                if offset:
                    headers.update({'Range':f'bytes={offset}-','If-Range':validator})
                elif pending.exists():
                    print('Starting this incomplete download again: no usable server resume validator.',flush=True)
                request=urllib.request.Request(url,headers=headers)
                try:response=urllib.request.urlopen(request,timeout=30)
                except urllib.error.HTTPError as error:
                    if error.code==416 and offset:
                        checkpoint.write_text('{}')
                        raise OSError('Server rejected the saved range; next attempt starts fresh.') from error
                    raise
                with response:
                    if url.startswith('https://') and not response.geturl().startswith('https://'):
                        raise ValueError('Download redirected away from HTTPS.')
                    partial=response.status==206
                    if partial:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if not match or int(match[1])!=offset:
                            checkpoint.write_text('{}')
                            raise OSError('Server returned an inconsistent resume range.')
                        total=int(match[3])
                        if validator and saved.get('etag') and response.headers.get('ETag') not in (None,saved['etag']):
                            checkpoint.write_text('{}')
                            raise OSError('Server changed the file; next attempt starts fresh.')
                    else:
                        offset=0
                        size=response.headers.get('Content-Length')
                        total=int(size) if size and size.isdigit() else None
                    etag=response.headers.get('ETag')
                    if etag and etag.startswith('W/'):etag=None
                    checkpoint.write_text(json.dumps({'url':url,'etag':etag,'last_modified':response.headers.get('Last-Modified')}))
                    done=offset
                    progress.update(done,total,'Receiving bytes'+(' (resumed)' if offset else ''))
                    with pending.open('ab' if partial and offset else 'wb') as file:
                        while data:=response.read1(256*1024):
                            file.write(data);done+=len(data)
                            progress.update(done,total)
                        file.flush()
                        os.fsync(file.fileno())
                    if total is not None and done!=total:
                        raise OSError('Connection ended before the advertised file size arrived.')
                actual=sha256(pending,progress)
                if expected and actual.lower()!=expected.lower():
                    checkpoint.write_text('{}')
                    raise OSError('File checksum differs from the published checksum; next attempt starts fresh.')
                pending.replace(target)
                receipt.write_text(json.dumps({'url':url,'sha256':actual,'bytes':target.stat().st_size}))
                checkpoint.unlink(missing_ok=True)
                return
            except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException) as error:
                print(f'\nAttempt {attempt}/3 stopped: {type(error).__name__}: {error}',flush=True)
                if attempt==3:raise RuntimeError('Download failed after three attempts. Rerun Setup to retry. Completed tools are preserved.') from error
                progress.update(phase='Retrying connection')
                time.sleep(2*attempt)


def extract(archive,destination,status=None):
    destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    with Progress('Extract '+Path(archive).name,status) as progress,zipfile.ZipFile(archive) as source:
        entries=source.infolist()
        total=sum(entry.file_size for entry in entries)
        done=0
        for entry in entries:
            name=entry.filename.replace('\\','/')
            target=(destination/name).resolve()
            if not target.is_relative_to(destination) or ':' in name or (entry.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Archive contains an unsafe member path.')
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with source.open(entry) as reader,target.open('wb') as writer:
                while data:=reader.read(1024*1024):
                    writer.write(data);done+=len(data)
                    progress.update(done,total,'Writing extracted files')
        progress.update(done,total,'Archive integrity checked')


def copy_tree(source,destination,status=None):
    source=Path(source).resolve()
    destination=Path(destination).resolve()
    if source==destination or destination.is_relative_to(source) or source.is_relative_to(destination):raise ValueError('Copy source and destination must be separate staging and tool directories.')
    entries=list(source.rglob('*'))
    if any(item.is_symlink() for item in entries):raise ValueError('Staging directory contains a symbolic link.')
    total=sum(item.stat().st_size for item in entries if item.is_file())
    destination.mkdir(parents=True,exist_ok=True)
    marker=destination/'.sheru-installing'
    marker.write_text('Tool file copy is incomplete until this marker is removed.')
    done=0
    with Progress('Install files '+destination.name,status) as progress:
        for item in entries:
            target=(destination/item.relative_to(source)).resolve()
            if not target.is_relative_to(destination):raise ValueError('Copy target leaves the application directory.')
            if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with item.open('rb') as reader,target.open('wb') as writer:
                while data:=reader.read(1024*1024):
                    writer.write(data);done+=len(data)
                    progress.update(done,total,'Copying tool files')
        marker.unlink()

def command(args,label,status=None):
    with Progress(label,status) as progress:
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',bufsize=1)
        progress.update(phase=f'Process {process.pid} is running')
        try:
            for line in process.stdout:
                # End the status line before writing actual installer output.
                print('\n'+line.rstrip(),flush=True)
                with progress.lock:progress.updated=time.monotonic()
            code=process.wait()
            if code:raise RuntimeError(f'{label} exited with code {code}.')
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
            raise


def pull_model(host,model,status=None):
    if not host.startswith(('http://','https://')):host='http://'+host
    with Progress('Download script model '+model,status) as progress:
        for attempt in range(1,4):
            try:
                progress.update(0,None,f'Attempt {attempt}/3: requesting model manifest')
                payload=json.dumps({'model':model,'stream':True}).encode()
                request=urllib.request.Request(host.rstrip('/')+'/api/pull',data=payload,headers={'Content-Type':'application/json'})
                success=False
                with urllib.request.urlopen(request,timeout=30) as response:
                    for line in response:
                        message=json.loads(line)
                        if message.get('error'):raise RuntimeError(message['error'])
                        phase=message.get('status','Pulling model')
                        layer=message.get('digest','').removeprefix('sha256:')[:8]
                        total=message.get('total')
                        done=message.get('completed')
                        progress.update(done,total,phase+(' · layer '+layer if layer else ''))
                        if phase=='success':success=True
                if not success:raise OSError('Model stream ended before success.')
                return
            except (OSError,ValueError,RuntimeError,urllib.error.URLError,http.client.HTTPException) as error:
                print(f'\nModel attempt {attempt}/3 stopped: {error}',flush=True)
                if attempt==3:raise RuntimeError('Model download failed. Cached model layers are retained; rerun Setup to resume.') from error
                time.sleep(2*attempt)


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    for name in ('download','extract','copy','run','pull'):
        item=sub.add_parser(name)
        item.add_argument('--status')
        if name=='download':
            item.add_argument('url');item.add_argument('target');item.add_argument('--sha256');item.add_argument('--refresh',action='store_true')
        elif name=='extract':
            item.add_argument('archive');item.add_argument('destination')
        elif name=='copy':
            item.add_argument('source');item.add_argument('destination')
        elif name=='pull':
            item.add_argument('--host',default='127.0.0.1:11435');item.add_argument('--model',required=True)
        else:
            item.add_argument('--label',required=True)
            item.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if args.action=='download':download(args.url,args.target,args.sha256,args.status,args.refresh)
    elif args.action=='extract':extract(args.archive,args.destination,args.status)
    elif args.action=='copy':copy_tree(args.source,args.destination,args.status)
    elif args.action=='pull':pull_model(args.host,args.model,args.status)
    else:
        cmd=args.command[1:] if args.command[:1]==['--'] else args.command
        if not cmd:parser.error('A command is required.')
        command(cmd,args.label,args.status)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:
        print('\nSetup cancelled. Completed downloads and tools are retained.',flush=True)
        raise SystemExit(130)
    except Exception as error:
        print('\nSETUP ERROR: '+str(error),file=sys.stderr,flush=True)
        raise SystemExit(1)
