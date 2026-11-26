"""Sheru Studio account connections. Secrets live in the Windows user's credential vault."""
import json
import re
import secrets
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit
from pathlib import Path

SERVICE = 'SheruStudio'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly']
IG_SCOPES = {'instagram_business_basic', 'instagram_business_content_publish'}

def stored(name):
    import keyring
    value = keyring.get_password(SERVICE, name)
    return json.loads(value) if value else None

def save(name, data):
    import keyring
    keyring.set_password(SERVICE, name, json.dumps(data))

def check_redirect(registered, returned, state):
    original, actual = urlsplit(registered), urlsplit(returned.strip())
    if (actual.scheme, actual.netloc, actual.path) != (original.scheme, original.netloc, original.path):
        raise ValueError('The callback address differs from the registered redirect URI.')
    args = parse_qs(actual.query)
    if args.get('state') != [state] or 'error' in args or len(args.get('code', [])) != 1:
        raise ValueError('Login was cancelled or its verification code did not match.')
    return args['code'][0]

def youtube(root):
    from tkinter import filedialog
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = stored('google-client')
    if not config:
        filename = filedialog.askopenfilename(parent=root,title='Choose Google Desktop OAuth client JSON',filetypes=[('Desktop client JSON','*.json')])
        if not filename: return
        config = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
        if 'installed' not in config: raise ValueError('Choose a Desktop app OAuth client.')
    flow = InstalledAppFlow.from_client_config(config, SCOPES,autogenerate_code_verifier=True)
    cred = flow.run_local_server(host='127.0.0.1',port=0,timeout_seconds=300,prompt='consent',access_type='offline',authorization_prompt_message=None)
    if not cred.refresh_token: raise ValueError('Google did not grant offline access. Reconnect and approve access.')
    save('google-client',config)
    save('youtube',json.loads(cred.to_json()))

def instagram(root):
    import requests
    from tkinter import simpledialog
    def ask(label,secret=False):
        answer=simpledialog.askstring('Connect Instagram',label,parent=root,show='*' if secret else '')
        return answer.strip() if answer else ''
    config=stored('ig-app')
    if not config:
        app=ask('Instagram App ID from Meta > Instagram > API setup')
        if not app: return
        secret=ask('Instagram App Secret',True)
        if not secret: return
        redirect=ask('Exact registered HTTPS redirect URI on a site you control (no query string)')
        if not redirect: return
        parsed=urlsplit(redirect)
        if parsed.scheme!='https' or not parsed.hostname or parsed.query or parsed.fragment: raise ValueError('Enter a registered HTTPS redirect URI.')
        version=ask('Graph API version enabled in your Meta app (for example v24.0)')
        if not version: return
        if not re.fullmatch(r'v\d+\.\d+',version): raise ValueError('Enter the version in vNN.N format.')
        config=dict(app=app,secret=secret,redirect=redirect,version=version)
    state=secrets.token_urlsafe(32)
    params=dict(client_id=config['app'],redirect_uri=config['redirect'],response_type='code',scope=','.join(sorted(IG_SCOPES)),state=state,force_authentication='1')
    webbrowser.open('https://www.instagram.com/oauth/authorize?'+urlencode(params))
    returned=ask('After approving Instagram access, paste the complete redirected browser URL here.',True)
    if not returned: return
    code=check_redirect(config['redirect'],returned,state)
    res=requests.post('https://api.instagram.com/oauth/access_token',data=dict(client_id=config['app'],client_secret=config['secret'],grant_type='authorization_code',redirect_uri=config['redirect'],code=code),timeout=30)
    res.raise_for_status()
    short=res.json()
    perms=short.get('permissions')
    if perms and not IG_SCOPES.issubset(set(perms if isinstance(perms,list) else re.split('[, ]+',perms))):
        raise ValueError('Instagram publishing permission was not granted.')
    res=requests.get('https://graph.instagram.com/access_token',params=dict(grant_type='ig_exchange_token',client_secret=config['secret'],access_token=short['access_token']),timeout=30)
    res.raise_for_status()
    token=res.json()
    res=requests.get('https://graph.instagram.com/'+config['version']+'/me',params={'fields':'user_id,username'},headers={'Authorization':'Bearer '+token['access_token']},timeout=30)
    res.raise_for_status()
    profile=res.json()
    save('ig-app',config)
    save('instagram',dict(token=token['access_token'],user=str(profile['user_id']),username=profile['username'],version=config['version'],expires=time.time()+token['expires_in']))

def main(platform=None):
    import tkinter as tk
    from tkinter import ttk,messagebox
    root=tk.Tk()
    root.title('Sheru Studio | Accounts')
    frame=ttk.Frame(root,padding=24)
    frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Sheru Studio',font=('Segoe UI',20)).pack(anchor='w')
    ttk.Label(frame,text='Publishing accounts',padding=(0,8)).pack(anchor='w')
    status=tk.StringVar()
    def refresh():
        ig=stored('instagram')
        ig_status=('Reconnect required' if ig['expires']<=time.time() else '@'+ig['username']) if ig else 'Awaiting connection'
        status.set('YouTube: '+('Authorization saved' if stored('youtube') else 'Awaiting connection')+'\nInstagram: '+ig_status)
    def connect(fn):
        try: fn(root); refresh()
        except ValueError as e:
            import diagnostics
            detail=diagnostics.capture(e,stage='Account connection')
            messagebox.showerror('Connection',str(e) if type(e) is ValueError else 'Invalid provider response.')
        except Exception as e:
            import diagnostics
            detail=diagnostics.capture(e,stage='Account connection')
            messagebox.showerror('Connection','Connection did not complete. Error '+detail['id']+'. Download the application log in Studio for the failed step and technical details.')
    ttk.Button(frame,text='Connect YouTube',command=lambda:connect(youtube)).pack(fill='x',pady=5)
    ttk.Button(frame,text='Connect Instagram',command=lambda:connect(instagram)).pack(fill='x',pady=5)
    ttk.Label(frame,textvariable=status).pack(anchor='w',pady=8)
    ttk.Button(frame,text='Google app credentials',command=lambda:webbrowser.open('https://console.cloud.google.com/apis/credentials')).pack(fill='x')
    ttk.Button(frame,text='Meta app settings',command=lambda:webbrowser.open('https://developers.facebook.com/apps/')).pack(fill='x')
    refresh()
    if platform:
        root.after(200,lambda:connect(youtube if platform=='youtube' else instagram))
    root.mainloop()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--platform',choices=['youtube','instagram'])
    main(parser.parse_args().platform)
