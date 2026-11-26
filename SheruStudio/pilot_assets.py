"""Conservative migration of the bundled pilot's missing shot frames."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def apply(doc,folder):
    bundle=ROOT/'assets'/'pilot-frames'
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf8'))
    fields=manifest['fields']
    signature=hashlib.sha256(json.dumps([{k:s.get(k) for k in fields} for s in doc['shots']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    reference=Path(folder)/'reference.png'
    if signature not in manifest['signatures'] or not reference.exists() or hashlib.sha256(reference.read_bytes()).hexdigest()!=manifest['reference_sha256']:
        return []
    filled=[]
    for index,(shot,frame,contract) in enumerate(zip(doc['shots'],manifest['frames'],manifest['contracts'])):
        if shot.get('first_frame') or shot.get('imported_clip'):continue
        name='pilot-frame-v1-'+frame+'.png'
        shutil.copy2(bundle/(frame+'.png'),Path(folder)/name)
        shot.update(contract)
        shot.update(first_frame=name,frame_origin='bundled-pilot-v1')
        filled.append(index+1)
    return filled


def apply_voices(doc,folder):
    """Use prerecorded stock lines only when the stock contracts and reference match."""
    bundle=ROOT/'assets'/'pilot-frames'
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf8'))
    signature=hashlib.sha256(json.dumps([{k:s.get(k) for k in manifest['fields']} for s in doc['shots']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    reference=Path(folder)/'reference.png'
    if signature not in manifest['signatures'] or not reference.is_file() or hashlib.sha256(reference.read_bytes()).hexdigest()!=manifest['reference_sha256']:return []
    voice_folder=ROOT/'assets'/'pilot-voices'
    lines=json.loads((voice_folder/'manifest.json').read_text(encoding='utf8'))['shots']
    changed=[]
    for index,(shot,recording) in enumerate(zip(doc['shots'],lines)):
        if shot.get('voice') or shot.get('imported_clip'):continue
        if (shot['speaker'],shot['line'])!=(recording['speaker'],recording['line']):continue
        source=voice_folder/recording['file']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=recording['sha256']:raise ValueError('A bundled Tamil recording failed its integrity check.')
        name='pilot-v1-'+recording['file'];shutil.copy2(source,Path(folder)/name)
        shot['voice']=name;changed.append(index+1)
    return changed
