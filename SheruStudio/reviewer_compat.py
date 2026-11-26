"""Single-image vision payloads and FFmpeg-decoded ASR input."""
import base64,io,math,subprocess
from pathlib import Path
from PIL import Image,ImageDraw,ImageOps

def image_payload(images):
    if len(images)==1:return base64.b64encode(Path(images[0]).read_bytes()).decode(),''
    columns=min(4,len(images));rows=math.ceil(len(images)/columns)
    tw,th=288,480
    sheet=Image.new('RGB',(columns*tw,rows*th),(24,24,24));draw=ImageDraw.Draw(sheet)
    for index,path in enumerate(images):
        x=(index%columns)*tw;y=(index//columns)*th
        with Image.open(path) as source:
            tile=ImageOps.contain(source.convert('RGB'),(tw-8,th-36))
            sheet.paste(tile,(x+(tw-tile.width)//2,y+32))
        draw.text((x+8,y+8),'IMAGE '+str(index+1)+(' / IDENTITY' if index==0 else ''),fill='white')
    data=io.BytesIO();sheet.save(data,format='JPEG',quality=90)
    note=('The attached single image is a numbered contact sheet. Every reference to Image 1, Image 2, etc. means the corresponding numbered tile, in left-to-right, top-to-bottom order. Image 1 is identity, not a video sample. Tiles are reduced for compatibility; mark hidden or insufficient detail uncertain.\n')
    return base64.b64encode(data.getvalue()).decode(),note

def asr_audio(clip):
    import numpy as np
    result=subprocess.run(['ffmpeg','-v','error','-i',str(clip),'-vn','-ac','1','-ar','16000','-f','f32le','pipe:1'],capture_output=True)
    if result.returncode:raise RuntimeError('ASR audio decoding failed: '+result.stderr.decode(errors='replace')[-1200:])
    audio=np.frombuffer(result.stdout,dtype='<f4').copy()
    if audio.size==0:raise ValueError('ASR audio decoding returned no samples.')
    return audio
