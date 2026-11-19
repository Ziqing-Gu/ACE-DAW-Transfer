"""Write fixed-size edits into a hash-bound native source, preserving other native objects."""
import hashlib
import json
import math
import os
import struct
import tempfile
from collections import Counter
from pathlib import Path

from archive_reader import Archive, number
from convert_project import sha
from native_events import Arrangement
from native_inventory import MEDIA_CLASSES, read_project_timing
from native_mixer import _trackinfo, _standard_pan
from plugin_xml import read_inserts


def require(valid, reason):
    if not valid:
        raise ValueError(reason + '。可使用同目录的轨道 XML。')


def make_layout(data):
    rows = []
    for group in ('videoTracks', 'markerTracks', 'tracks'):
        for track in data.get(group, []):
            clips = track.get('patterns', [])
            if clips or (group == 'tracks' and track.get('type') == 'audio'):
                def requested(item):
                    return None if item.get('_keep_native_color') else item.get('color')
                rows.append(dict(track_uuid=track['uuid'], event_uuids=[c['uuid'] for c in clips],
                                 color=requested(track), marker_colors={c['uuid']:requested(c) for c in clips}
                                 if group=='markerTracks' else {}))
    return rows


def unique_index(items, key):
    result = {}
    for item in items:
        ident = item.get(key)
        require(isinstance(ident, str) and ident and ident not in result, '工程映射缺少或重复 UUID')
        result[ident] = item
    return result


def origin_files(ace,data):
    original = Path(data['source_archive']).resolve()
    candidates=[original]
    for key in ('source_relative','source_copy'):
        if data.get(key):
            candidates.append((ace.parent/data[key]).resolve())
    return original,[p for p in dict.fromkeys(candidates) if p.suffix.lower()==original.suffix.lower() and p.is_file()]


def available_native_format(ace):
    try:
        data=json.loads((ace.parent/'transfer-map.json').read_text(encoding='utf-8'))
        if (not isinstance(data,dict) or data.get('schema')!=2 or
            not isinstance(data.get('source_sha256'),str) or len(data['source_sha256'])!=64 or
            not isinstance(data.get('tracks'),list) or not isinstance(data.get('events'),list)):
            return None
        original,candidates=origin_files(ace,data)
        ext=original.suffix.lower()
        if candidates and ext in ('.cpr','.npr'):
            return ext[1:]
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):
        pass
    return None


def provenance(source_ace, destination):
    ace = Path(source_ace).resolve()
    manifest = ace.parent / 'transfer-map.json'
    require(ace.suffix.lower() == '.acep' and ace.is_file(), '缺少本次读取的 ACE 工程')
    require(manifest.is_file(), '此 ACE 工程没有可核对的原生来源，暂不能从零写出 CPR/NPR')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    require(data.get('schema') == 2, '工程映射没有记录可核对的原生轨道和事件身份')
    ext = destination.suffix.lower()
    require(ext in ('.cpr', '.npr'), '目标原生格式无效')
    original,candidates=origin_files(ace,data)
    require(original.suffix.lower() == ext, '原生写回目前支持返回同一宿主格式')
    require(len(data['source_sha256']) == 64, '缺少来源工程指纹')
    base=next((p for p in candidates if sha(p)==data['source_sha256']),None)
    require(base is not None,'原始 CPR/NPR 已移动或内容已更改，无法按来源写回。仍可导出轨道 XML。')
    from native_projects import read_header, PROJECT_TYPES
    require(PROJECT_TYPES[ext].encode() + b'\0' in read_header(base)[:1024], '原生来源宿主与目标格式不符')
    return data, base


