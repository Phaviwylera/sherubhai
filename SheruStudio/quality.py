"""Observable video criteria and renderer controls. These are screening tools."""
CATEGORIES = {
    'physics': 'Grounded weight transfer, natural real-time acceleration and gravity. Mark uncertain when camera scale or frame spacing prevents a physical judgment.',
    'contact': 'Hands and feet contact solid surfaces; gripping precedes moving an object and release follows support. No penetration or sliding grip.',
    'identity': 'Stable lion face, pale-gold mane, ivory suit, pendant, body proportions and object form throughout the clip.',
    'anatomy': 'Human-proportioned body and hands with stable limbs, joints and fingers. Separate the intended lion head from unintended anatomical distortion.',
    'eyelines': 'Both characters face the designated partner or target, and maintain the approved screen direction and body orientation.',
    'continuity': 'Objects persist through occlusion. Start, action and end states follow a causal sequence without jumps, extra cuts or unexplained additions.',
    'artifacts': 'Stable edges, textures and lighting. No unexpected sparks, floating objects, melted surfaces, flicker or duplicated props.',
    'culture': 'Setting, clothing, gestures and props agree with the specific local reference and shot contract. Mark unsupported cultural judgments uncertain.',
    'expression': 'Subtle natural expression and listening movement, with the designated speaker mouth visible and the listener mouth closed.'
}
NEGATIVE = (
    'extra fingers, fused fingers, extra limbs, bent joints, warped lion face, changing mane, '
    'changing suit, duplicated person, floating props, hand passing through objects, foot sliding, '
    'teleportation, object disappearance, object morphing, flickering edges, waxy textures, '
    'unmotivated sparks, unintended camera cut, unexpected zoom, slow motion, exaggerated expression, '
    'listener speaking, camera-facing dialogue, unreadable text, unrequested subtitles'
)
DEFAULT_CONTROLS = dict(seed=20261126,review_samples=16,negative_prompt=NEGATIVE,max_shot_seconds=6)

def controls(settings):
    result=DEFAULT_CONTROLS.copy()
    result.update({k:settings[k] for k in result if k in settings})
    if type(result['seed']) is not int or not 0<=result['seed']<2**31:
        raise ValueError('Seed must be an integer from 0 to 2147483647.')
    if type(result['review_samples']) is not int or not 8<=result['review_samples']<=24:
        raise ValueError('Use 8 to 24 visual review samples.')
    if result['max_shot_seconds']!=6:
        raise ValueError('The current quality profile uses a six-second maximum shot.')
    if not isinstance(result['negative_prompt'],str) or len(result['negative_prompt'])>3000:
        raise ValueError('Negative prompt must contain at most 3000 characters.')
    return result

def preflight(ep):
    """Fail before spending GPU quota when the plan violates the selected profile."""
    controls(ep['settings'])
    for i,shot in enumerate(ep['shots'],1):
        if not 3<=shot['seconds']<=6:
            raise ValueError(f'Shot {i} is {shot["seconds"]} seconds. Split or retime it to 3-6 seconds, preserving the full episode duration.')
    return ep

def visual_result(data):
    checks=data.get('checks') if isinstance(data,dict) else None
    if not isinstance(checks,dict) or set(checks)!=set(CATEGORIES):
        raise ValueError('The reviewer did not assess every visual category.')
    for category,result in checks.items():
        if not isinstance(result,dict) or result.get('verdict') not in ('pass','fail','uncertain'):
            raise ValueError('The reviewer returned an invalid verdict.')
        if not isinstance(result.get('evidence'),str) or not result['evidence'].strip():
            raise ValueError('Each category needs visible evidence or an uncertainty reason.')
    verdicts=[result['verdict'] for result in checks.values()]
    verdict='fail' if 'fail' in verdicts else 'uncertain' if 'uncertain' in verdicts else 'pass'
    return dict(verdict=verdict,checks=checks,
        issues=[category+': '+result['evidence'] for category,result in checks.items() if result['verdict']=='fail'],
        corrections=str(data.get('corrections',''))[:2000])

def unknown_visual(reason):
    return dict(verdict='uncertain',issues=[reason],corrections='',
                checks={name:dict(verdict='uncertain',evidence=reason) for name in CATEGORIES})

def renderer_controls(parameters,settings,index,attempt):
    """Use exposed API controls only. Leave model-specific sampler defaults intact."""
    cfg=controls(settings)
    seed=(cfg['seed']+index*1009+(attempt-1))%(2**31)
    args={}
    supported={}
    for label,names,value in [
        ('seed',('seed','random_seed','seed_ui'),seed),
        ('randomize_seed',('randomize_seed',),False),
        ('negative_prompt',('negative_prompt','n_prompt'),cfg['negative_prompt'])
    ]:
        name=next((name for name in names if name in parameters),None)
        supported[label]='sent' if name else 'not exposed by this endpoint'
        if name:args[name]=value
    return args,dict(requested_seed=seed,controls=supported)

def render_prompt(ep,index):
    shot=ep['shots'][index]
    from direction import IDENTITY
    speech=float(shot.get('speech_seconds',max(.5,shot['seconds']-shot['dialogue_start']-.2)))
    end=min(shot['seconds'],shot['dialogue_start']+speech)
    return (
        f"One continuous vertical 9:16 cinematic shot, {shot['seconds']} seconds, real-time movement. "
        f"{ep.get('setting','A small Chennai neighbourhood tea stall with a steel tea boiler and clear tea tumblers.')} "
        f"{IDENTITY} "
        f"Camera: {shot['camera']} "
        f"Initial state: {shot['start']} "
        f"Screen positions: {shot['blocking']} "
        f"Facing and gaze: {shot['gaze']} "
        f"Only this action unfolds: {shot['action']} "
        f"Visible contact and support: {shot['hands']} "
        f"Final state: {shot['end']} "
        "Preserve every visible object's shape, scale, material and location through occlusion. "
        "Feet bear weight on the ground, objects stay on their supports, motion uses ordinary Earth gravity at natural speed. "
        f"{shot['speaker']} alone makes restrained speaking movements from {shot['dialogue_start']:.2f}s to {end:.2f}s. "
        f"{shot['listener']} listens with a closed mouth. Natural breathing and restrained facial expression. "
        "Follow the approved first-frame composition and reference identity. "
        "Maintain the same camera side and lighting throughout this shot. "
        f"Correction for this attempt: {shot.get('corrections') or 'Keep the specified contact, gaze and identity stable.'}"
    )
