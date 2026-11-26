"""Versioned shot contracts shared by the writer, renderer and reviewers."""
import json

IDENTITY = (
    "Sheru Bhai matches the supplied portrait: mature male lion head, broad tawny muzzle, "
    "long pale-gold mane, natural lion ears, upright human-proportioned body and hands. "
    "Ivory three-piece suit, cream loafers, gold pendant and pocket square. "
    "Keep his face, mane, body proportions and wardrobe identical across shots."
)
RULES = (
    "Every final frame is vertical 9:16. Plan one continuous, achievable action per shot. "
    "Define speaker, listener or target, screen positions, body orientation and gaze before speech. "
    "Speaking characters face the listener, object or screen specified in the shot. "
    "Maintain the 180-degree axis and screen direction. Allow a motivated turn before a new eyeline. "
    "Show hand contact before moving a prop, maintain the grip until release, and let weight settle. "
    "Objects remain solid with stable shape and scale. Feet contact the ground and bear body weight. "
    "Doors rotate about their hinge after the handle is released. Cars stop before anyone exits. For door actions, specify the exact hand reaching the handle, fingers gripping it, latch release, hinge rotation and a maintained grip. For walking, specify planted supporting foot, weight transfer and the next footfall. Separate entry, exit and dialogue into achievable actions. "
    "Start and end positions must connect to adjacent shots. Edit on a motivated action or reaction. "
    "No phantom objects, sliding feet, extra limbs, hand penetration, teleportation or impossible cuts. "
    "Tamil is natural spoken Chennai Tamil in Tamil script. Keep the intended meaning and forms of address. "
    "Give one speaker each dialogue shot, visible mouth and unhurried articulation. "
    "Time the final speech recording before generating video. No overlapping dialogue. "
    "Describe the sound source, time, duration and level of each effect. Maintain room tone across cuts."
)

def enrich(ep):
    result=json.loads(json.dumps(ep))
    for i,s in enumerate(result['shots']):
        left=s['speaker'] in ('Sheru','Murugan')
        # Pilot positions keep Sheru left at the counter and right in the car exchange.
        if s['listener']=='Murugan': left=False
        if s['speaker']=='Murugan': left=True
        s.setdefault('blocking',f"{s['speaker']} on screen {'left' if left else 'right'}, {s['listener']} on screen {'right' if left else 'left'}. Bodies turned inward, three-quarter profile.")
        s.setdefault('gaze',f"{s['speaker']} looks directly at {s['listener']}'s eyes before speaking and holds that eyeline. Listener looks back.")
        s.setdefault('camera','Eye-level medium two-shot, fixed camera on the established side of the conversation axis. Mouth stays visible.')
        s.setdefault('hands','Keep hands relaxed and visible. Move a prop only with visible contact and a stable grip.')
        s.setdefault('start','Both characters are stationary with feet grounded. Establish eye contact before the first word.')
        s.setdefault('end','Hold the listener reaction briefly. Preserve character positions and prop state for the next cut.')
        s.setdefault('transition','Straight cut on the listener reaction, preserving room tone and screen direction.')
        s.setdefault('sound_cues',[])
        s.setdefault('dialogue_start',0.15)
        s.setdefault('corrections','')
        s.setdefault('first_frame',None)
    result.setdefault('title','Sheru Bhai · Tea kadai')
    result.setdefault('settings',dict(text_model='qwen2.5:3b',vision_model='qwen2.5vl:3b',asr_model='small',attempts=2,grade='warm'))
    from quality import DEFAULT_CONTROLS
    for key,value in DEFAULT_CONTROLS.items():
        result['settings'].setdefault(key,value)
    return result

def check(ep):
    from sheru_studio import validate
    validate(ep)
    for i,s in enumerate(ep['shots'],1):
        for k in ('blocking','gaze','camera','hands','start','end','transition'):
            if not isinstance(s.get(k),str) or not s[k].strip():
                raise ValueError(f'Shot {i}: {k} is required.')
        if not isinstance(s.get('dialogue_start'),(int,float)) or not 0 <= s['dialogue_start'] < s['seconds']:
            raise ValueError(f'Shot {i}: invalid dialogue start.')
        if not isinstance(s.get('sound_cues'),list): raise ValueError('Sound cues must be a list.')
        for cue in s['sound_cues']:
            if not isinstance(cue,dict) or set(cue)!={'file','start','gain_db'}:
                raise ValueError('Sound cues require file, start and gain_db.')
            if not isinstance(cue['file'],str) or not isinstance(cue['start'],(int,float)) or not 0 <= cue['start'] < s['seconds']:
                raise ValueError(f'Shot {i}: sound cue timing is invalid.')
            if not isinstance(cue['gain_db'],(int,float)) or not -60 <= cue['gain_db'] <= 6:
                raise ValueError('Sound gain must be between -60 and +6 dB.')
    settings=ep['settings']
    if settings.get('grade') not in ('warm','neutral'): raise ValueError('Choose warm or neutral grading.')
    if type(settings.get('attempts')) is not int or not 1 <= settings['attempts'] <= 3:
        raise ValueError('Use 1 to 3 render attempts per shot.')
    from quality import controls
    controls(settings)
    return ep

def prompt(ep,index):
    s=ep['shots'][index]
    previous=ep['shots'][index-1]['end'] if index else 'Opening shot.'
    return (
        f"CHARACTER REFERENCE\n{IDENTITY}\n\nDIRECTING RULES\n{RULES}\n\n"
        f"SHOT {index+1:02} · duration {s['seconds']} seconds · final aspect 9:16\n"
        f"Start state: {s['start']}\nIncoming continuity: {previous}\n"
        f"Blocking: {s['blocking']}\nEyeline: {s['gaze']}\n"
        f"Camera: {s['camera']}\nSingle action: {s['action']}\n"
        f"Hand and prop contact: {s['hands']}\nEnd state: {s['end']}\n"
        f"Edit: {s['transition']}\n"
        f"Speaker {s['speaker']}, listener/target {s['listener']}. "
        f"Exact Tamil dialogue starts at {s['dialogue_start']}s: {s['line']}\n"
        f"Sound direction: {s['sfx']}\n"
        "Animate the speaker only during the speech window. Hold the listener's mouth closed. "
        "Maintain natural breathing and subtle listening motion. The reference portrait defines identity. "
        "The approved first-frame image defines the shot composition and setting.\n"
        f"Required corrections from previous review: {s.get('corrections','') or 'None.'}"
    )

def writer_prompt(idea,max_shot_seconds=6):
    return (
        f"You are a Tamil film comedy screenwriter and director. {IDENTITY}\n{RULES}\n"
        "Write a coherent 30-second episode with a setup, reversal and punchline. "
        "Return JSON with title, shots and settings. Each shot requires seconds (number), speaker, listener, "
        "line (Tamil), action, sfx, blocking, gaze, camera, hands, start, end, transition (strings), "
        "sound_cues (empty array), dialogue_start (number), corrections (empty string), first_frame (null). "
        f"Durations sum to exactly 30. Each shot must be 3 to {max_shot_seconds:g} seconds with dialogue that fits. Decompose pick-up, speech, sip and set-down into separate achievable shots when needed. Preserve causal order, prop state and screen direction. Avoid stacking unrelated actions in one shot. Use exact Tamil dialogue, never a romanized approximation. "
        "Settings: text_model=qwen2.5:3b, vision_model=qwen2.5vl:3b, asr_model='small', attempts=2, grade=warm. "
        "Use the supplied reference wardrobe. Do not invent a different costume. "
        f"Episode idea: {idea}"
    )