class Changes:
    def __init__(self, arrangement):
        self.a = arrangement
        self.writes = {}
        self.fields = Counter()
        self.events = set()

    def field(self, start, old, new, label, event_id=None):
        require(len(old) == len(new) and bytes(self.a.view[start:start+len(old)]) == old,
                '原生字段的原值或长度与解析结果不一致')
        position = self.a.offset + start
        if position in self.writes:
            require(self.writes[position] == (old, new), '多个引用要求向同一字段写入不同值')
        elif old != new:
            self.writes[position] = (old, new)
            self.fields[label] += 1
            if event_id:
                self.events.add(event_id)

    def numeric(self, pos, fmt, new, label, event_id=None):
        size = struct.calcsize(fmt)
        old = bytes(self.a.view[pos:pos+size])
        self.field(pos, old, struct.pack(fmt, new), label, event_id)


def plugin_signature(plugins):
    return [(p['cid'], bool(p['enabled'] and not p['bypassed']),
             {k: v for k,v in p['chunks'].items() if k in ('Comp','Cont')}) for p in plugins]


def plan_plugins(a, lo, hi, before, after, edits):
    from plugin_state import parse_preset, from_envelope
    require(len(before)==len(after),'增加或删除 VST3 插入需要重建原生槽位')
    fields={b'Comp':'audioComponent',b'Cont':'editController'}
    region=None
    for old,new in zip(before,after):
        require(old['cid']==new['cid'] and
                (old['enabled'] and not old['bypassed'])==(new['enabled'] and not new['bypassed']),
                '原生插件顺序、类别或启用状态发生变化')
        _,prior=parse_preset(from_envelope(old['preset']))
        _,requested=parse_preset(from_envelope(new['preset']))
        for tag,field in fields.items():
            first=prior.get(tag)
            second=requested.get(tag)
            if first==second:
                continue
            require(first is not None and second is not None and len(first)==len(second),
                    'VST3 处理状态变长或块结构变化，需要重建原生对象')
            prefix=attr_key(field,2)+struct.pack('>HI',7,len(first))
            if region is None:
                region=bytes(a.view[lo:hi])
            require(region.count(prefix+first)==1,'VST3 插槽状态在通道内不能唯一定位')
            pos=lo+region.index(prefix+first)+len(prefix)
            edits.field(pos,first,second,'vst3_'+tag.decode('ascii'))


def attr_key(name, kind):
    value = name.encode('ascii') + b'\0'
    return struct.pack('>I',len(value)) + value + struct.pack('>H',kind)


def plan_channel(a, row, target, changes):
    _, _, start, end, _, node_end, _ = row
    _, attrs, flags, device_end = _trackinfo(a.archive, start, end)
    volume = attrs.find('member[@name="Volume"]')
    require(volume is not None, '轨道缺少可核对的通道推子')
    state = (flags & ~32, number(attrs, 'SoloFlag'))
    require(state in ((0,0),(2,1)) and (state==(2,1))==target.get('mute',False),
            '轨道静音或独奏状态变化，需要重建原生通道属性')
    panner = attrs.find('member[@name="Panner"]')
    require(panner is not None,'原生通道声像器设置缺失')
    blob = panner.find('bin[@name="audioComponent"]')
    require(blob is not None,'声像状态不可读取')
    state = bytes.fromhex(blob.text)
    require(len(state)==20 and state[4:]==bytes.fromhex('0000003F040000000200000000000000'),
            '原生轨道使用其他声像器')
    pan=target.get('pan',0)
    if abs(_standard_pan(panner)-pan)>1e-6:
        prefix=attr_key('audioComponent',2)+struct.pack('>HI',7,len(state))
        region=bytes(a.view[node_end:device_end])
        require(region.count(prefix+state)==1,'声像状态在原生通道内不唯一')
        at=node_end+region.index(prefix+state)+len(prefix)
        changes.field(at,state[:4],struct.pack('<f',(pan+10)/20),'track_pan')
    source_plugins = read_inserts(attrs.find('member[@name="InsertFolder"]'),allow_mono_main=True)
    plan_plugins(a,node_end,device_end,source_plugins,target.get('plugins',[]),changes)
    level = number(volume, 'AnchorValue')
    gain = target['gain']
    if abs(level - gain) <= 1e-5 and abs(number(volume,'Value')-target['fader_value'])<=.002:
        return
    region = bytes(a.view[node_end:device_end])
    prefix = attr_key('InputPhase',1) + bytes(8) + attr_key('Volume',2) + struct.pack('>HI',6,2) + attr_key('Value',4)
    middle = attr_key('AnchorValue',4)
    require(region.count(prefix)==1,'原生通道推子的类型与位置不唯一')
    pos = node_end + region.index(prefix) + len(prefix)
    anchor = pos + 8 + len(middle)
    require(bytes(a.view[pos+8:anchor]) == middle and
            anchor+8 <= device_end,
            '原生通道推子布局不受支持')
    value = target['fader_value']
    changes.field(pos,struct.pack('>d',number(volume,'Value')),struct.pack('>d',value),'track_fader')
    changes.field(anchor,struct.pack('>d',level),struct.pack('>d',gain),'track_gain_db')


