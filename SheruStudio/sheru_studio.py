"""Sheru Studio starter. Python 3.10+, FFmpeg/ffprobe; local Ollama for writing.
python sheru_studio.py pilot episode01.json
python sheru_studio.py write episode02.json "Sheru buys a bus ticket" --model YOUR_OLLAMA_MODEL
python sheru_studio.py review episode01.json --model YOUR_OLLAMA_MODEL
python sheru_studio.py assemble episode01.json clips01 episode01.mp4
Supply one finished, dubbed clip per shot, named 01.mp4, 02.mp4, etc.
Assembly checks media and duration, fits each clip to its slot and retains order.
Listen to the final mix and review eyelines and mouth movement before release.
"""
import argparse
import json
import math
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

CAST = "Sheru: lion head, human body, ivory three-piece suit, gold pendant. Tea master: checked shirt. Murugan: driver in pale blue."
STYLE = ("Write natural spoken Chennai Tamil, with numbers written as spoken. "
         "Use a setup, reversal and punchline. Keep the cast consistent. "
         "Each shot has seconds, speaker, listener, line (Tamil), action and sfx (English). "
         "One speaker per shot. Face the listener before speaking. Total exactly 30 seconds.")
PILOT = [
    (5,'Sheru','tea master','அண்ணே, ஒரு டீ. ஸ்ட்ராங்கா.','Car stops. Cut to Sheru facing the master across the counter.','Soft brake, door latch.'),
    (5,'tea master','Sheru','என்னடா ஷேரு, புதுக் காரா? எவ்வளவு?','Set glass down. Look back from car to Sheru before speaking.','Glass taps counter.'),
    (4,'Sheru','tea master','எட்டு கோடி, அண்ணே.','Look at master, deliver casually, then sip.','Faint kettle simmer.'),
    (7,'tea master','Sheru','போன தடவை குடிச்ச டீக்கு பன்னெண்டு ரூபா பாக்கி. ஞாபகம் இருக்கா?','Find ledger entry, then look up at Sheru.','One page turn before dialogue.'),
    (5,'Sheru','Murugan','முருகா… இருபத்தி நாலு ரூபா இருக்காடா?','Pat pocket, turn to open driver window. Make eye contact.','Soft fabric rustle.'),
    (4,'Murugan','Sheru','எனக்கும் ஒரு டீ சொல்லுங்கண்ணே!','Answer Sheru face-to-face. Cut to tea master opening pen.','One pen click after line.')]
KEYS = ('seconds','speaker','listener','line','action','sfx')

def validate(ep):
    shots = ep['shots']
    if not shots or not all(isinstance(s,dict) and all(isinstance(s.get(k),str) and s[k].strip() for k in KEYS[1:]) and type(s.get('seconds')) in (int,float) and 0 < s['seconds'] <= 30 and s['speaker'] != s['listener'] and any('\u0b80' <= c <= '\u0bff' for c in s['line']) for s in shots):
        raise ValueError('Invalid shot structure or Tamil dialogue')
    if not math.isclose(sum(s['seconds'] for s in shots),30,abs_tol=.001):
        raise ValueError('Episode must total 30 seconds')
    return ep

def ask(model,prompt):
    payload = dict(model=model,prompt=prompt,stream=False,format='json')
    host = os.getenv('OLLAMA_HOST', '127.0.0.1:11434')
    if not host.startswith(('http://', 'https://')):
        host = 'http://' + host
    req = urllib.request.Request(host.rstrip('/') + '/api/generate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=600) as response:
        return json.loads(json.load(response)['response'])

def save(ep,path):
    validate(ep)
    with path.open('x',encoding='utf8') as f:
        json.dump(ep,f,ensure_ascii=False,indent=2)

def assemble(ep,folder,out):
    if out.exists():
        raise ValueError('Output already exists')
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        root = Path(tmp)
        parts = []
        for i,s in enumerate(ep['shots'],1):
            clip = folder / f'{i:02}.mp4'
            streams = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(clip)]))['streams']
            for kind in ('video','audio'):
                stream = next((v for v in streams if v['codec_type']==kind),{})
                if float(stream.get('duration',0)) + .045 < s['seconds']:
                    raise ValueError(f'{clip}: missing or short {kind}')
            part = f'part{i:02}.mp4'
            subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(clip),'-t',str(s['seconds']),'-map','0:v:0','-map','0:a:0','-vf','fps=25,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(root/part)],check=True)
            parts.append("file '"+part+"'")
        (root/'order.txt').write_text('\n'.join(parts))
        result=root/'master.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','concat','-safe','1','-i',str(root/'order.txt'),'-c','copy','-movflags','+faststart',str(result)],check=True)
        result.rename(out)

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    cmds=p.add_subparsers(dest='cmd',required=True)
    for name in ('pilot','write','review','assemble'):
        cmd=cmds.add_parser(name)
        cmd.add_argument('episode',type=Path)
        if name=='write': cmd.add_argument('idea')
        if name in ('write','review'): cmd.add_argument('--model',required=True)
        if name=='assemble':
            cmd.add_argument('clips',type=Path)
            cmd.add_argument('output',type=Path)
    a=p.parse_args()
    if a.cmd=='pilot':
        save({'shots':[dict(zip(KEYS,row)) for row in PILOT]},a.episode)
    elif a.cmd=='write':
        save(ask(a.model,CAST+' '+STYLE+' Return JSON with shots array. Episode idea: '+a.idea),a.episode)
    else:
        ep=validate(json.loads(a.episode.read_text(encoding='utf8')))
        if a.cmd=='review':
            print(json.dumps(ask(a.model,'Act as a native Chennai Tamil dialogue editor. Review each shot for oral rhythm, timing, respectful address, eyelines and continuity. Return JSON with verdict, shot-specific reasons and suggested Tamil replacements. '+json.dumps(ep,ensure_ascii=False)),ensure_ascii=False,indent=2))
        else:
            assemble(ep,a.clips,a.output)

if __name__=='__main__':
    main()
