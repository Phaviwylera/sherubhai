"""Generate and dub one clip at a time. Completed shots survive quota/network failures."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from sheru_studio import assemble, validate

def duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(path)]))

def find_video(obj):
    if isinstance(obj,str) and Path(obj).suffix.lower() in ('.mp4','.webm','.mov') and Path(obj).is_file():
        return obj
    children=obj.values() if isinstance(obj,dict) else obj if isinstance(obj,(list,tuple)) else ()
    for child in children:
        result=find_video(child)
        if result: return result

def render(ep,folder):
    from direction import enrich,check
    from finishing import generate,make_voice,dub_and_finish,review,finish_master
    ep=check(enrich(ep))
    from quality import preflight
    preflight(ep)
    folder.mkdir(parents=True,exist_ok=True)
    marker=folder/'script.sha256'
    key=hashlib.sha256(json.dumps(ep,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if marker.exists() and marker.read_text()!=key:
        raise ValueError('Script changed. Choose a new output folder for this revision.')
    marker.write_text(key)
    reference=folder/'reference.png'
    if not reference.exists():
        shutil.copy2(Path(__file__).parent/'assets'/'sheru-reference.png',reference)
    from provider import open_provider,preflight_episode
    connection=None
    if any(not shot.get('imported_clip') for shot in ep['shots']):
        connection=open_provider(folder)
        preflight_episode(ep,folder,connection,range(len(ep['shots'])))
    clips=folder/'clips'
    clips.mkdir(exist_ok=True)
    for i,shot in enumerate(ep['shots']):
        clip=clips/f'{i+1:02}.mp4'
        report_path=folder/f'review-{i+1:02}.json'
        if clip.exists() and report_path.exists():
            prior=json.loads(report_path.read_text(encoding='utf8'))
            if prior.get('status')=='fail': raise ValueError(f'Shot {i+1} requires correction. Open Studio.cmd to inspect and regenerate it.')
            continue
        workspace=folder/f'shot-{i+1:02}'
        workspace.mkdir(exist_ok=True)
        from finishing import safe_file
        imported=shot.get('imported_clip')
        voice=None
        if shot.get('voice'): voice=safe_file(folder,shot['voice'])
        elif not imported:
            voice=workspace/'voice.mp3'
            make_voice(shot,voice)
        raw=safe_file(folder,imported) if imported else generate(ep,i,workspace,folder,connection,print)
        dub_and_finish(raw,voice,ep,i,clip,folder)
        report=review(clip,shot,ep['settings'],reference,workspace)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        if report['status']=='fail': raise ValueError(f'Shot {i+1} requires correction. Open Studio.cmd to inspect and regenerate it.')
    assembly=folder/'assembly.mp4'
    if not assembly.exists(): assemble(ep,clips,assembly)
    finish_master(assembly,folder/'master-preview.mp4',ep,folder)
    print('Preview:',folder/'master-preview.mp4','Review native Tamil, full-speed physics, lip sync and sound timing before release.')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('doctor')
    r=sub.add_parser('render')
    r.add_argument('episode',type=Path)
    r.add_argument('folder',type=Path)
    a=p.parse_args()
    if a.cmd=='doctor':
        missing=[x for x in ['ffmpeg','ffprobe','ollama'] if not shutil.which(x)]
        missing += [x for x in ['gradio_client','edge_tts','googleapiclient','requests','flask','PIL','faster_whisper'] if not importlib.util.find_spec(x)]
        if missing: raise SystemExit('Missing: '+', '.join(missing))
        print('Local tools available.')
    else: render(validate(json.loads(a.episode.read_text(encoding='utf8'))),a.folder)