def same_media(source, target, kind, cache):
    p, q = Path(source).resolve(), Path(target).resolve()
    require(p.is_file() and q.is_file(), '原生工程或 ACE 引用的媒体缺失')
    if p == q:
        return
    pair = (p, p.stat().st_size, p.stat().st_mtime_ns, q, q.stat().st_size, q.stat().st_mtime_ns, kind)
    if pair in cache:
        return
    if kind == 'video':
        require(p.stat().st_size==q.stat().st_size and sha(p)==sha(q),'视频内容变化，需要原生素材池重建')
    else:
        import soundfile as sf
        import numpy as np
        with sf.SoundFile(p) as left, sf.SoundFile(q) as right:
            require((len(left),left.samplerate,left.channels)==(len(right),right.samplerate,right.channels),
                    '音频规格变化，需要原生素材池重建')
            for block in left.blocks(blocksize=262144,dtype='float32',always_2d=True):
                require(np.array_equal(block,right.read(len(block),dtype='float32',always_2d=True)),
                        '音频采样内容已替换，需要原生素材池重建')
    cache.add(pair)


def plan_fade(a, pos, end, native, target, wanted_type, edits, event_id):
    w = a.width
    if a.archive.uint(pos,w,end)==0:
        require(target['length']==0 and native['length']==0,'添加淡化需要新建原生淡化对象')
        return pos+w
    kind, b, e, after = a.archive.obj(pos,end)
    require(kind==wanted_type,'原生淡化对象类型不匹配')
    require(native['native'] and target['native'] and target['length']>0,'删除或改变原生淡化类型需要重建对象')
    if abs(target['length']-native['length']) <= 1e-9:
        return after
    typ, cs, ce, trail = a.archive.obj(b,e)
    count = a.archive.uint(cs,4,ce)
    require(typ=='MLinearInterpolator' and 2<=count<=65536 and cs+4+16*count+32==ce and trail+8==e,
            '淡化控制点布局未知')
    pts = target['points']
    require(len(pts)==count,'淡化曲线控制点数量变化')
    old = bytes(a.view[cs+4:ce])
    xmin,xmax,ymin,ymax = struct.unpack_from('>dddd',old,16*count)
    require(xmin==0 and math.isclose(xmax/48000,native['length'],abs_tol=1e-9),
            '原生淡化区间与声音参数不符')
    new = bytearray(old)
    max_new = target['length']*48000
    for i,(nx,ny) in enumerate(pts):
        x,y=struct.unpack_from('>dd',old,i*16)
        require(abs(x/xmax-nx)<1e-9 and abs(y-ny)<1e-9,'淡化曲线形状变化')
        struct.pack_into('>d',new,i*16,nx*max_new)
    struct.pack_into('>d',new,16*count+8,max_new)
    edits.field(cs+4,old,bytes(new),wanted_type,event_id)
    return after


