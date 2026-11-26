"""Prepare missing local review models before spending cloud video quota."""
import json,os,time,urllib.request

def ensure(model,on_progress=None):
    if not model:return
    host=os.getenv('OLLAMA_HOST','127.0.0.1:11434')
    if not host.startswith(('http://','https://')):host='http://'+host
    host=host.rstrip('/')
    with urllib.request.urlopen(host+'/api/tags',timeout=30) as response:
        names={item['name'] for item in json.load(response).get('models',[])}
    if model in names or model+':latest' in names:return
    if on_progress:on_progress('Preparing missing local model '+model+'. Downloaded weights use the selected installation drive.')
    request=urllib.request.Request(host+'/api/pull',data=json.dumps({'model':model,'stream':True}).encode(),headers={'Content-Type':'application/json'})
    last=0;finished=False
    with urllib.request.urlopen(request,timeout=120) as response:
        for line in response:
            item=json.loads(line)
            if item.get('error'):raise ValueError('Local model download failed: '+item['error'])
            if item.get('status')=='success':finished=True
            if on_progress and (time.monotonic()-last>5 or finished):
                status=item.get('status','Downloading')
                if item.get('total'):status+=' · '+str(round(item.get('completed',0)/2**20))+'/'+str(round(item['total']/2**20))+' MB'
                on_progress(model+' · '+status);last=time.monotonic()
    if not finished:raise ValueError('Local model download ended before its success confirmation.')
