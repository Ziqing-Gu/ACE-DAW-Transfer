"""Per-user Windows preferences for the transfer window."""
import os
import json
import tempfile
from platform_support import user_data
if os.name=='nt':
    import winreg

KEY = r'Software\ACE-DAW-Transfer\Preferences'
CHECKBOXES = ('bake_fades', 'static_only', 'presets_only')


def load():
    values = dict.fromkeys(CHECKBOXES, False)
    if os.name!='nt':
        try: data=json.loads((user_data()/'preferences.json').read_text(encoding='utf8'))
        except (OSError,ValueError):return values
        if isinstance(data,dict):
            for key,item in data.items():
                if valid(key,item):values[key]=item
        return values
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            for name in (*CHECKBOXES, 'output_format', 'language'):
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if name == 'language':
                    if kind == winreg.REG_SZ and value in ('zh','en'): values[name]=value
                elif name == 'output_format':
                    if kind == winreg.REG_SZ and value in ('acep', 'npr', 'cpr', 'xml'):
                        values[name] = value
                elif kind == winreg.REG_DWORD and value in (0, 1):
                    values[name] = bool(value)
    except OSError:
        pass
    return values


def valid(name,value):
    return ((name in CHECKBOXES and type(value) is bool) or
            (name=='output_format' and value in ('acep','npr','cpr','xml')) or
            (name=='language' and value in ('zh','en')))


def save(name, value):
    if not valid(name,value):return
    if os.name!='nt':
        import fcntl
        folder=user_data()
        folder.mkdir(parents=True,exist_ok=True)
        with (folder/'preferences.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            data=load()
            data[name]=value
            with tempfile.NamedTemporaryFile(dir=folder,delete=False,mode='w',encoding='utf8') as tmp:
                json.dump(data,tmp)
            try:os.replace(tmp.name,folder/'preferences.json')
            finally:
                from pathlib import Path
                Path(tmp.name).unlink(missing_ok=True)
        return
    # Update just the changed setting, so another window cannot overwrite peers.
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        if name in CHECKBOXES:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
