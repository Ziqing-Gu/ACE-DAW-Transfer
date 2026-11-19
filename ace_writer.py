"""Write the editable ACE timeline and project media without starting a DAW."""
import datetime
import json
import math
import os
import uuid
from pathlib import Path

from ace_file import save_project
from ace_file_plugins import catalog, make_insert, read_insert
from ace_short_events import marker_ticks, pad_short_audio, visible_seconds
from archive_reader import Archive
from convert_project import VERSION, sha
from plugin_state import from_envelope, state_summary

def bake_event(source,dest,event):
    import numpy as np
    import soundfile as sf
    with sf.SoundFile(source) as inp:
        if inp.frames*inp.channels*4+128>=2**32:raise ValueError('淡化写入后的 WAV 超过 4 GB。')
        with sf.SoundFile(dest,'w',samplerate=inp.samplerate,channels=inp.channels,subtype='FLOAT') as out:
            offset=event['offset'];duration=event['duration']
            while inp.tell()<len(inp):
                start=inp.tell();audio=inp.read(262144,dtype='float32',always_2d=True)
                local=(np.arange(start,start+len(audio),dtype=np.float64)/inp.samplerate)-offset
                gain=np.ones(len(audio),dtype=np.float64)
                for side in ('fade_in','fade_out'):
                    fade=event[side];length=fade['length']
                    if fade['native'] or length<=0:continue
                    relative=local if side=='fade_in' else local-(duration-length)
                    mask=(relative>=0)&(relative<=length)
                    gain[mask]*=np.interp(relative[mask]/length,[p[0] for p in fade['points']],[p[1] for p in fade['points']])
                out.write(audio*gain[:,None])

def save_plugin_presets(targets, folder):
    folder.mkdir()
    entries=[]
    seen=set()
    for track,track_id,index in targets:
        for order,plugin in enumerate(track['plugins']):
            data=from_envelope(plugin['preset'])
            digest=state_summary(data)["preset_sha256"]
            name=digest+'.vstpreset'
            if digest not in seen:
                (folder/name).write_bytes(data)
                seen.add(digest)
            entries.append(dict(track=track['name'],track_uuid=track_id,insert_order=order+1,
                                source_slot=int(plugin.get('slot',order))+1,
                                plugin=plugin['name'],cid=plugin['cid'],enabled=plugin['enabled'],
                                bypassed=plugin['bypassed'],preset=name))
    (folder/'插入顺序.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf8')
    return len(entries)


def new_id():
    return '{'+str(uuid.uuid4())+'}'


def new_track(kind, name):
    return dict(uuid=new_id(), type=kind, name=name, gain=0., pan=0., mute=False, solo=False,
                record=False, listen=False, color='#438bc9', extraInfo={}, patterns=[])


def timeline(archive):
    project = json.loads((Path(__file__).parent/'templates'/'ace-empty.json').read_text(encoding='utf8'))
    project.update(tracks=[], markerTracks=[], videoTracks=[], chordTrack=new_track('chord',''))
    project['tempos'][0]['bpm'] = archive.bpm
    if any(t['type']=='video' for t in archive.tracks):
        project['canvas']['frameRate'] = 24.
    return project


def create_clip(event, kind, media, ace_dir, bpm, label):
    import soundfile as sf
    shape = dict(offsetX=0., offsetY=0.)
    clip = dict(uuid=new_id(), name=event['name'], enabled=event['enabled'], color='',
                fadeIn=dict(length=0.,shape=shape), fadeOut=dict(length=0.,shape=shape,crossfade=False), extraInfo={})
    if kind == 'marker':
        duration = marker_ticks(event, bpm)
        clip.update(timeUnit='tick', pos=round(event['start']*bpm*8), clipPos=0., clipDur=duration, dur=duration,
                    color=event.get('color') or '#ffc394')
        return clip
    source = media[event['path']]
    duration = event['duration']
    if kind == 'audio':
        baked=None
        for key, field in [('fade_in','fadeIn'),('fade_out','fadeOut')]:
            if event[key]['native']:
                clip[field]['length'] = event[key]['length']
        if any(not event[k]['native'] and event[k]['length'] for k in ('fade_in','fade_out')):
            baked = ace_dir/'Media'/(label+'_fade.wav')
            baked.parent.mkdir(exist_ok=True)
            bake_event(source, baked, event)
            source = baked
        duration = visible_seconds(event)
        if duration != event['duration']:
            padded = ace_dir/'Media'/(label+'_short.wav')
            padded.parent.mkdir(exist_ok=True)
            pad_short_audio(source, padded, event)
            if baked:baked.unlink()
            clip['fadeOut']['length'] = 0.
            source = padded
        full = sf.info(source).duration
        clip.update(gain=event['gain'], isStreaming=False)
    else:
        full = max(event.get('source_duration', 0.), event['offset'] + duration)
        clip.update(gain=0., detached=True)
    clip.update(timeUnit='sec', pos=event['start']-event['offset'], dur=full, clipPos=event['offset'],
                clipDur=duration, path='./'+source.relative_to(ace_dir).as_posix()
                if source.is_relative_to(ace_dir) else source.as_posix())
    return clip


