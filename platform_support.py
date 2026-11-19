"""Platform services used by the offline converter."""
import os
import sys
from pathlib import Path


def media_directory(path):
    return str(Path(path)).rstrip('/\\') + os.sep


def stored_directory(value):
    return Path(value.replace('\\', os.sep).replace('/', os.sep))


def user_data():
    if sys.platform == 'darwin':
        return Path.home()/'Library'/'Application Support'/'ACE-DAW-Transfer'
    if os.name == 'nt':
        return Path(os.environ['LOCALAPPDATA'])/'ACE-DAW-Transfer'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'ACE-DAW-Transfer'


def open_directory(folder):
    if os.name == 'nt':
        os.startfile(folder)
    else:
        import subprocess
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])

