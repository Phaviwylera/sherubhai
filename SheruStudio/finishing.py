"""Shot generation, measurable media checks, model-assisted review and finishing."""
import asyncio
import difflib
import json
import math
import os
import re
import subprocess
from pathlib import Path
import urllib.request
import urllib.error
from direction import IDENTITY

def run(args,timeout=1800):
    try:
        result=subprocess.run(args,capture_output=True,text=True,errors='replace',timeout=timeout)
    except subprocess.TimeoutExpired as error:
        import diagnostics
        diagnostics.record('ERROR','Media command timed out.',command=args,timeout_seconds=timeout)
        raise RuntimeError(f'Media operation did not finish within {timeout} seconds.') from error
    if result.returncode:
        import diagnostics
        diagnostics.record('ERROR','Media command failed.',command=args,exit_code=result.returncode,stderr=result.stderr[-24000:])
        raise RuntimeError('Media operation failed: '+result.stderr[-1200:])
    return result

def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]).stdout)

def seconds(path):
    return float(probe(path)['format']['duration'])

def model_json(model,instruction,images=None):
    host=os.getenv('OLLAMA_HOST','127.0.0.1:11434')
    if not host.startswith(('http://','https://')): host='http://'+host
    body=dict(model=model,prompt=instruction,stream=False,format='json',keep_alive=0,options={'temperature':0.15})
    if images:
        from reviewer_compat import image_payload
        payload,note=image_payload(images)
        body['images']=[payload]
        body['prompt']=note+instruction
    request=urllib.request.Request(host.rstrip('/')+'/api/generate',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=1200) as response:
            return json.loads(json.load(response)['response'])
    except urllib.error.HTTPError as error:
        import diagnostics
        detail=diagnostics.redact(error.read(16000).decode('utf8',errors='replace'))
        raise ValueError(f'Local model {model} returned HTTP {error.code}: '+detail) from error

def safe_file(folder,name):
    candidate=(folder/name).resolve()
    if not candidate.is_relative_to(folder.resolve()) or not candidate.is_file():
        raise ValueError('A project media file is missing or outside the project.')
    return candidate

def extract_frames(clip,folder,count=16):
    folder.mkdir(exist_ok=True,parents=True)
    length=seconds(clip)
    frames=[]
    for i in range(count):
        target=folder/f'{i:02}.jpg'
        run(['ffmpeg','-v','error','-ss',str(min(length-.05,length*i/(count-1))),'-i',str(clip),'-frames:v','1','-vf','scale=360:-2','-y',str(target)])
        frames.append(target)
    return frames

def technical(clip,expected):
    metadata=probe(clip)
    video=next((s for s in metadata['streams'] if s['codec_type']=='video'),None)
    audio=next((s for s in metadata['streams'] if s['codec_type']=='audio'),None)
    problems=[]
    if not video: problems.append('Video track missing.')
    if not audio: problems.append('Audio track missing.')
    if video and abs(video['width']/video['height']-9/16)>.003:
        problems.append('Frame is not 9:16.')
    if abs(float(metadata['format']['duration'])-expected)>.15:
        problems.append('Clip duration differs from the approved slot.')
    scan=run(['ffmpeg','-hide_banner','-i',str(clip),'-vf','blackdetect=d=0.25:pic_th=0.98,freezedetect=n=-45dB:d=0.8','-af','astats=metadata=1:reset=0','-f','null','-'])
    # A quiet locked-off shot can trigger freeze detection, so report it for judgment.
    warnings=re.findall(r'(?:black_start:[^\n]+|freeze_start:[^\n]+)',scan.stderr)
    if re.search(r'Peak level dB:\s+0\.0',scan.stderr): warnings.append('Audio reaches full scale; inspect clipping.')
    if 'Peak level dB: -inf' in scan.stderr: problems.append('Audio is silent.')
    return dict(status='fail' if problems else 'pass',problems=problems,warnings=warnings)

