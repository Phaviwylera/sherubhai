"""Bounded autonomous audio repair and evidence-based visual regeneration."""
import copy,json,re,unicodedata
from pathlib import Path

class QualityRetry(ValueError):
    reason='quality'
    retry_after=300

def tamil_text(value):
    value=unicodedata.normalize('NFC',value)
    # Conservative spelling/number normalization. Do not supply the desired line
    # to the recognizer or treat generated text as evidence of correct audio.
    numbers={'8':'எட்டு','12':'பன்னிரண்டு','24':'இருபத்திநான்கு'}
    value=re.sub(r'(?<!\d)(24|12|8)(?!\d)',lambda m:numbers[m[0]],value)
    value=re.sub(r'[^\u0b80-\u0bff]','',value)
    for left,right in [('பன்னெண்டு','பன்னிரண்டு'),('பனிரண்டு','பன்னிரண்டு'),('இருபத்திநாலு','இருபத்திநான்கு')]:value=value.replace(left,right)
    return value

def missing_numbers(expected,observed):
    target=tamil_text(expected);actual=tamil_text(observed)
    critical=('இருபத்திநான்கு','பன்னிரண்டு','எட்டு')
    return [word for word in critical if word in target and word not in actual]

def audio_failed(report):return report.get('tamil',{}).get('verdict')=='fail'
def other_failed(report):
    return report.get('technical',{}).get('status')=='fail' or report.get('visual',{}).get('verdict')=='fail' or report.get('cut',{}).get('verdict')=='fail'
def refresh_status(report):
    report['status']='fail' if audio_failed(report) or other_failed(report) else 'review'
    return report

def corrections(report):
    text=list(report.get('technical',{}).get('problems',[]))+list(report.get('visual',{}).get('issues',[]))
    if report.get('cut',{}).get('verdict')=='fail':text.append('Cut continuity: '+report['cut'].get('evidence',''))
    if report.get('visual',{}).get('corrections'):text.append(str(report['visual']['corrections']))
    return ' '.join(text)[:2500]

def repair_audio(ep,index,raw,voice,clip,report,folder,project,note,make_voice,already_graded=False):
    """Re-recognize, repair speech/mix, and recheck without requesting another video."""
    from finishing import tamil_review,seconds,dub_and_finish,technical,run
    shot=ep['shots'][index];settings=ep['settings'];repairs=[]
    if not audio_failed(report):return clip,voice,report,repairs
    # A different ASR model can distinguish a short-utterance recognition error.
    stronger='medium' if settings.get('asr_model') not in ('medium','large-v3') else settings['asr_model']
    note('Tamil diagnosis',f'Shot {index+1}: retrying recognition without VAD, then checking with {stronger}. No new video request.')
    diagnosis=tamil_review(clip,shot,settings,model_name=stronger)
    repairs.append({'action':'independent ASR recheck','model':stronger,'result':diagnosis})
    if diagnosis['verdict']=='pass':
        report['tamil']['initial_screen']=copy.deepcopy(report['tamil'])
        report['tamil']=dict(diagnosis,initial_screen=report['tamil']['initial_screen'])
        return clip,voice,refresh_status(report),repairs
    current_voice=voice
    for repair_no in range(1,4):
        # First remix the complete existing line; only then request new delivery.
        if repair_no>1 or current_voice is None:
            current_voice=Path(folder)/f'repair-voice-{index+1:02}-{repair_no}.mp3'
            rate='+0%' if repair_no<3 else '+5%'
            note('Tamil voice repair',f'Shot {index+1}: recording the unchanged Tamil line, repair {repair_no}/3.')
            make_voice(shot,current_voice,rate=rate)
        length=seconds(current_voice);available=shot['seconds']-shot['dialogue_start']-.12
        if length>available:
            factor=length/available
            if factor>1.15:
                repairs.append({'action':'voice timing','repair':repair_no,'seconds':length,'available':available,'result':'too long; try next delivery'})
                continue
            fitted=Path(folder)/f'repair-voice-fit-{index+1:02}-{repair_no}.wav'
            run(['ffmpeg','-v','error','-i',str(current_voice),'-af',f'atempo={factor:.6f}','-y',str(fitted)])
            current_voice=fitted
        mix_ep=copy.deepcopy(ep)
        if already_graded:mix_ep['settings']['grade']='neutral'
        repaired=Path(folder)/f'repaired-{index+1:02}-{shot.get("render_attempt",1)}-{repair_no}.mp4'
        note('Tamil remix',f'Shot {index+1}: replacing audio over the same motion footage and rechecking the result.')
        dub_and_finish(raw,current_voice,mix_ep,index,repaired,project)
        checked=tamil_review(repaired,shot,settings,model_name=stronger)
        source_check=tamil_review(current_voice,shot,settings,model_name=stronger)
        repairs.append({'action':'remix' if repair_no==1 else 'new exact-line delivery','repair':repair_no,'clip':str(repaired),'voice':str(current_voice),'mixed_audio':checked,'clean_voice':source_check})
        clip=repaired;voice=current_voice
        report['technical']=technical(clip,shot['seconds'])
        # Both the clean line and final mixed clip must pass; no threshold lowering.
        if checked['verdict']=='pass' and source_check['verdict']=='pass':
            report['tamil']=dict(checked,clean_voice=source_check)
            return clip,voice,refresh_status(report),repairs
        report['tamil']=dict(checked,verdict='fail',clean_voice=source_check)
    return clip,voice,refresh_status(report),repairs
