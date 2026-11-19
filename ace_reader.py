"""Build the conversion snapshot directly from a saved ACE project."""
from pathlib import Path

from ace_file import load_project
from ace_file_plugins import catalog, read_insert


def read_saved(source, log=print):
    log('正在离线读取已保存的 ACE 工程…')
    project, digest = load_project(Path(source))
    snapshot = []
    for group in ('tracks','markerTracks','videoTracks'):
        for track in project.get(group, []):
            patterns = track.get('patterns', [])
            clips = [dict(clipUuid=c['uuid'], clipName=c.get('name') or Path(c.get('path','')).stem,
                          clipBeginSec=c['pos'] + c.get('clipPos', 0)) for c in patterns]
            fallback = {'videoTracks':'视频轨', 'markerTracks':'标记轨', 'tracks':'音频轨'}[group]
            if group == 'tracks' and clips and clips[0]['clipName']:
                fallback = clips[0]['clipName']
            snapshot.append(dict(track=dict(trackUuid=track['uuid'], trackName=track.get('name') or fallback), clips=clips))
    entries = None
    for track in [project['master'], *project['tracks']]:
        inserts = track.get('externalFxChain', [])
        if inserts and entries is None:
            entries = catalog()
        track['_plugins'] = [read_insert(fx, entries, i) for i, fx in enumerate(inserts)]
    return project, snapshot, digest