def tamil_review(clip,shot,settings,model_name=None):
    from auto_repair import tamil_text,missing_numbers
    name=model_name or settings.get('asr_model')
    if not name:return {'verdict':'uncertain','note':'Tamil ASR is disabled.'}
    try:
        from faster_whisper import WhisperModel
        from reviewer_compat import asr_audio
        model=WhisperModel(name,device='cpu',compute_type='int8',download_root=str(Path(os.getenv('SHERU_ROOT',str(Path(__file__).resolve().parent)))/'cache'/'whisper'))
        # These shots are only 3-6 seconds; VAD can discard quiet opening words.
        segments,_=model.transcribe(asr_audio(clip),language='ta',vad_filter=False,beam_size=5,temperature=0,condition_on_previous_text=False)
        transcript=' '.join(segment.text for segment in segments)
        del model
        score=difflib.SequenceMatcher(None,tamil_text(shot['line']),tamil_text(transcript)).ratio()
        missing=missing_numbers(shot['line'],transcript)
        return {'transcript':transcript,'expected':shot['line'],'similarity':round(score,3),'missing_spoken_numbers':missing,'verdict':'pass' if score>=.85 and not missing else 'fail','model':name,'vad_filter':False,'note':'ASR screening with conservative colloquial spelling/number normalization. This does not establish native delivery or lip synchronization.'}
    except Exception as error:
        import diagnostics
        detail=diagnostics.capture(error,reviewer='Tamil ASR')
        return {'verdict':'uncertain','model':name,'note':'Tamil recognition unavailable: '+detail['message'],'error_id':detail['id']}

def review(clip,shot,settings,reference,folder):
    report={'technical':technical(clip,shot['seconds'])}
    from quality import controls,CATEGORIES,visual_result,unknown_visual
    samples=controls(settings)['review_samples']
    images=extract_frames(clip,folder/'frames',samples)
    vision=settings.get('vision_model')
    if vision:
        try:
            timeline=[round(min(shot['seconds']-.05,shot['seconds']*i/(samples-1)),3) for i in range(samples)]
            result=model_json(vision,
                "You are reviewing sampled frames, not a physics simulation. The FIRST image is the identity reference. "
                +IDENTITY+"\nThe remaining images are ordered video frames at seconds: "+str(timeline)+
                "\nShot contract: "+json.dumps(shot,ensure_ascii=False)+
                "\nAssess ALL categories: "+json.dumps(CATEGORIES)+
                "\nReturn JSON with checks (object keyed by every category above). Each value has verdict "
                "(pass, fail or uncertain) and evidence (visible observation with a frame time, or a specific uncertainty reason). "
                "Also return corrections (short concrete instructions for the next render). "
                "Do not approve a category from a generic impression. Mark hidden anatomy or unobservable contact uncertain. "
                "Do not claim to measure gravitational acceleration or to verify speech/audio from still frames. "
                "Any clear failing category should identify its specific defect. Use the shot contract to distinguish intended movements from glitches.",
                [reference]+images)
            report['visual']=visual_result(result)
            report['visual']['sample_times']=timeline
            report['visual']['input_format']='Single numbered contact sheet; details may be reduced. Not full-motion analysis.'
        except Exception as error:
            import diagnostics
            diagnostics.capture(error,reviewer='visual')
            report['visual']=unknown_visual('Visual reviewer unavailable or incomplete: '+type(error).__name__)
    else:
        report['visual']=unknown_visual('Visual reviewer is disabled.')
    report['lip_sync']={'verdict':'uncertain','note':'Frame sampling and Tamil transcription do not establish phoneme-to-mouth synchronization. Review full-speed audio/video.'}
    report['tamil']=tamil_review(clip,shot,settings)
    report['status']='fail' if report['technical']['status']=='fail' or any(report[k]['verdict']=='fail' for k in ('visual','tamil')) else 'review'
    report['human_checks']=['Native Tamil pronunciation and delivery','Speech-to-mouth timing','Full-speed motion and hand contact','Cut continuity','SFX timing and level']
    return report

def generate(ep,index,folder,project,connection=None,on_progress=None):
    from provider_pool import Pool,LocalPool
    if isinstance(connection,(Pool,LocalPool)):return connection.generate(ep,index,folder,project,on_progress)
    return _generate_single(ep,index,folder,project,connection,on_progress)

