"""Direct event and media-pool reader for measured Steinberg RIFF/RIF2 projects."""
import math
import mmap
import struct
from platform_support import stored_directory
from pathlib import Path, PureWindowsPath

from native_inventory import _Archive, FOLDERS, MEDIA_CLASSES, NativeInventoryError, read_project_timing


def need(test, reason):
    if not test:
        raise NativeInventoryError(reason)


class Arrangement:
    def __init__(self, project, media_paths=None):
        self.project = Path(project).resolve()
        self.file = self.mapping = self.view = None
        self.audio_pool = {}
        self.media_paths = media_paths or {}

    def __enter__(self):
        try:
            self.file = self.project.open('rb')
            self.mapping = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            data = self.mapping
            self.width = {b'RIFF': 4, b'RIF2': 8}.get(data[:4])
            w = self.width
            need(w and data[4+w:8+w] == b'NUND', '未识别的原生工程文件头。')
            p = 8 + w
            need(p + int.from_bytes(data[4:4+w], 'big') == len(data), '工程文件长度不一致。')
            root = b''
            while p < len(data):
                kind = data[p:p+4]
                base = p + 4 + w
                end = base + int.from_bytes(data[p+4:base], 'big')
                need(base <= end <= len(data), '工程分块越界。')
                if kind == b'ROOT':
                    need(end-base < 2048, '工程根标签过长。')
                    root = data[base:end]
                elif kind == b'ARCH':
                    if root.endswith(b'\x00\x00\x00\x0cPArrangement'):
                        need(self.view is None, '多编排工程无法唯一选择。')
                        self.offset = base
                        self.view = memoryview(data)[base:end]
                        self.archive = _Archive(self.view, w)
                    root = b''
                p = end
            need(self.view is not None, '缺少工程编排。')
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.view is not None:
            self.view.release()
            self.view = None
        if self.mapping is not None:
            self.mapping.close()
            self.mapping = None
        if self.file is not None:
            self.file.close()
            self.file = None

    def __exit__(self, *args):
        self.close()

    def track_rows(self):
        a = self.archive
        def walk(base, end, ancestors):
            need(len(ancestors) < 128, '轨道文件夹层级异常。')
            kind, _, attrs, limit = a.node(base, end)
            need(kind == 'MTrackList' and a.uint(attrs, 4, limit) == 1, '未知轨道列表结构。')
            n = a.uint(attrs + 14, 4, limit)
            p = attrs + 18
            need(n <= min(100000, (limit-p)//self.width), '轨道数量超出列表。')
            for _ in range(n):
                kind, start, stop, p = a.obj(p, limit)
                name = ''
                ta = node_end = None
                if kind in MEDIA_CLASSES or kind in FOLDERS or kind == 'MDeviceTrackEvent':
                    _, name, ta, node_end = a.node(start, stop)
                yield (kind, name, start, stop, ta, node_end, ancestors)
                if kind in FOLDERS:
                    yield from walk(start, stop, ancestors + (name,))
            need(p == limit, '轨道表尾部存在未读取数据。')
        typ, s, e, _ = a.obj(0, len(self.view))
        need(typ == 'PArrangement', '原生工程缺少编排。')
        typ, s, e, _ = a.obj(s, e)
        need(typ == 'MRoot', '原生工程缺少轨道树。')
        yield from walk(s, e, ())

    def text(self, pos, end):
        n = self.archive.uint(pos, 4, end)
        pos += 4
        need(n <= min(end-pos, 4*1024*1024), '工程文本长度异常。')
        raw = bytes(self.view[pos:pos+n])
        if raw.endswith(b'\0\xef\xbb\xbf'):
            raw = raw[:-4]
        elif raw.endswith(b'\0'):
            raw = raw[:-1]
        need(b'\0' not in raw, '无法识别工程文本编码。')
        return raw.decode('utf8'), pos + n

    def domain(self, pos, end):
        a = self.archive
        mode = a.uint(pos, 4, end)
        p = pos + 4
        if mode == 0:
            for target in ('MTempoTrackEvent', 'MSignatureTrackEvent'):
                kind, _, _, p = a.obj(p, end)
                need(kind == target, '无效的音乐时间域。')
            period = None
        else:
            need(mode in (1, 10) and p+8 <= end, '未知的工程时间域。')
            period, = struct.unpack_from('>d', self.view, p)
            need(math.isfinite(period) and period > 0, '时间单位周期无效。')
            p += 8
        return mode, period, p

    def events(self, attrs, end):
        mode, period, p = self.domain(attrs, end)
        n = self.archive.uint(p, 4, end)
        p += 4
        need(n <= min(1000000, (end-p)//self.width), '事件数量超过列表。')
        for _ in range(n):
            kind, s, e, p = self.archive.obj(p, end)
            yield kind, s, e, mode, period
        need(p == end, '事件列表尾部存在未读取数据。')

    def fnpath(self, start, end):
        filename, p = self.text(start, end)
        p += 4  # Mac file type
        for _ in range(3):
            _, p = self.text(p, end)
        p += 6
        directory, p = self.text(p, end)
        need(p == end and filename and PureWindowsPath(filename).name == filename,
             '无法确定媒体池文件名或目录。')
        return filename, directory

    def resolve_path(self, filename, directory, video=False):
        folder = stored_directory(directory)
        first = folder / filename
        if not first.is_absolute():
            first = self.project.parent / first
        if first in self.media_paths:
            return self.media_paths[first]
        if not hasattr(self,'saved_folder'):
            from native_pool import saved_project_folder
            try:self.saved_folder=saved_project_folder(self)
            except (ValueError,UnicodeError):self.saved_folder=None
        if self.saved_folder and first.is_relative_to(self.saved_folder):
            local=self.project.parent/first.relative_to(self.saved_folder)
            if local.is_file():return local.resolve()
        if first.is_file():
            return first.resolve()
        candidates = [self.project.parent / name / filename for name in ('', 'Audio', 'Edits', 'Video')]
        valid = {p.resolve() for p in candidates if p.is_file()}
        need(len(valid) == 1, '媒体缺失或重定位不唯一：' + str(first))
        return valid.pop()

    def attributes(self, pos, end, fourcc=False, depth=0):
        need(depth < 64, '属性树嵌套层级异常。')
        a = self.archive
        n = a.uint(pos,4,end)
        pos += 4
        need(n <= (end-pos)//6, '工程属性列表数量异常。')
        keys = []
        for _ in range(n):
            if fourcc:
                need(pos+4 <= end,'无效工程事件标签。')
                key = bytes(self.view[pos:pos+4]).decode('latin1')
                pos += 4
            else:
                key, pos = self.text(pos,end)
            keys.append(key)
            typ = a.uint(pos,2,end)
            pos += 2
            if typ in (1,4):
                pos += 8
            elif typ == 8:
                _, pos = self.text(pos,end)
            elif typ in (0x12,0x22,0x82,0x92,0xa2):
                _, _, _, pos = a.obj(pos,end)
            elif typ == 2:
                sub = a.uint(pos,2,end)
                pos += 2
                if sub == 6:
                    _, pos = self.attributes(pos,end,depth=depth+1)
                elif sub == 7:
                    pos += 4 + a.uint(pos,4,end)
                else:
                    raise NativeInventoryError(f'未知素材属性 {key} 类型 {sub}。')
            else:
                raise NativeInventoryError(f'未知素材属性 {key} 类型 {typ}。')
            need(pos <= end, '素材属性超出对象范围。')
        return keys, pos

    def audio_source(self, base, end):
        if base in self.audio_pool:
            return self.audio_pool[base]
        a, w = self.archive, self.width
        clip_name, p = self.text(base, end)
        mode, period, p = self.domain(p, end)
        need(mode == 10, '音频素材时间单位未知。')
        count = a.uint(p, 4, end)
        p += 4
        need(count <= (end-p)//w, '音频素材事件列表异常。')
        for _ in range(count):
            _, _, _, p = a.obj(p, end)
        typ, ps, pe, p = a.obj(p, end)
        need(typ == 'FNPath', '音频媒体池缺少文件路径。')
        _, p = self.text(p,end)  # source UID
        keys, p = self.attributes(p+14,end)
        typ, b, e, trailing = a.obj(p,end)
        need(typ == 'AudioCluster', '素材池缺少音频段表。')
        need(trailing + 6 <= end, '音频素材记录不完整。')
        path_type, _, _, q = a.obj(b,e)
        need(path_type == 'FNPath', '音频池路径引用无效。')
        streams = a.uint(q+2,4,e)
        q += 6
        need(0 < streams <= min(65536,(e-q)//w), '音频流数量异常。')
        sources = {}
        for _ in range(streams):
            typ, fs, fe, q = a.obj(q,e)
            need(typ == 'AudioFile' and fs+18+w <= fe, '音频池子流格式未知。')
            # File frame count is always int64, including 32-bit RIFF archives.
            frames, _, _, channels, rate = struct.unpack_from('>QHHHf',self.view,fs)
            typ, actual_path, actual_end, _ = a.obj(fs+18,fe)
            need(typ == 'FNPath', '实际音频流缺少路径。')
            filename, directory = self.fnpath(actual_path,actual_end)
            need(math.isfinite(rate) and abs(rate*period-1)<1e-5 and channels in (1,2),
                 '未支持的音频声道或采样率。')
            sources[fs] = (filename,directory,frames,channels,int(rate))
        parts = a.uint(q+2,4,e)
        q += 6
        need(0 < parts <= (e-q)//w,'音频池分段数量异常。')
        segments = []
        cursor = 0
        for _ in range(parts):
            typ, ss, se, q = a.obj(q,e)
            need(typ == 'AClusterSegment' and se-ss == w+24, '音频池分段结构无法确认。')
            segtype, fs, _, after = a.obj(ss,se)
            need(segtype == 'AudioFile' and fs in sources, '音频段引用未知子流。')
            source_offset, length, position = struct.unpack_from('>QQQ',self.view,after)
            name, directory, frames, channels, rate = sources[fs]
            need(source_offset+length <= frames and position == cursor, '音频池分段不连续或超出源文件。')
            cursor += length
            path = self.resolve_path(name,directory)
            import soundfile
            info = soundfile.info(str(path))
            need(info.samplerate == rate and info.channels == channels and info.frames >= source_offset+length,
                 '磁盘音频规格与工程池记录不一致：'+str(path))
            segments.append(dict(path=str(path),offset=source_offset,frames=length,
                                 position=position,channels=channels,rate=rate))
        need(q == e and len({(s['rate'],s['channels']) for s in segments}) == 1,
             '音频池声道映射或分段布局未知。')
        result = (clip_name, segments, cursor)
        self.audio_pool[base] = result
        return result
    def curve(self, pos, limit, typ, rate):
        a = self.archive
        if a.uint(pos,self.width,limit) == 0:
            return dict(length=0.,native=True,points=[]),pos+self.width
        kind, s, e, pos = a.obj(pos,limit)
        need(kind == typ,'未知淡化对象。')
        kind, s, ce, _ = a.obj(s,e)
        need(kind == 'MLinearInterpolator','未支持的淡化曲线：'+kind)
        n = a.uint(s,4,ce)
        need(2 <= n <= 1000000 and s+4+16*n+32 == ce,'淡化点表布局异常。')
        pairs = list(struct.iter_unpack('>dd',self.view[s+4:s+4+16*n]))
        span = pairs[-1][0]
        need(pairs[0][0] == 0 and math.isfinite(span) and span > 0 and
             all(math.isfinite(y) and 0 <= y <= 1 for _,y in pairs) and
             all(x1 < x2 for (x1,_),(x2,_) in zip(pairs,pairs[1:])), '淡化曲线越界。')
        import numpy as np
        pts = [[x/span,y] for x,y in pairs]
        u = np.linspace(0,1,2049)
        measured = np.interp(u,[x for x,_ in pts],[y for _,y in pts])
        expected = u*u*(3-2*u)
        if typ == 'MFadeOut':
            expected = 1-expected
        return dict(length=span/rate,native=bool(np.max(abs(measured-expected)) < 2e-6),points=pts),pos

    def read_audio(self, start, end, factor, origin, warnings, track_name):
        a = self.archive
        flags, pos, length, offset = self.geometry(start,end)
        kind, cs, ce, p = a.obj(start+26,end)
        need(kind == 'PAudioClip', '音频事件引用了未知的素材。')
        name, segs, frames = self.audio_source(cs,ce)
        keys, p = self.attributes(p,end,fourcc=True)
        losses = []
        def record_loss(code, detail, bakeable=False):
            losses.append(dict(code=code,detail=detail,bakeable=bakeable))
            warnings.append(track_name+'：'+detail)
        extra_keys = [k for k in keys if k != 'braF']
        if extra_keys:
            record_loss('event_attributes','事件附加状态 '+', '.join(extra_keys)+' 保存在原生工程。')
        p += 4  # event priority
        rate = segs[0]['rate']
        fi,p = self.curve(p,end,'MFadeIn',rate)
        fo,p = self.curve(p,end,'MFadeOut',rate)
        desc,p = self.text(p+8,end)
        need(p+14 <= end, '音频事件音量记录不完整。')
        inverted = a.uint(p,4,end)
        gain, = struct.unpack_from('>f',self.view,p+4)
        need(math.isfinite(gain) and gain >= 0 and inverted in (0,1),'片段增益无效。')
        if inverted:
            record_loss('polarity','片段极性反转可写入独立音频。',True)
        if any(self.view[p+8:p+14]):
            record_loss('processing_flags','未映射的额外处理标志 '+bytes(self.view[p+8:p+14]).hex()+'。')
        vc,vs,ve,last = a.obj(p+14,end)
        need(vc == 'VolumeCurveDataNode' and last == end, '音频事件包络结构未知。')
        _, vp = self.text(vs,ve)
        envelope = []
        for kind, ps, pe, mode, period in self.events(vp,ve):
            need(kind == 'VolumeCurveEvent' and mode == 10 and abs(period*rate-1)<1e-5
                 and ps+18 <= pe, '不能识别片段包络节点。')
            x, y, segment_shape = struct.unpack_from('>ddH', self.view, ps)
            need(math.isfinite(x) and math.isfinite(y) and y >= 0, '片段包络点无效。')
            point = dict(frame=x,amplitude=y,shape=segment_shape)
            if segment_shape or ps+18 != pe:
                point['curve_data_hex'] = bytes(self.view[ps+18:pe]).hex()
            envelope.append(point)
        curved = any(point['shape'] or 'curve_data_hex' in point for point in envelope)
        ordered = all(p1['frame'] < p2['frame'] for p1,p2 in zip(envelope,envelope[1:]))
        if envelope:
            if curved or not ordered:
                record_loss('volume_envelope_curve','片段含 Bézier 或非单调音量包络；原始曲线点已记录，ACE 保留静态电平。')
            else:
                record_loss('volume_envelope_linear','片段音量包络的直线段可按原始采样位置和幅度点写入音频。',True)
        db = 20*math.log10(gain) if gain else -70.
        if gain and not -70 <= db <= 30:
            record_loss('gain_range','片段静态增益超出 ACE 可写范围，已限于 -70 至 +30 dB。')
        source_path = segs[0]['path']
        seg = segs[0]
        materialize = len(segs) > 1
        if not materialize:
            offset += seg['offset']
        source_duration = frames/rate if materialize else (frames+seg['offset'])/rate
        ev = dict(name=desc or name,start=origin+pos*factor,duration=length/rate,
                  offset=offset/rate,path=source_path,enabled=not bool(flags&2) and gain>0,
                  gain=max(-70.,min(30.,db)),fade_in=fi,fade_out=fo,source_duration=source_duration,
                  channels=seg['channels'],sample_rate=rate)
        if inverted:
            ev['_native_inverted'] = True
        if envelope:
            ev['_native_volume_envelope'] = envelope
            ev['_native_envelope_origin'] = 0 if materialize else seg['offset']
        if losses:
            ev['losses'] = losses
        if materialize:
            ev['_native_segments'] = segs
        return ev

    def geometry(self, start, end):
        need(start+26 <= end,'原生事件头部不完整。')
        flags, pos, length, offset = struct.unpack_from('>Hddd',self.view,start)
        need(all(map(math.isfinite,(pos,length,offset))) and length >= 0, '原生工程包含无效坐标。')
        return flags,pos,length,offset

    def read_video(self, start,end,factor,origin):
        flags,pos,length,offset = self.geometry(start,end)
        kind,s,e,_ = self.archive.obj(start+26,end)
        need(kind == 'PVideoClip','视频源类型未知。')
        name,q = self.text(s,e)
        _,period,q = self.domain(q,e)
        need(period == 1 and self.archive.uint(q,4,e) == 0, '视频源使用未知时间单位。')
        kind,s,e,_ = self.archive.obj(q+4,e)
        need(kind == 'FNPath','视频媒体路径未知。')
        filename,directory = self.fnpath(s,e)
        return dict(name=name,start=origin+pos*factor,duration=length,offset=offset,
                    path=str(self.resolve_path(filename,directory)),enabled=not bool(flags&2),gain=0.)

    def read_marker(self,kind,start,end,factor,origin):
        name,pos = self.text(start,end)
        marker_id = self.archive.uint(pos,4,end)
        flags,place,length,offset = self.geometry(pos+4,end)
        point = kind == 'MMarkerEvent'
        return dict(name=name or str(marker_id), start=origin+place*factor, duration=0. if point else length*factor,
                    offset=0., gain=0., enabled=not bool(flags&2),marker_kind='point' if point else 'range',
                    marker_id=marker_id)

    def read_part(self, start,end, factor, origin, bpm, warnings, track_name):
        flags,position,length,part_offset = self.geometry(start,end)
        kind,s,e,_ = self.archive.obj(start+26,end)
        need(kind == 'MAudioPart','音频 Part 节点未知。')
        _,attrs = self.text(s,e)
        part_start = origin + position*factor
        part_end = part_start + length*factor
        result = []
        for child_kind,bs,be,domain,period in self.events(attrs,e):
            need(child_kind == 'MAudioEvent','包含嵌套或未知类型的音频 Part。')
            cf = time_factor(domain,period,bpm)
            item = self.read_audio(bs,be,cf,part_start-part_offset*factor,warnings,track_name)
            st = item['start']
            en = st+item['duration']
            overlap_start = max(st,part_start)
            overlap_end = min(en,part_end)
            if overlap_end <= overlap_start:
                warnings.append(track_name+'：音频 Part 中完全位于可见范围外的素材保存在源工程。')
                continue
            if overlap_start-st > 1e-7 or en-overlap_end > 1e-7:
                need(not item['fade_in']['length'] and not item['fade_out']['length'],
                     '音频 Part 裁切穿过带有淡化的子事件。')
                item['offset'] += overlap_start-st
                item['duration'] = overlap_end-overlap_start
                item['start'] = overlap_start
            if flags & 2:
                item['enabled'] = False
            result.append(item)
        return result


def time_factor(mode,period,bpm):
    if mode == 0:
        return 60/(bpm*480)
    need(mode == 1,'轨道时间单位不适用于音视频事件。')
    return period


def read_native_events(path, media_paths=None):
    """Return transferable arrangement tracks and a list of explicit sound/layout limitations."""
    bpm = read_project_timing(path)['bpm']
    warnings, tracks, omitted = [], [], []
    kinds = {'MAudioTrackEvent':'audio','MVideoTrackEvent':'video','MMarkerTrackEvent':'marker'}
    from project_colors import native_palette,native_color
    with Arrangement(path,media_paths) as record:
        try:palette=native_palette(record)
        except (ValueError,UnicodeError):palette=()
        def colors(pos,end):
            try:return native_color(record,pos,end,palette)
            except (ValueError,UnicodeError):
                warnings.append('有颜色属性的结构无法识别，沿用目标默认颜色。')
                return {}
        for idx,(kind,name,base,end,attrs,node_end,ancestors) in enumerate(record.track_rows()):
            if kind not in kinds:
                if kind not in FOLDERS and kind not in ('MTempoTrackEvent','MSignatureTrackEvent','MDeviceTrackEvent'):
                    omitted.append(dict(code='unsupported_track', detail=f'轨道 {name or idx}（{kind}）不能转换为 ACE 音频。',bakeable=False))
                continue
            track = dict(type=kinds[kind],name=name,gain=0.,events=[],_native_index=idx)
            _,_,_,after=record.archive.obj(base+26,end)
            track.update(colors(after,end))
            _,origin,_,track_offset = record.geometry(base,end)
            need(track_offset == 0,'轨道本身带有不支持的偏移：'+name)
            for i,(ty,s,e,domain,period) in enumerate(record.events(attrs,node_end)):
                factor = time_factor(domain,period,bpm)
                try:
                    if kind == 'MAudioTrackEvent' and ty == 'MAudioEvent':
                        batch = [record.read_audio(s,e,factor,origin,warnings,name)]
                    elif kind == 'MAudioTrackEvent' and ty == 'MAudioPartEvent':
                        batch = record.read_part(s,e,factor,origin,bpm,warnings,name)
                        warnings.append(name+'：音频 Part 已展开为独立片段。')
                    elif kind == 'MVideoTrackEvent' and ty == 'MVideoEvent':
                        batch = [record.read_video(s,e,factor,origin)]
                    elif kind == 'MMarkerTrackEvent' and ty in ('MRangeMarkerEvent','MMarkerEvent'):
                        batch = [record.read_marker(ty,s,e,factor,origin)]
                        _,pos=record.text(s,e)
                        batch[0].update(colors(pos+30+record.width,e))
                    else:
                        raise NativeInventoryError('未知原生事件类型：'+ty)
                    for child,item in enumerate(batch):
                        item['_source_event_index'] = [i,child] if ty=='MAudioPartEvent' else [i]
                        need(item['start']>=-1e-8 and item['offset']>=-1e-8,
                             '可见片段起始或源偏移为负。')
                        if item.get('source_duration') is not None:
                            need(item['duration']>0,'音频片段长度无效。')
                            need(item['duration']+item['offset'] <= item['source_duration']+1/item['sample_rate'],
                                 '片段超出音频素材范围。')
                        track['events'].append(item)
                except (NativeInventoryError,UnicodeError,struct.error) as exc:
                    raise NativeInventoryError(f'{name} / 事件 {i+1}：{exc}') from exc
            tracks.append(track)
    if any(e.get('_native_segments') for t in tracks for e in t['events']):
        warnings.append('含离线编辑的片段按媒体池的原始文件和 Edits 分段重建为独立音频。')
    if omitted:
        warnings.extend(l['detail'] for l in omitted)
        need(tracks, '工程仅含不支持的轨道：'+'、'.join(l['detail'] for l in omitted))
        tracks[0]['project_losses'] = omitted
    return tracks,list(dict.fromkeys(warnings))


def materialize_native_media(tracks,directory,*,bake_event_processing=False):
    """Write compound source clips once, then replace the event media paths in place."""
    from hashlib import sha256
    import json
    import os
    from uuid import uuid4
    import soundfile as sf
    directory = Path(directory)
    created = {}
    for track in tracks:
        for item in track['events']:
            segs = item.get('_native_segments',[])
            if not segs:
                continue
            key = sha256(json.dumps(segs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if key not in created:
                directory.mkdir(parents=True,exist_ok=True)
                dst = directory / f'Pool_{key[:20]}_{uuid4().hex[:8]}.wav'
                tmp = dst.with_suffix('.partial')
                total = sum(part['frames'] for part in segs)
                fmt = 'RF64' if total*segs[0]['channels']*4 + 128 >= 2**32 else 'WAV'
                with sf.SoundFile(str(tmp),'x',format=fmt,subtype='FLOAT',samplerate=segs[0]['rate'],
                                  channels=segs[0]['channels']) as out:
                    for seg in segs:
                        with sf.SoundFile(seg['path']) as inp:
                            need(inp.channels==out.channels and inp.samplerate==out.samplerate and
                                 inp.frames>=seg['offset']+seg['frames'],'离线音频源规格发生变化。')
                            inp.seek(seg['offset'])
                            left = seg['frames']
                            while left:
                                data = inp.read(min(left,262144),dtype='float32',always_2d=True)
                                need(len(data)>0,'离线编辑素材读取不完整。')
                                out.write(data)
                                left -= len(data)
                need(sf.info(str(tmp)).frames == total,'离线编辑重建长度不一致。')
                os.rename(tmp,dst)
                created[key] = str(dst.resolve())
            item['path'] = created[key]
    if bake_event_processing:
        for track in tracks:
            for item in track['events']:
                codes = {loss['code'] for loss in item.get('losses',[]) if loss.get('bakeable')}
                if not codes.intersection(('polarity','volume_envelope_linear')):
                    continue
                source = Path(item['path'])
                envelope = item.get('_native_volume_envelope',[]) if 'volume_envelope_linear' in codes else []
                inverted = 'polarity' in codes
                origin = item.get('_native_envelope_origin',0)
                key = sha256(json.dumps([str(source),envelope,origin,inverted],sort_keys=True).encode()).hexdigest()
                if key not in created:
                    directory.mkdir(parents=True,exist_ok=True)
                    dst = directory / f'Event_{key[:20]}_{uuid4().hex[:8]}.wav'
                    _render_event_controls(source,dst,envelope,origin,inverted)
                    created[key] = str(dst.resolve())
                item['_native_preprocessing_source'] = str(source)
                item['path'] = created[key]
                item['_native_baked_controls'] = sorted(codes.intersection(('polarity','volume_envelope_linear')))
    return list(created.values())


def _render_event_controls(source,destination,envelope,origin,inverted):
    import numpy as np
    import soundfile as sf
    temp = destination.with_suffix('.partial')
    x = [point['frame'] + origin for point in envelope]
    y = [point['amplitude'] for point in envelope]
    with sf.SoundFile(source) as src:
        frames = src.frames
        fmt = 'RF64' if frames*src.channels*4 + 128 >= 2**32 else 'WAV'
        with sf.SoundFile(str(temp),'x',format=fmt,subtype='FLOAT',samplerate=src.samplerate,channels=src.channels) as output:
            while src.tell() < frames:
                first = src.tell()
                data = src.read(262144,dtype='float32',always_2d=True)
                need(len(data)>0,'事件音频读取不完整。')
                if envelope:
                    # Constant outside the first/last knots, straight ramps in
                    # linear-amplitude coordinates on the source sample grid.
                    values = np.interp(np.arange(first,first+len(data)),x,y)
                    data = data*values[:,None]
                output.write(-data if inverted else data)
    need(sf.info(str(temp)).frames == frames,'事件处理写入长度不一致。')
    temp.rename(destination)