def plan_event(a, row, raw, source, target, edits, event_id, media_cache, bpm):
    kind,s,e,mode,period = raw
    require(kind in ('MAudioEvent','MVideoEvent','MRangeMarkerEvent','MMarkerEvent'),'Part 或未知类型暂不能离线写回')
    require(source['name']==target['name'],'片段或标记名称变化，需要重建原生字符串')
    p=s
    if kind in ('MRangeMarkerEvent','MMarkerEvent'):
        require((kind=='MMarkerEvent')==(target.get('marker_kind')=='point'),'标记类型变化，需要重建原生事件')
        require(source['marker_id']==target['marker_id'],'标记编号与源文件不一致')
        _,p=a.text(p,e)
        p+=4
    require(mode in (0,1) and p+26<=e,'事件单位或范围不明')
    flags,start,length,offset=struct.unpack_from('>Hddd',a.view,p)
    unit = 1/(bpm*8) if mode==0 else period
    old_start=source['start']
    length_unit = 1/48000 if kind=='MAudioEvent' else unit
    if kind in ('MAudioEvent','MVideoEvent'):
        same_media(source['path'],target['path'],'audio' if kind=='MAudioEvent' else 'video',media_cache)
    _,track_start,track_len,_ = struct.unpack_from('>Hddd',a.view,row[2])
    require(target['start']+target['duration'] <= (track_start+track_len)*unit+1e-7 or
            (abs(target['start']-old_start)<1e-9 and abs(target['duration']-source['duration'])<1e-9),
            '片段超出来源编排的已保存长度')
    if kind=='MMarkerEvent':
        require(target['duration']==0 and target['start']>=0,'点标记必须具有有效的起点且没有范围长度')
        if abs(target['start']-old_start)>1e-9:
            edits.numeric(p+2,'>d',start+(target['start']-old_start)/unit,'position',event_id)
        edits.numeric(p,'>H',(flags & ~2) | (0 if target['enabled'] else 2),'mute',event_id)
        return
    require(abs(length*length_unit-source['duration'])<1e-6,'原生片段长度与所在时间域不符')
    for loc,old,new,label in ((p+2,start,start+(target['start']-old_start)/unit,'position'),
                              (p+10,length,target['duration']/length_unit,'duration'),
                              (p+18,offset,offset+(target['offset']-source['offset'])/length_unit,'source_offset')):
        scale=unit if label=='position' else length_unit
        if abs(new-old)*scale>1e-9:
            require(new>=0 and math.isfinite(new),'新的事件位置、长度或源偏移无效')
            edits.numeric(loc,'>d',new,label,event_id)
    new_flags = (flags & ~2) | (0 if target['enabled'] else 2)
    edits.numeric(p,'>H',new_flags,'mute',event_id)
    if kind!='MAudioEvent':
        return
    typ,base,limit,q=a.archive.obj(s+26,e)
    require(typ=='PAudioClip','音频块的来源对象类型未知')
    _,segments,_=a.audio_source(base,limit)
    require(len(segments)==1 and segments[0]['rate']==48000,
            '合成音频或其他采样率的片段暂不能写回')
    seg=segments[0]
    require(target['offset']*48000 >= seg['offset']-1e-5 and
            (target['offset']+target['duration'])*48000<=seg['offset']+seg['frames']+1e-5,
            '剪裁超出原生素材池的分段边界')
    _,q=a.attributes(q,e,fourcc=True)
    q+=4
    for side,typ in (('fade_in','MFadeIn'),('fade_out','MFadeOut')):
        q=plan_fade(a,q,e,source[side],target[side],typ,edits,event_id)
    _,q=a.text(q+8,e)
    require(a.archive.uint(q,4,e)==0 and bytes(a.view[q+8:q+14])==bytes(6),
            '音频块含尚未支持的增益或声道处理')
    typ,_,_,nxt=a.archive.obj(q+14,e)
    require(typ=='VolumeCurveDataNode' and nxt==e,'音频块结尾与已验证结构不符')
    require(struct.unpack_from('>f',a.view,q+4)[0]>0,'原生片段使用零振幅增益，超出 ACE 精确表示范围')
    if abs(source['gain']-target['gain'])>1e-6:
        edits.numeric(q+4,'>f',10**(target['gain']/20),'event_gain',event_id)