def _generate_single(ep,index,folder,project,connection=None,on_progress=None):
    from gradio_client import handle_file
    from production import find_video
    from provider import open_provider,preflight_episode,submit,DEFAULT_SPACE
    from quality import renderer_controls,render_prompt,preflight
    preflight(ep)
    connection=connection or open_provider(folder)
    preflight_episode(ep,project,connection,[index])
    client,name,params,report=connection
    args,audit=renderer_controls(params,ep['settings'],index,ep['shots'][index].get('render_attempt',1))
    args['prompt']=render_prompt(ep,index)
    image_name=report['capabilities']['image_parameter']
    if image_name:
        args[image_name]=handle_file(str(safe_file(project,ep['shots'][index]['first_frame'])))
    if 'reference_image' in params:
        args['reference_image']=handle_file(str(project/'reference.png'))
    for key in params:
        if key in ('width','width_ui'):args[key]=576
        elif key in ('height','height_ui'):args[key]=1024
        elif key in ('duration','duration_seconds','duration_s','duration_ui'):args[key]=ep['shots'][index]['seconds']
    # LTX models quantize temporal length to 8k+1 frames. Request the next
    # valid grid length and trim in finishing; never slow or stretch motion.
    if report['space'] in ('Lightricks/LTX-2.5','Lightricks/LTX-Video-distilled'):
        fps=24 if report['space']=='Lightricks/LTX-2.5' else 30
        frames=math.ceil((ep['shots'][index]['seconds']*fps-1)/8)*8+1
        duration_key=report['capabilities']['duration_parameter']
        args[duration_key]=(frames-1)/fps if fps==24 else frames/fps
        if 'auto_len' in params:args['auto_len']=False
        if 'do_enhance' in params:args['do_enhance']=False
        audit['duration_grid']={'fps':fps,'target_frames':frames,'note':'Next supported frame count, then trim excess at real-time speed.'}
        audit['prompt_enhancement']='disabled to preserve the compiled shot contract' if 'do_enhance' in params else 'not exposed'
    audit.update(space=report['space'],endpoint=name,positive_prompt=args['prompt'],
        negative_prompt=args.get('negative_prompt',args.get('n_prompt')),
        requested_duration=ep['shots'][index]['seconds'],requested_dimensions=[576,1024],
        sent_dimensions={key:args[key] for key in ('width','height','width_ui','height_ui') if key in args},
        sent_duration={key:args[key] for key in ('duration','duration_seconds','duration_s','duration_ui') if key in args},
        image_conditioning='shot first frame' if image_name else 'reference image',
        separate_reference_sent=('reference_image' in args))
    audit_file=folder/f'provider-{index+1:02}-{ep["shots"][index].get("render_attempt",1)}.json'
    audit_file.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    result=submit(client,name,args,on_progress)
    video=find_video(result)
    if not video:raise ValueError('Provider returned no usable video.')
    raw=Path(video)
    # This verified provider returns 480x832 portrait. Normalize its small known
    # aspect difference explicitly, retaining 1x motion and recording the crop.
    if report['space'] in (DEFAULT_SPACE,'Lightricks/LTX-2.5','Lightricks/LTX-Video-distilled'):
        stream=next(s for s in probe(raw)['streams'] if s['codec_type']=='video')
        ratio=stream['width']/stream['height']
        loss=abs(ratio-9/16)/max(ratio,9/16)
        if abs(ratio-9/16)>.003 and loss<=.03:
            width=stream['width'];height=stream['height']
            if ratio>9/16:width=int(height*9/16)//2*2
            else:height=int(width/(9/16))//2*2
            normalized=folder/f'provider-vertical-{index+1:02}-{ep["shots"][index].get("render_attempt",1)}.mp4'
            run(['ffmpeg','-v','error','-i',str(raw),'-vf',f'crop={width}:{height}', '-an','-c:v','libx264','-pix_fmt','yuv420p','-y',str(normalized)])
            audit['composition_adjustment']=dict(source=[stream['width'],stream['height']],crop=[width,height],note='Small centre crop from the provider portrait format to 9:16; motion speed unchanged.')
            audit_file.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
            raw=normalized
    return raw

