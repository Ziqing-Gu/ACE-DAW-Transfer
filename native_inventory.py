"""Read the saved Steinberg arrangement track tree for transfer completeness checks.

Works on measured big-endian NUND RIFF (32-bit offsets) and RIF2 (64-bit).
Media and insert payloads are skipped by object size; neither is searched.
"""
import math
import mmap
import struct
from pathlib import Path


class NativeInventoryError(ValueError):
    """The saved arrangement cannot be inventoried with this reader."""


MEDIA_CLASSES = frozenset(('MAudioTrackEvent', 'MVideoTrackEvent', 'MMarkerTrackEvent'))
FOLDERS = frozenset(('MFolderTrack', 'MFolderTrackEvent'))


class _Archive:
    def __init__(self, view, width, timing=False, counts=False):
        self.view = view
        self.width = width
        self.ref_bit = 1 << (width * 8 - 1)
        self.declare = self.ref_bit * 2 - 1
        self.result = []
        self.want_timing = timing
        self.want_counts = counts
        self.counts = dict(audio_events=0, video_events=0, range_markers=0)
        self.tempo = None
        self.meter = None
        self.tempo_objects = set()
        self.meter_objects = set()

    def uint(self, pos, width, end):
        if pos < 0 or pos + width > end:
            raise NativeInventoryError('原生工程的字段超出所属对象。')
        return int.from_bytes(self.view[pos:pos + width], 'big')

    def typename(self, pos, end):
        length = self.uint(pos, 4, end)
        start = pos + 4
        if not 2 <= length <= 256 or start + length + 2 > end:
            raise NativeInventoryError('原生工程类名长度异常。')
        data = bytes(self.view[start:start + length])
        if data[-1:] != b'\0' or not data[:-1].isascii() or not all(32 <= byte <= 126 for byte in data[:-1]):
            raise NativeInventoryError('原生工程类名无效。')
        return data[:-1].decode('ascii'), start + length + 2

    def referenced_type(self, size_pos, seen_at):
        # Object references address an earlier object's size field. Resolve its
        # attached descriptor only; never search arbitrarily for track names.
        w = self.width
        if not w <= size_pos < seen_at:
            raise NativeInventoryError('原生工程对象引用越界。')
        tag = self.uint(size_pos - w, w, size_pos)
        if self.ref_bit <= tag < self.declare - 1:
            return self.class_ref(tag, size_pos - w)
        # A concrete class declaration ends exactly at this size field.
        candidates = []
        for length in range(2, min(256, size_pos - w - 6) + 1):
            at = size_pos - 2 - length - 4 - w
            if (self.uint(at, w, size_pos) == self.declare
                    and self.uint(at + w, 4, size_pos) == length):
                name, stop = self.typename(at + w, size_pos)
                if stop == size_pos:
                    candidates.append(name)
        if len(candidates) != 1:
            raise NativeInventoryError('无法唯一识别原生工程对象引用的类型。')
        return candidates[0]

    def class_ref(self, tag, seen_at):
        at = tag - self.ref_bit
        if at < 0 or at + self.width > seen_at:
            raise NativeInventoryError('原生工程类引用越界。')
        if self.uint(at, self.width, seen_at) not in (self.declare, self.declare - 1):
            raise NativeInventoryError('原生工程类引用缺少类声明。')
        return self.typename(at + self.width, seen_at)[0]

    def obj(self, pos, limit):
        """Return concrete type, payload start/end, next sibling position."""
        w = self.width
        for _ in range(64):
            start = pos
            tag = self.uint(pos, w, limit)
            pos += w
            if tag in (self.declare, self.declare - 1):
                name, pos = self.typename(pos, limit)
                if tag == self.declare - 1:
                    continue
            elif tag & self.ref_bit:
                name = self.class_ref(tag, start)
            else:
                name = self.referenced_type(tag, start)
                size = self.uint(tag, w, start)
                end = tag + w + size
                if end > start:
                    raise NativeInventoryError('原生工程对象引用未指向已保存对象。')
                return name, tag + w, end, pos
            size = self.uint(pos, w, limit)
            base = pos + w
            if base + size > limit:
                raise NativeInventoryError('原生工程对象长度越界。')
            return name, base, base + size, base + size
        raise NativeInventoryError('原生工程类层级异常。')

    def node(self, body, limit):
        # MEvent's common serialized start/length/flags precede its data node.
        kind, body, end, _ = self.obj(body + 26, limit)
        if kind not in ('MListNode', 'MDataNode', 'MTrackList'):
            raise NativeInventoryError('原生轨道使用了尚未识别的数据节点：' + kind)
        length = self.uint(body, 4, end)
        at = body + 4
        if length > 1048576 or at + length > end:
            raise NativeInventoryError('原生轨道名称超出所属节点。')
        raw = bytes(self.view[at:at + length])
        if raw.endswith(b'\x00\xef\xbb\xbf'):
            raw = raw[:-4]
        elif raw.endswith(b'\x00'):
            raw = raw[:-1]
        if b'\x00' in raw:
            raise NativeInventoryError('原生轨道名称编码无法识别。')
        return kind, raw.decode('utf-8'), at + length, end

    def read_tempo(self, start, end):
        if start in self.tempo_objects:
            return
        self.tempo_objects.add(start)
        count = self.uint(start, 4, end)
        after = start + 4 + count * 22
        if not count or count > 1000000 or after + 6 > end:
            raise NativeInventoryError('速度表长度无法确认。')
        fixed = struct.unpack_from('>f', self.view, after)[0]
        mode = self.uint(after + 4, 2, end)
        if mode == 1:
            bpm = fixed
        elif mode == 0:
            # Each tempo point stores a float32 period (seconds per quarter).
            period = struct.unpack_from('>f', self.view, start + 4)[0]
            if not math.isfinite(period) or period <= 0:
                raise NativeInventoryError('速度轨节拍周期无效。')
            for i in range(1, count):
                if struct.unpack_from('>f', self.view, start + 4 + i * 22)[0] != period:
                    raise NativeInventoryError('原生工程使用变速，当前无法完整迁移。')
            bpm = 60.0 / period
        else:
            raise NativeInventoryError('无法确定原生工程当前使用哪份速度表。')
        if not math.isfinite(bpm) or not 1 <= bpm <= 1000:
            raise NativeInventoryError('原生工程速度超出支持范围。')
        if self.tempo is not None and not math.isclose(bpm, self.tempo, rel_tol=1e-7):
            raise NativeInventoryError('工程中存在多个不同速度域。')
        self.tempo = bpm

    def read_meter(self, start, end):
        if start in self.meter_objects:
            return
        self.meter_objects.add(start)
        count = self.uint(start, 4, end)
        if not count or count > 1000000 or start + 4 + count * 16 > end:
            raise NativeInventoryError('拍号表长度无法确认。')
        signatures = {struct.unpack_from('>HH', self.view, start + 8 + i * 16)
                      for i in range(count)}
        if len(signatures) != 1:
            raise NativeInventoryError('原生工程使用变拍，当前无法完整迁移。')
        meter = signatures.pop()
        if not all(meter) or (self.meter is not None and self.meter != meter):
            raise NativeInventoryError('工程拍号域不一致。')
        self.meter = meter

    def time_domain(self, attrs, end):
        if self.uint(attrs, 4, end) != 0:
            return
        kind, start, stop, pos = self.obj(attrs + 4, end)
        if kind != 'MTempoTrackEvent':
            raise NativeInventoryError('无法识别轨道的速度对象。')
        self.read_tempo(start, stop)
        kind, start, stop, _ = self.obj(pos, end)
        if kind != 'MSignatureTrackEvent':
            raise NativeInventoryError('无法识别轨道的拍号对象。')
        self.read_meter(start, stop)

    def count_events(self, track_class, attrs, end):
        domain = self.uint(attrs, 4, end)
        pos = attrs + 4
        if domain == 0:
            for expected in ('MTempoTrackEvent', 'MSignatureTrackEvent'):
                kind, _, _, pos = self.obj(pos, end)
                if kind != expected:
                    raise NativeInventoryError('无法识别轨道事件时间域。')
        elif domain == 1:
            pos += 8
        else:
            raise NativeInventoryError('轨道事件使用了尚未识别的时间域。')
        count = self.uint(pos, 4, end)
        pos += 4
        if count > (end - pos) // self.width:
            raise NativeInventoryError('轨道事件数量超出所属列表。')
        expected, field = {'MAudioTrackEvent': ('MAudioEvent', 'audio_events'),
                           'MVideoTrackEvent': ('MVideoEvent', 'video_events'),
                           'MMarkerTrackEvent': ('MRangeMarkerEvent', 'range_markers')}[track_class]
        for _ in range(count):
            kind, _, _, pos = self.obj(pos, end)
            if kind != expected:
                raise NativeInventoryError('轨道包含未支持的事件类型：' + kind)
        if pos != end:
            raise NativeInventoryError('轨道事件列表未完整解析。')
        self.counts[field] += count

    def tracks(self, body, end, depth=0):
        if depth >= 128:
            raise NativeInventoryError('原生工程轨道文件夹层级异常。')
        kind, _, attrs, list_end = self.node(body, end)
        if kind != 'MTrackList' or self.uint(attrs, 4, list_end) != 1:
            raise NativeInventoryError('原生工程轨道列表使用了尚未识别的时间域。')
        n = self.uint(attrs + 14, 4, list_end)
        p = attrs + 18
        if n > min(100000, (list_end - p) // self.width):
            raise NativeInventoryError('原生工程轨道数量超出列表。')
        for _ in range(n):
            kind, start, stop, p = self.obj(p, list_end)
            if len(self.result) >= 100000:
                raise NativeInventoryError('原生工程轨道数量过多。')
            if kind in ('MTempoTrackEvent', 'MSignatureTrackEvent'):
                if self.want_timing:
                    if kind == 'MTempoTrackEvent':
                        self.read_tempo(start, stop)
                    else:
                        self.read_meter(start, stop)
                self.result.append((kind, ''))
                continue
            if kind in MEDIA_CLASSES or kind in FOLDERS or kind == 'MDeviceTrackEvent':
                _, name, attrs, node_end = self.node(start, stop)
                if self.want_timing:
                    self.time_domain(attrs, node_end)
                if self.want_counts and kind in MEDIA_CLASSES:
                    self.count_events(kind, attrs, node_end)
            else:
                # Non-transferable track classes can use a different payload.
                # The type is known even when a display name cannot be decoded.
                name = ''
                try:
                    _, name, _, _ = self.node(start, stop)
                except (NativeInventoryError, UnicodeError):
                    pass
            self.result.append((kind, name))
            if kind in FOLDERS:
                self.tracks(start, stop, depth + 1)
        if p != list_end:
            raise NativeInventoryError('原生工程轨道列表存在未识别数据，无法确认完整性。')

    def project_clock_footer(self, begin, end):
        # Linear-time projects can store their only tempo map after the track
        # tree. Accept a footer candidate only when its entire object sequence
        # and track-ID table terminate exactly at the declared MRoot boundary.
        begin = max(begin, end - 4 * 1024 * 1024)
        region = bytes(self.view[begin:end])
        key = b'\xff' * self.width + struct.pack('>I', 17) + b'MTempoTrackEvent\0'
        pos = region.find(key)
        matches = []
        while pos >= 0:
            at = begin + pos
            try:
                k, tb, te, at = self.obj(at, end)
                s, sb, se, at = self.obj(at, end)
                fade, _, _, at = self.obj(at, end)
                count = self.uint(at, 2, end)
                at += 2 + count * (2 + self.width)
                loud, _, _, at = self.obj(at, end)
                if (k, s, fade, loud, at) == ('MTempoTrackEvent', 'MSignatureTrackEvent',
                                              'MAutoFadeSetting', 'MLoudnessTrackEvent', end):
                    matches.append((tb, te, sb, se))
            except NativeInventoryError:
                pass
            pos = region.find(key, pos + len(key))
        if len(matches) == 1:
            tb, te, sb, se = matches[0]
            self.read_tempo(tb, te)
            self.read_meter(sb, se)

    def inventory(self):
        kind, body, end, next_pos = self.obj(0, len(self.view))
        if kind != 'PArrangement' or next_pos != len(self.view):
            raise NativeInventoryError('原生工程的编排对象无效。')
        arr_end = end
        kind, body, end, setup_at = self.obj(body, arr_end)
        if kind != 'MRoot':
            raise NativeInventoryError('原生工程编排根对象无效。')
        self.tracks(body, end)
        if self.want_counts:
            return self.counts
        if not self.want_timing:
            return self.result
        kind, start, stop, _ = self.obj(setup_at, arr_end)
        if kind != 'PArrangeSetup' or start + 52 > stop:
            raise NativeInventoryError('无法读取原生工程设置。')
        if self.uint(start + 8, 4, stop) != 1 or self.uint(start + 28, 4, stop) != 1:
            raise NativeInventoryError('工程设置使用了尚未识别的时间域。')
        if self.tempo is None or self.meter is None:
            _, _, _, list_end = self.obj(body + 26, end)
            self.project_clock_footer(list_end, end)
        if self.tempo is None or self.meter is None:
            raise NativeInventoryError('原生工程缺少可确认的速度或拍号。')
        rate = struct.unpack_from('>f', self.view, start + 48)[0]
        if not math.isfinite(rate) or rate <= 0:
            raise NativeInventoryError('原生工程采样率无效。')
        return dict(bpm=self.tempo, numerator=self.meter[0], denominator=self.meter[1],
                    frame_type=self.uint(start + 40, 4, stop), sample_rate=rate)


def _read_project(project, timing=False, counts=False):
    """Walk bounded container chunks and select the arrangement archive."""
    with Path(project).open('rb') as handle:
        if handle.seek(0, 2) < 16:
            raise NativeInventoryError('原生工程文件过短。')
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            width = {b'RIFF': 4, b'RIF2': 8}.get(data[:4])
            if width is None or data[4 + width:8 + width] != b'NUND':
                raise NativeInventoryError('文件不是已确认的 Cubase/Nuendo 原生工程。')
            if int.from_bytes(data[4:4 + width], 'big') + width + 8 != len(data):
                raise NativeInventoryError('原生工程文件长度不一致。')
            p = width + 8
            found = None
            parent = b''
            while p < len(data):
                base = p + 4 + width
                if base > len(data):
                    raise NativeInventoryError('原生工程分块头部不完整。')
                kind = data[p:p + 4]
                end = base + int.from_bytes(data[p + 4:base], 'big')
                if end > len(data):
                    raise NativeInventoryError('原生工程分块超出文件。')
                if kind == b'ROOT':
                    if end - base > 2048:
                        raise NativeInventoryError('无法识别的原生工程根标签。')
                    parent = data[base:end]
                elif kind == b'ARCH':
                    if parent.endswith(struct.pack('>I', 12) + b'PArrangement'):
                        if found is not None:
                            raise NativeInventoryError('原生工程含多个编排，无法确定迁移范围。')
                        with memoryview(data)[base:end] as view:
                            found = _Archive(view, width, timing=timing, counts=counts).inventory()
                    parent = b''
                p = end
            if found is None:
                raise NativeInventoryError('原生工程缺少轨道编排。')
            return found


def read_track_inventory(project: Path) -> list[tuple[str, str]]:
    """Return native class and display name in depth-first order, including I/O."""
    return _read_project(project, timing=False)


def read_project_timing(project: Path) -> dict:
    """Read effective fixed BPM, constant meter, frame type and sample rate.

    Reject active tempo or time-signature changes. An inactive tempo map does
    not override the project's fixed tempo when the fixed-tempo flag is set.
    """
    return _read_project(project, timing=True)


def read_event_counts(project: Path) -> dict[str, int]:
    """Count inline audio/video/range-marker events in arrangement track lists.

    Reject other event types on these tracks, e.g. a MIDI/Audio Part, instead
    of counting nested parts, pool objects, alternate takes or point markers.
    """
    return _read_project(project, counts=True)
