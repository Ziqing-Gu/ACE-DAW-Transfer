"""Read saved Steinberg project data before touching an ACE session."""
import math
from pathlib import Path

from archive_reader import Archive
from convert_project import sha
from native_inventory import read_project_timing


class NativeInput:
    def __init__(self, path, bake_fades=False, static_only=False):
        import soundfile as sf
        from native_events import read_native_events
        from native_mixer import read_native_mixer

        self.path = Path(path).resolve()
        self.source_sha256 = sha(self.path)
        self.timing = read_project_timing(self.path)
        self.bpm = self.timing['bpm']
        self.tracks, self.warnings = read_native_events(self.path)
        self.bus_presets=[]
        mix, warnings, self.errors, self.master = read_native_mixer(self.path, static_only, include_master=True,bus_presets=self.bus_presets)
        self.warnings = list(self.warnings) + list(warnings)
        self.errors = list(self.errors)
        if not 1 <= self.bpm <= 1000:
            self.errors.append('速度超出 ACE 可写范围。')
        if (self.timing['numerator'], self.timing['denominator']) != (4, 4):
            self.errors.append('当前往返仅支持固定 4/4。')
        if any(t['type'] == 'video' for t in self.tracks) and self.timing['frame_type'] != 2:
            self.errors.append('含视频的工程仅支持已校准的 24 fps 时间基。')
        audio = [t for t in self.tracks if t['type'] == 'audio']
        if [t['name'] for t in audio] != [t['name'] for t in mix]:
            raise ValueError('保存工程中的剪辑轨与混音轨顺序不一致，已停止。')
        for track, settings in zip(audio, mix):
            track.update({k: settings[k] for k in ('gain','pan','mute','solo','plugins')})
            if settings.get('fader_value') is not None:
                track['fader_value'] = settings['fader_value']
            elif 'source_fader_value' in settings:
                track.update(source_gain_db=settings['source_gain_db'],source_fader_value=settings['source_fader_value'])
        if not self.tracks:
            self.errors.append('没有可读取的音频、视频或标记轨道。')
        info = {}
        prepared = []
        for index, track in enumerate(self.tracks):
            track['source_track_index'] = index
            for issue in track.get('project_losses',[]):
                (self.warnings if static_only else self.errors).append(issue['detail'])
            title = track['name']
            kind = track['type']
            if kind == 'audio' and not (-70 <= track['gain'] <= 6 and -10 <= track['pan'] <= 10):
                self.errors.append(f'{title} 的推子或声像超出 ACE 范围。')
            for ei, event in enumerate(track['events']):
                event['source_event_index'] = ei
                where = f'{title} / {event["name"]}'
                for issue in event.get('losses',[]):
                    if bake_fades and issue.get('bakeable'):continue
                    (self.warnings if static_only else self.errors).append(where+'：'+issue['detail'])
                point = event.get('marker_kind') == 'point' and kind == 'marker'
                if not all(math.isfinite(event[k]) for k in ('start','duration')) or event['start'] < 0 or (event['duration'] <= 0 and not point):
                    self.errors.append(f'{where} 的片段起止时间无法写入 ACE。')
                if kind == 'marker':
                    continue
                path = Path(event['path'])
                if not path.is_file():
                    self.errors.append(f'{where} 缺少媒体 {path}')
                    continue
                if kind == 'video':
                    continue
                if path not in info:
                    try:
                        info[path] = sf.info(str(path))
                    except (OSError, RuntimeError) as exc:
                        self.errors.append(f'{where} 无法读取音频 {path} ({exc})')
                        info[path] = None
                media = info[path]
                if not math.isfinite(event['offset']) or event['offset'] < 0:
                    self.errors.append(f'{where} 的源偏移无效。')
                if media and event['offset'] + event['duration'] > event.get('source_duration',media.duration) + 1 / media.samplerate:
                    self.errors.append(f'{where} 超出源媒体长度。')
                if not -70 <= event['gain'] <= 30:
                    self.errors.append(f'{where} 的片段增益超出 ACE 范围。')
                for side in ('fade_in','fade_out'):
                    fade = event[side]
                    if not 0 <= fade['length'] <= event['duration'] + 1e-7:
                        self.errors.append(f'{where} 的淡化长度无效。')
                    if fade['length'] and not fade['native'] and not bake_fades:
                        self.errors.append(f'{where} 含非默认淡化。可勾选将该淡化写入独立音频。')
            if kind == 'audio':
                lanes = split_overlaps(track)
                if len(lanes) > 1:
                    message = f'{title} 的重叠片段需要 {len(lanes)} 条 ACE 轨道，重叠区的混音与原工程可能不同。'
                    (self.warnings if static_only else self.errors).append(message)
                prepared.extend(lanes)
            else:
                prepared.append(track)
        self.source_track_count = len(self.tracks)
        self.source_audio_count = len(audio)
        self.source_inserts = sum(len(t['plugins']) for t in audio)
        self.tracks = prepared
        self.warnings = list(dict.fromkeys(self.warnings))
        self.errors = list(dict.fromkeys(self.errors))
        if sha(self.path) != self.source_sha256:
            raise RuntimeError('读取期间源工程已重新保存。请等保存结束后重试。')

    def report(self):
        result = Archive.report(self)
        result.pop('source_xml')
        result['master_inserts']=len(self.master['plugins']) if self.master else 0
        points = sum(e.get('marker_kind') == 'point' for t in self.tracks for e in t['events'])
        result.update(source_project=str(self.path), native_timing=self.timing, point_markers=points,
                      source_track_count=self.source_track_count, source_audio_tracks=self.source_audio_count,
                      source_recoverable_track_inserts=self.source_inserts, steinberg_access='direct_file')
        return result


def split_overlaps(track):
    from ace_short_events import visible_seconds
    lanes = [dict(track, events=[])]
    ends = [-math.inf]
    for event in sorted(track['events'], key=lambda e: e['start']):
        index = next((i for i, end in enumerate(ends) if end <= event['start'] + 1e-9), len(ends))
        if index == len(lanes):
            lanes.append(dict(track, name=f'{track["name"]} (重叠 {index + 1})', events=[]))
            ends.append(-math.inf)
        lanes[index]['events'].append(event)
        ends[index] = event['start'] + visible_seconds(event)
    return lanes