def replan(project, desired, layout, manifest, media_cache, media_paths=None):
    from native_events import read_native_events
    tracks,_=read_native_events(project,media_paths)
    source_tracks=unique_index(manifest['tracks'],'ace_track_uuid')
    source_events=unique_index(manifest['events'],'ace_clip_uuid')
    active=unique_index(layout,'track_uuid')
    require(set(active)==set(source_tracks),'轨道已添加、删除或身份发生变化')
    require(len(layout)==len(desired.tracks) and all(not row.get('omitted_empty_audio') for row in layout),
            '空音轨或未进入 XML 的轨道暂不能离线写回')
    require(len(source_tracks)==len(tracks),'拆分或合并轨道暂不能离线写回')
    ordered={};seen_clips=set();seen_tracks=set()
    with Arrangement(project,media_paths) as a:
        changes=Changes(a)
        from project_colors import assign_slots,color_field,native_palette,rgb
        requested=[c for link in layout for c in [link.get('color'),*link.get('marker_colors',{}).values()] if rgb(c)]
        palette=native_palette(a) if requested else ()
        require(not requested or palette,'工程缺少可核对的原生调色板')
        existing=[item.get('color') for track in tracks for item in [track,*track['events']]]
        slots=assign_slots(existing+requested,palette) if requested else {}
        changes.approximate_colors=sum(palette[slots[rgb(c)]]!=rgb(c) for c in set(requested))
        def plan_color(color,p,end,label,clip=None):
            if not rgb(color):return
            pos=color_field(a,p,end)
            require(pos is not None,'需要新增原生颜色字段，请导入此轨道 XML 以使用新颜色')
            changes.numeric(pos,'>q',slots[rgb(color)],label,clip)
        from native_mixer import _read_master
        master_issues=[]
        master=_read_master(a,master_issues.append,static_only=False)
        require(not master_issues,'原生主输出含未解析的设置：'+'；'.join(master_issues[:2]))
        request=getattr(desired,'master',None) or dict(gain=0,plugins=[])
        require(master is not None and abs(master['gain']-request['gain'])<1e-5 and
                plugin_signature(master['plugins'])==plugin_signature(request.get('plugins',request.get('_plugins',[]))),
                '主输出推子或插件链变化，不能离线写回')
        rows=list(a.track_rows())
        for link,target in zip(layout,desired.tracks):
            uid=link['track_uuid']
            mapped=source_tracks[uid]
            i=mapped['source_track_index']
            require(type(i) is int and 0<=i<len(tracks) and i not in seen_tracks,'原生轨道映射不唯一')
            seen_tracks.add(i)
            src=tracks[i]
            typ=target['type']
            require((target['name'],typ)==(src['name'],src['type'])==(mapped['name'],mapped['type']),
                    '轨道名称、类型或顺序变化，需要重建原生轨道')
            require(ordered.get(typ,-1)<i,'同类轨道次序变化，需要重排原生对象')
            ordered[typ]=i
            row=rows[src['_native_index']]
            require(row[0] in MEDIA_CLASSES and row[1]==src['name'],'原生编排轨道不匹配')
            if link.get('color'):
                _,_,_,after=a.archive.obj(row[2]+26,row[3])
                plan_color(link['color'],after,row[3],'track_color')
            if typ=='audio':
                plan_channel(a,row,target,changes)
            wanted_events=target['events']
            ids=link['event_uuids']
            require(len(ids)==len(wanted_events)==len(src['events']),'轨内有新增或删除的片段')
            raw=list(a.events(row[4],row[5]))
            require(len(raw)==len(ids),'Part 或已拆分片段暂不能离线写回')
            visits=set()
            for clip,target_event in zip(ids,wanted_events):
                require(isinstance(clip,str) and clip not in seen_clips and clip in source_events,
                        '片段已替换、复制或缺少原始 UUID')
                seen_clips.add(clip)
                mapped_event=source_events[clip]
                require((mapped_event['ace_track_uuid'],mapped_event['source_track_index'])==(uid,i),
                        '跨轨移动片段暂不能写回')
                j=mapped_event['source_event_index']
                require(type(j) is int and 0<=j<len(raw) and j not in visits,'原生事件索引不唯一')
                visits.add(j)
                event=src['events'][j]
                marker_color=link.get('marker_colors',{}).get(clip)
                if marker_color:
                    _,s,end,_,_=raw[j]
                    _,after=a.text(s,end)
                    plan_color(marker_color,after+30+a.width,end,'marker_color',clip)
                require(event.get('_source_event_index')==[j] and not event.get('_native_segments'),
                        'Part 或复合媒体池暂不能离线写回')
                require(not event.get('losses'),'该片段含额外音频处理，需要保留完整来源或使用 XML')
                plan_event(a,row,raw[j],event,target_event,changes,clip,media_cache,desired.bpm)
            require(len(visits)==len(raw),'原生事件映射覆盖不完整')
        require(seen_clips==set(source_events),'原有片段被删除或未进入 XML')
    return changes, len(tracks), len(seen_clips)