def dub_and_finish(raw,voice,ep,index,target,project):
    shot=ep['shots'][index]
    length=shot['seconds']
    video=next(s for s in probe(raw)['streams'] if s['codec_type']=='video')
    if abs(video['width']/video['height']-9/16)>.01:
        raise ValueError('Renderer returned a nonvertical composition. Supply a 9:16 first frame and a vertical-capable endpoint.')
    if seconds(raw)+.045<length: raise ValueError('Generated video is shorter than its slot. Revise the shot or use a longer-duration renderer.')
    if voice and seconds(voice)+shot['dialogue_start']>length-.1: raise ValueError('Tamil delivery is longer than the shot. Revise dialogue or timing.')
    grade='eq=contrast=1.025:saturation=1.02:brightness=0.005,colorbalance=rs=.018:bs=-.012' if ep['settings']['grade']=='warm' else 'null'
    args=['ffmpeg','-v','error','-i',str(raw)]
    if voice: args+=['-i',str(voice)]
    else:
        if not any(s['codec_type']=='audio' for s in probe(raw)['streams']): raise ValueError('Imported clip requires its final dialogue audio.')
    filters=[f'[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=25,{grade}[v]']
    source='1:a' if voice else '0:a'
    delay=round(shot['dialogue_start']*1000) if voice else 0
    filters.append(f'[{source}]adelay={delay}:all=1,apad,atrim=0:{length},loudnorm=I=-16:TP=-2:LRA=7[dialogue]')
    labels=['[dialogue]']
    offset=2 if voice else 1
    for i,cue in enumerate(shot['sound_cues']):
        audio=safe_file(project,cue['file'])
        args+=['-i',str(audio)]
        filters.append(f'[{i+offset}:a]volume={cue["gain_db"]}dB,adelay={round(cue["start"]*1000)}:all=1,apad,atrim=0:{length}[s{i}]')
        labels.append(f'[s{i}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.89:level=0[a]')
    run(args+['-filter_complex',';'.join(filters),'-map','[v]','-map','[a]','-t',str(length),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-y',str(target)])

def make_voice(shot,path,rate=None):
    import edge_tts
    asyncio.run(edge_tts.Communicate(shot['line'],'ta-IN-ValluvarNeural',rate=rate or ('-5%' if shot['speaker']=='Sheru' else '+0%'),pitch='-12Hz' if shot['speaker']=='Sheru' else '+9Hz' if shot['speaker']=='tea master' else '+0Hz').save(str(path)))

def finish_master(assembly,target,ep,project):
    length=sum(s['seconds'] for s in ep['shots'])
    args=['ffmpeg','-v','error','-i',str(assembly)]
    filters=[]
    mix=ep.get('mix',{})
    for kind in ('ambience','music'):
        if mix.get(kind):
            args+=['-stream_loop','-1','-i',str(safe_file(project,mix[kind]))]
    labels=['[base]']
    filters.append('[0:a]asplit=2[base][duck]')
    index=1
    for kind,level in [('ambience',-28),('music',-24)]:
        if mix.get(kind):
            filters.append(f'[{index}:a]atrim=0:{length},volume={level}dB[{kind}bed]')
            if kind=='music':
                filters.append('[musicbed][duck]sidechaincompress=threshold=0.025:ratio=6:attack=15:release=300[music]')
                labels.append('[music]')
            else: labels.append('[ambiencebed]')
            index+=1
    if not mix.get('music'): filters.append('[duck]anullsink')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=7[a]')
    run(args+['-filter_complex',';'.join(filters),'-map','0:v:0','-map','[a]','-t',str(length),'-c:v','copy','-c:a','aac','-movflags','+faststart','-y',str(target)])

def review_cut(previous,current,previous_shot,current_shot,settings,reference,folder):
    if not settings.get('vision_model'):
        return {'verdict':'uncertain','evidence':'Visual reviewer is disabled; cross-shot continuity requires playback.'}
    try:
        left=extract_frames(previous,folder/'previous',4)
        right=extract_frames(current,folder/'current',4)
        result=model_json(settings['vision_model'],
            "Check continuity across a planned cinematic cut. Image 1 is Sheru's identity reference. "
            "Images 2 and 3 show the previous clip's end; images 4 and 5 show the next clip's start. "
            "Previous shot: "+json.dumps(previous_shot,ensure_ascii=False)+
            "\nNext shot: "+json.dumps(current_shot,ensure_ascii=False)+
            "\nReturn JSON with verdict (pass, fail, uncertain) and evidence (specific observed mismatch or uncertainty reason). "
            "Compare visible prop state, clothing and identity. Respect a declared change of camera axis or motivated ellipsis. "
            "Do not treat an intentional reverse angle as a teleport. Mark occluded or unobservable continuity uncertain.",
            [reference,left[-2],left[-1],right[0],right[1]])
        if result.get('verdict') not in ('pass','fail','uncertain') or not isinstance(result.get('evidence'),str) or not result['evidence'].strip():
            raise ValueError('Incomplete cut review')
        return result
    except Exception as error:
        import diagnostics
        diagnostics.capture(error,reviewer='cut continuity')
        return {'verdict':'uncertain','evidence':'Cut reviewer unavailable or incomplete: '+type(error).__name__}