def convert_to_ace(source, parent, log=print, bake_fades=False, static_only=False, native_timing=None,
                   *, archive=None, load_plugins=True):
    source = Path(source).resolve()
    if archive is None:
        archive = Archive(source,bake_fades,static_only,native_timing=native_timing)
    if archive.errors:
        raise ValueError('导入前检查未通过：\n'+'\n'.join(archive.errors))
    report = archive.report()
    for warning in archive.warnings:
        log('提示：'+warning)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out = Path(parent).resolve()/(source.stem+'_to_ACE_'+stamp)
    out.mkdir(parents=True)
    ace_dir = out/'ACE项目'
    media_dir = ace_dir/'Media'
    ace_dir.mkdir(parents=True)
    path = ace_dir/'ACE项目.acep'
    native = source.suffix.lower() in ('.cpr','.npr')
    report.update(tool_version=VERSION, status='in_progress',output_directory=str(out), ace_access='direct_file',
                  skipped_inserts=[],plugin_results=[],short_audio_adjustments=[],baked_fade_events=0,
                  audio_video_max_error_seconds=0.,marker_max_rounding_seconds=0.)
    def checkpoint():
        (out/'转换结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    checkpoint()
    try:
        if native:
            from native_events import materialize_native_media
            materialize_native_media(archive.tracks,media_dir,bake_event_processing=bake_fades)
        files = sorted({e['path'] for t in archive.tracks for e in t['events'] if 'path' in e})
        media = {}
        source_media = {}
        for i, name in enumerate(files, 1):
            original = Path(name).resolve(strict=True)
            log(f'引用素材 {i}/{len(files)}：{original.name}')
            digest=sha(original)
            media[name] = original
            source_media[name]=dict(path=original.relative_to(ace_dir).as_posix() if original.is_relative_to(ace_dir)
                                    else original.as_posix(),sha256=digest)
        project = timeline(archive)
        track_map, mappings, targets = [], [], []
        groups = dict(audio='tracks',marker='markerTracks',video='videoTracks')
        ticks = archive.bpm*8
        max_end_ticks = 0.
        for i, record in enumerate(archive.tracks):
            kind = record['type']
            lanes = []
            for j, event in enumerate(record['events']):
                clip = create_clip(event,kind,media,ace_dir,archive.bpm,f'{i+1:03d}_{j+1:03d}')
                if kind=='marker' and not event.get('color'):
                    from project_colors import FACTORY
                    clip['color']=record.get('color') or FACTORY[(event.get('marker_id',j+1)-1)%len(FACTORY)]
                start = clip['pos']+clip['clipPos']
                end = start + clip['clipDur']
                max_end_ticks = max(max_end_ticks,end if kind=='marker' else end*ticks)
                # Independent lanes avoid trimming overlapping padded markers.
                lane = next((track for track,spans in lanes if kind!='marker' or
                             all(end<=a or start>=b for a,b in spans)),None)
                if lane is None:
                    lane = new_track(kind,record['name'])
                    lanes.append((lane,[]))
                next(spans for track,spans in lanes if track is lane).append((start,end))
                lane['patterns'].append(clip)
                link = dict(ace_track_uuid=lane['uuid'],ace_clip_uuid=clip['uuid'],track=record['name'],
                            source_event=event,source_track_index=record.get('source_track_index',i),
                            source_event_index=event.get('source_event_index',j))
                if kind=='marker':
                    link['ace_marker_ticks'] = clip['clipDur']
                    link['ace_color']=clip['color']
                    report['marker_max_rounding_seconds']=max(report['marker_max_rounding_seconds'],
                        abs(start/ticks-event['start']),abs(clip['clipDur']/ticks-event['duration']))
                else:
                    report['audio_video_max_error_seconds']=max(report['audio_video_max_error_seconds'],abs(start-event['start']))
                    if kind=='audio':
                        if clip['clipDur']!=event['duration']:
                            report['short_audio_adjustments'].append(dict(track=record['name'],event=event['name'],
                                source_seconds=event['duration'],ace_seconds=clip['clipDur']))
                        if any(not event[k]['native'] and event[k]['length'] for k in ('fade_in','fade_out')):
                            report['baked_fade_events']+=1
                mappings.append(link)
            for track,_ in lanes or [(new_track(kind,record['name']),[])]:
                if record.get('color'):track['color']=record['color']
                if kind=='marker':
                    track['protected']=False
                if kind=='audio':
                    track['inputChannelIndex']=-1
                    for key in ('gain','pan','mute','solo'):
                        if key in record:track[key]=record[key]
                project[groups[kind]].append(track)
                row = dict(ace_track_uuid=track['uuid'],source_track_index=record.get('source_track_index',i),
                           name=record['name'],type=kind,ace_color=track['color'],color=record.get('color'))
                if 'fader_value' in record:
                    row.update(gain=record['gain'],fader_value=record['fader_value'])
                elif 'source_fader_value' in record:
                    row.update(gain=record['gain'],fader_value=record['source_fader_value'],source_gain_db=record['source_gain_db'])
                track_map.append(row)
                if record.get('plugins'):
                    targets.append((record,track,i))
        project['duration']=max(project['duration'],math.ceil(max_end_ticks/1920+1)*1920)
        master=getattr(archive,'master',None)
        if master:
            project['master']['gain']=master['gain']
            if master.get('plugins'):
                targets.append((master,project['master'],len(archive.tracks)))
        report['native_master_gain_db']=master['gain'] if master else None
        buses=getattr(archive,'bus_presets',[])
        if buses:
            bus_targets=[(bus,None,i) for i,bus in enumerate(buses)]
            report['saved_bus_presets']=save_plugin_presets(bus_targets,out/'总线插件预置')
            report['bus_inserts']=[dict(bus=b['name'],plugins=[p['name'] for p in b['plugins']]) for b in buses]
            report['warnings'].append('总线插件的原始 VST3 状态单独保存在“总线插件预置”，其发送与返回路由须在 DAW 中使用。')
            log(f'已单独保存 {report["saved_bus_presets"]} 个总线插入预置。')
        if targets:
            presets = []
            if load_plugins:
                entries = catalog()
                number=256
                for record,track,i in targets:
                    states=[]
                    skipped=[]
                    for index,plugin in enumerate(record['plugins']):
                        number+=1
                        try:
                            fx=make_insert(plugin,entries,number)
                        except ValueError as exc:
                            if not static_only:raise
                            skipped.append(dict(plugin,slot=plugin.get('slot',index)))
                            report['skipped_inserts'].append(dict(track=record['name'],name=plugin['name'],reason=str(exc)))
                            report['warnings'].append(record['name']+' / '+plugin['name']+'：'+str(exc)+' 原始预置已保存。')
                            continue
                        readback=read_insert(fx,entries,len(states))
                        expected=state_summary(from_envelope(plugin['preset']))
                        if any(readback['chunks'].get(k)!=v for k,v in expected['chunks'].items() if k in ('Comp','Cont')):
                            raise ValueError(plugin['name']+'：离线写入插件状态回读不一致。')
                        track.setdefault('externalFxChain',[]).append(fx)
                        states.append(dict(name=plugin['name'],cid=plugin['cid'],source_state=expected,ace_state=state_summary(from_envelope(readback['preset']))))
                    report['plugin_results'].append(dict(track=record['name'],plugins=states))
                    if skipped:presets.append((dict(record,plugins=skipped),track.get('uuid','master'),i))
            else:
                presets=[(r,t.get('uuid','master'),i) for r,t,i in targets]
            report['saved_plugin_presets']=save_plugin_presets(presets,out/'插件状态') if presets else 0
            report['plugin_mode']='offline_state' if load_plugins else 'presets_only'
        manifest=dict(schema=2,source_archive=str(source),source_relative=os.path.relpath(source,ace_dir) if source.drive==ace_dir.drive else None,
                      source_sha256=archive.source_sha256,source_media=source_media,events=mappings,tracks=track_map)
        (ace_dir/'transfer-map.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
        if sha(source)!=archive.source_sha256:raise ValueError('转换期间来源工程发生变化。')
        save_project(path,project)
        report['copied_media_files']=0
        report['external_media_files']=sum(not p.is_relative_to(ace_dir) for p in set(media.values()))
        report['derived_media_files']=sum(1 for p in media_dir.glob('*') if p.is_file())
        if report['marker_max_rounding_seconds']:
            report['warnings'].append('ACE 以可编辑范围显示点标记，原始编号与位置保存在 transfer-map.json。')
        report.update(status='complete',ace_project=str(path),verified_saved_events=len(mappings),
                      restored_vst3_inserts=sum(len(row['plugins']) for row in report['plugin_results']))
        checkpoint()
        log(f'文件回读通过：{report["audio_events"]} 个音频块、{report["range_markers"]+report.get("point_markers",0)} 个标记、{report["restored_vst3_inserts"]} 个工程内 VST3 插入。')
        log('转换完成：'+str(path))
        return report
    except Exception as error:
        report.update(status='failed',error=str(error))
        checkpoint()
        raise
