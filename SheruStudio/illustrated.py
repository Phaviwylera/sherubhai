"""Quota-independent illustrated/hybrid episode export. No character animation claim."""
import json,shutil
from pathlib import Path
import numpy as np
from finishing import run,seconds,safe_file,dub_and_finish,finish_master,make_voice


def wav(path,samples,rate=48000):
    import wave
    samples=np.clip(samples,-.95,.95)
    with wave.open(str(path),'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(rate);file.writeframes((samples*32767).astype('<i2').tobytes())

def cue(kind,path):
    sr=48000;n=int(sr*(.35 if kind=='glass' else .12));t=np.arange(n)/sr
    if kind=='glass':
        data=sum(np.sin(2*np.pi*f*t)*np.exp(-t*d)*a for f,d,a in [(1700,20,.12),(2500,28,.08),(4100,35,.04)])
    else:
        rng=np.random.default_rng(44);data=rng.normal(0,.13,n)*np.exp(-t*100)
        data+=np.sin(2*np.pi*650*t)*np.exp(-t*90)*.07
    wav(path,data);return path


def soundtrack(path,length):
    sr=48000;n=int(length*sr);rng=np.random.default_rng(22)
    noise=rng.standard_normal(n)
    spectrum=np.fft.rfft(noise);freq=np.fft.rfftfreq(n,1/sr)
    spectrum*=np.where((freq>90)&(freq<4500),1/np.sqrt(np.maximum(freq,90)),0)
    bed=np.fft.irfft(spectrum,n);bed*=.006/max(float(np.std(bed)),1e-9)
    fade=np.minimum(1,np.arange(n)/(sr*.15))*np.minimum(1,(n-np.arange(n))/(sr*.3))
    wav(path,bed*fade)




def render(ep,project,folder,on_progress=None,cached=None):
    """Render every slot. Reuse reviewed current-revision footage when supplied."""
    import copy
    ep=copy.deepcopy(ep);project=Path(project);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cached=cached or {};ordered=folder/'ordered';ordered.mkdir(exist_ok=True)
    output={'mode':'illustrated','shots':[],'limitations':'Illustrated panels with camera motion, not generated body motion or synchronized mouths.'}
    def note(stage,message):
        if on_progress:on_progress(stage,message)
    from sheru_studio import validate,assemble
    validate(ep)
    # Check every image before generating speech. Supplied cached clips use no new quota.
    for index,shot in enumerate(ep['shots']):
        if index not in cached:
            if not shot.get('first_frame'):raise ValueError(f'Shot {index+1}: an opening image is needed for illustrated completion.')
            safe_file(project,shot['first_frame'])
    for index,shot in enumerate(ep['shots']):
        target=ordered/f'{index+1:02}.mp4'
        if index in cached:
            note('Retained clip',f'Shot {index+1}: retaining the completed video, with no new generation.')
            shutil.copy2(cached[index],target)
            output['shots'].append({'index':index,'source':'retained finished footage','clip':str(target)})
            continue
        image=safe_file(project,shot['first_frame'])
        # The new closing portrait is specific to the bundled pilot's cast/blocking.
        closing=Path(__file__).resolve().parent/'assets'/'pilot-frames'/'closing-reaction.png'
        if index==6 and shot.get('frame_origin')=='bundled-pilot-v1' and closing.exists():image=closing
        voice=safe_file(project,shot['voice']) if shot.get('voice') else folder/f'voice-{index+1:02}.mp3'
        if not shot.get('voice'):
            note('Tamil voice',f'Shot {index+1}: recording its exact Tamil dialogue.')
            make_voice(shot,voice)
        length=seconds(voice);available=shot['seconds']-shot['dialogue_start']-.16;adjustment=None
        if length>available:
            factor=length/available
            if factor>1.15:raise ValueError(f'Shot {index+1}: Tamil speech is too long for its slot. No words have been cut.')
            adjusted=folder/f'voice-fit-{index+1:02}.wav'
            run(['ffmpeg','-v','error','-i',str(voice),'-af',f'atempo={factor:.6f}','-y',str(adjusted)])
            voice=adjusted;length=seconds(voice);adjustment={'tempo_factor':round(factor,4),'note':'Small pitch-preserving timing adjustment; full Tamil line retained.'}
        frames=round(shot['seconds']*25);raw=folder/f'illustrated-{index+1:02}.mp4'
        note('Illustrated camera',f'Shot {index+1}: rendering a slow camera move over its scene image. Characters remain still.')
        # One image expands to exactly its slot's frame count. No synthetic limb motion.
        direction=1 if index%2==0 else -1
        zoom=f'1+0.025*on/{max(1,frames-1)}' if direction==1 else f'1.025-0.025*on/{max(1,frames-1)}'
        vf=f"scale=1440:2560:force_original_aspect_ratio=increase,crop=1440:2560,zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s=720x1280:fps=25,setsar=1"
        run(['ffmpeg','-v','error','-i',str(image),'-vf',vf,'-frames:v',str(frames),'-an','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-threads','2','-y',str(raw)])
        # Original procedural accents are mixed only for this illustrated cut.
        shot['sound_cues']=list(shot.get('sound_cues',[]))
        if shot.get('frame_origin')=='bundled-pilot-v1' and index in (1,4,6):
            kind='pen' if index==6 else 'glass';effect=folder/f'{kind}-{index}.wav';cue(kind,effect)
            when=.45 if index==1 else min(shot['seconds']-.3,shot['dialogue_start']+length+.08)
            if effect.is_relative_to(project):
                shot['sound_cues'].append({'file':str(effect.relative_to(project)),'start':when,'gain_db':-5})
        note('Sound & colour',f'Shot {index+1}: mixing Tamil and the illustrated-cut sound accents.')
        dub_and_finish(raw,voice,ep,index,target,project)
        output['shots'].append({'index':index,'source':'illustrated panel with camera motion','clip':str(target),'speech_seconds':length,'voice_adjustment':adjustment})
    assembly=folder/'assembly.mp4';assembly.unlink(missing_ok=True)
    note('Assembly','Joining every shot in order, with no missing slots.')
    assemble(ep,ordered,assembly)
    ambience=folder/'original-ambience.wav';soundtrack(ambience,30)
    ep.setdefault('mix',{})
    if not ep['mix'].get('ambience') and ambience.is_relative_to(project):ep['mix']['ambience']=str(ambience.relative_to(project))
    master=folder/'Sheru-Bhai-E01-Illustrated.mp4'
    finish_master(assembly,master,ep,project)
    metadata=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(master)]).stdout)
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    assert video['width']==720 and video['height']==1280 and abs(float(metadata['format']['duration'])-30)<.15
    if cached:output['mode']='hybrid'
    output['master']=str(master);output['seconds']=30;output['dimensions']=[720,1280]
    (folder/'illustrated-export.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8')
    note('Complete','Complete illustrated/hybrid episode is ready. Review Tamil and sound before publishing.')
    return output