def write_project(xml, destination, *, source_bpm, source_ace, layout, log=print):
    """Return a verification report only after a new native document passes readback."""
    destination=Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(str(destination))
    manifest,source=provenance(source_ace,destination)
    timing=read_project_timing(source)
    require(isinstance(source_bpm,(int,float)) and math.isfinite(source_bpm) and math.isclose(source_bpm,timing['bpm'],rel_tol=1e-7),'工程速度已更改')
    desired=Archive(xml,native_timing=timing,native_output=True)
    require(not desired.errors,'生成的轨道 XML 暂不满足原生写回条件：'+'；'.join(desired.errors[:4]))
    cache=set()
    from native_pool import ace_sources, compact_pool, reference_media
    samples=ace_sources(source_ace,manifest)
    changes,track_count,event_count=replan(source,desired,layout,manifest,cache,samples)
    placement=sorted(changes.writes.items())
    require(all(x+len(v[0])<=y for (x,v),(y,_) in zip(placement,placement[1:])),'原生字段写入发生重叠')
    descriptor,temp=tempfile.mkstemp(prefix='.ACE-native-',suffix=destination.suffix,dir=destination.parent)
    temp=Path(temp)
    try:
        with os.fdopen(descriptor,'w+b') as dst,source.open('rb') as src:
            h=hashlib.sha256()
            while block:=src.read(8*1024*1024):
                dst.write(block)
                h.update(block)
            require(h.hexdigest()==manifest['source_sha256'],'来源工程在核对期间发生变化')
            for pos,(before,after) in placement:
                dst.seek(pos)
                require(dst.read(len(before))==before,'临时副本字段校验不一致')
                dst.seek(pos)
                dst.write(after)
            dst.flush()
            os.fsync(dst.fileno())
        removed,pool_warning=compact_pool(temp)
        media_report=reference_media(temp,destination,samples)
        require(read_project_timing(temp)==timing,'写回后工程时间设置变化')
        check,tracks,events=replan(temp,desired,layout,manifest,set())
        require(not check.writes and (tracks,events)==(track_count,event_count),'写回后的原生工程与 ACE 编辑不一致')
        # The link atomically publishes only if the final name was still free.
        # On Windows rename also guarantees no replacement and supports exFAT.
        if os.name=='nt':
            temp.rename(destination)
        else:
            os.link(temp,destination)
    finally:
        temp.unlink(missing_ok=True)
    report=dict(matched_saved_tracks=track_count, matched_saved_events=event_count,
                changed_fields=dict(changes.fields),changed_events=len(changes.events),
                native_write_mode='verified_compact_project' if removed else ('verified_source_edit' if placement else 'verified_external_references'),
                native_base_sha256=manifest['source_sha256'],
                native_pruned_pool_entries=removed, native_pool_warning=pool_warning,
                native_approximate_colors=changes.approximate_colors, **media_report)
    if pool_warning:log('素材池沿用原结构：'+pool_warning)
    log(f'已离线回读 {track_count} 条轨道、{event_count} 个片段/标记；已改动 {len(changes.events)} 个片段/标记。')
    return report
