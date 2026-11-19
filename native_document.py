"""Emit the typed RIF2 object stream for an editable Cubase/Nuendo 14 document."""
import bisect
import hashlib
import json
import struct
from platform_support import media_directory
from pathlib import Path

BASE = Path(__file__).parent / 'templates'
NULL = bytes(8)
REF = 1 << 63


def pack(fmt, *values):
    return struct.pack('>' + fmt, *values)


def string(value, wide=True):
    data = value.encode('utf-8') + (b'\0\xef\xbb\xbf' if wide else b'\0') if value else b''
    return pack('I', len(data)) + data


class Node:
    def __init__(self, kind, *parts):
        self.kind = kind
        self.parts = list(parts)


class Abstract:
    def __init__(self, kind):
        self.kind = kind


VERSIONS = {'MFade':3, 'MFadeIn':0, 'MFadeOut':0, 'MMarkerEvent':0,
            'MRangeMarkerEvent':0, 'MAudioTrackEvent':1, 'MAudioEvent':6,
            'PAudioClip':4, 'PMediaDescriptor':1, 'AudioStream':0, 'AudioCluster':0,
            'AudioFile':1, 'AClusterSegment':0, 'VolumeCurveDataNode':0,
            'MVideoTrackEvent':0, 'MVideoEvent':0, 'PVideoClip':0, 'VideoFile':2,
            'MAudioTrack':0, 'MVideoTrack':0}
PARENTS = {'PAudioClip':['PMediaDescriptor'], 'PVideoClip':['PMediaDescriptor'],
           'FNPath':['FPath'], 'AudioCluster':['AudioStream'], 'MFadeIn':['MFade'],
           'MFadeOut':['MFade'], 'MLinearInterpolator':['MInterpolator'],
           'MRangeMarkerEvent':['MMarkerEvent']}


class Encoder:
    def __init__(self, versions):
        self.versions = versions | VERSIONS
        self.buffer = bytearray()
        self.classes = {}
        self.objects = {}

    def descriptor(self, kind, abstract):
        if kind in self.classes:
            if not abstract:
                self.buffer.extend(pack('Q', REF + self.classes[kind]))
            return
        self.classes[kind] = len(self.buffer)
        self.buffer.extend(pack('q', -2 if abstract else -1) + string(kind, False)
                           + pack('H', self.versions[kind]))

    def emit(self, part):
        if isinstance(part, bytes):
            self.buffer.extend(part)
        elif isinstance(part, Abstract):
            self.descriptor(part.kind, True)
        elif part in self.objects:
            self.buffer.extend(pack('Q', self.objects[part]))
        else:
            for parent in PARENTS.get(part.kind, ()):
                self.descriptor(parent, True)
            self.descriptor(part.kind, False)
            at = len(self.buffer)
            self.objects[part] = at
            self.buffer.extend(NULL)
            for child in part.parts:
                self.emit(child)
            struct.pack_into('>Q', self.buffer, at, len(self.buffer) - at - 8)


class Seed:
    """Read the frozen, empty arrangement into independent objects and references.

    The index lists only structural fields. Device state and binary preferences
    stay opaque. A changed template must never be interpreted with this index.
    """
    def __init__(self):
        self.file = (BASE/'ACE-Empty-14.npr').read_bytes()
        self.index = json.loads((BASE/'native-14-layout.json').read_text(encoding='utf8'))
        if hashlib.sha256(self.file).hexdigest() != self.index['sha256']:
            raise ValueError('原生工程模板校验失败。')
        self.offset = 261
        self.data = self.file[self.offset:self.offset+49751]
        self.headers = {int(i):v for i,v in self.index['headers'].items()}
        self.abstract = {int(i):v for i,v in self.index['abstract'].items()}
        self.refs = {int(i):v for i,v in self.index['refs'].items()}
        self.nodes = {size:Node(kind) for kind,size,end in self.headers.values()}
        self.edits = {}
        self.append = {}
        self.replacements = {}

    def parts(self, start, end, positions):
        p = start
        while p < end:
            if p in self.edits:
                stop, values = self.edits[p]
                if stop > end:
                    raise ValueError('模板字段跨越对象边界。')
                yield from values
                p = stop
            elif p in self.headers:
                kind, size, stop = self.headers[p]
                if stop > end:
                    raise ValueError('模板对象边界无效。')
                obj = self.nodes[size]
                if size in self.replacements:
                    yield self.replacements[size]
                else:
                    obj.parts = list(self.parts(size+8, stop, positions))
                    obj.parts.extend(self.append.get(size, ()))
                    yield obj
                p = stop
            elif p in self.abstract:
                kind,p = self.abstract[p]
                yield Abstract(kind)
            elif p in self.refs:
                yield self.nodes[self.refs[p]]
                p += 8
            else:
                i = bisect.bisect_right(positions,p)
                stop = min(end, positions[i]) if i<len(positions) else end
                yield self.data[p:stop]
                p = stop

    def render(self):
        pos = sorted(set(self.headers) | set(self.abstract) | set(self.refs) | set(self.edits))
        writer = Encoder(self.index['versions'])
        for part in self.parts(0,len(self.data),pos):
            writer.emit(part)
        return bytes(writer.buffer)

    def document(self, destination, arrangement):
        out = bytearray(self.file[:self.offset-8])
        if destination.suffix.lower()=='.cpr':
            out[66:205] = bytes.fromhex(self.index['cubase_version'])
        out.extend(pack('Q', len(arrangement)) + arrangement + self.file[self.offset+len(self.data):])
        struct.pack_into('>Q',out,4,len(out)-16)
        return bytes(out)


def file_path(path, category):
    extension = path.suffix.lower().lstrip('.')
    info = {'audio':(b'WAVE',extension,extension,'Wave 文件'),
            'video':(bytes(4),extension,extension,extension.upper()+' 视频文件'),
            'document':(b'NDPR',extension,'','Cubase 工程文件' if extension=='cpr' else 'Nuendo 工程文件'),
            'folder':(bytes(4),'','','')}
    magic,default,filter_ext,title = info[category]
    folder = media_directory(path.parent)
    return Node('FNPath', string(path.name) + magic + string(default) + string(filter_ext) +
                string(title) + (pack('IH',1,1) if category=='folder' else pack('IH',1,0)) + string(folder))


def palette_data(colors):
    table = pack('I',len(colors)) + b''.join(string('Color '+str(i+1)) +
            bytes.fromhex('ff'+c[1:]) for i,c in enumerate(colors))
    return string('Event Colors') + table + table + bytes(2)


def assemble(destination, tracks, media, duration, bpm, colors, seed=None):
    seed = seed or Seed()
    def field(at, size, value):
        seed.edits[at] = (at+size,[value])
    field(330,4,pack('I',len(tracks)+1))
    seed.append[304] = tracks
    for at in (215,49562):
        field(at,8,pack('d',duration))
    field(623,4,pack('f',60/bpm))
    field(645,4,pack('f',bpm))
    seed.replacements[48020] = file_path(destination.parent,'folder')
    seed.replacements[48447] = file_path(destination,'document')
    if colors:
        seed.replacements[32868] = Node('UColorSet', palette_data(colors))
    serial = 5
    for category, slot in [('audio',48227),('video',48326)]:
        entries=[]
        for kind,name,clip in media:
            if kind==category:
                entries.append(Node('GTreeEntry',clip,pack('HI',0x263,0)+string(name)+pack('I',serial)))
                serial += 1
        seed.edits[slot+10] = (slot+14,[pack('I',len(entries)),*entries])
    markers=[t for t in tracks if t.kind=='MMarkerTrackEvent']
    if markers:
        seed.replacements[48725]=markers[0]
    # Track IDs in this table address actual event objects. ID 1 is the original
    # I/O folder. The empty host template has never allocated an ID to Stereo Out.
    seed.edits[49318] = (49330,[pack('H',2+len(tracks)),pack('H',1),seed.nodes[387],
                                pack('H',2),seed.nodes[508],
                                *(v for i,t in enumerate(tracks,3) for v in (pack('H',i),t))])
    if destination.suffix.lower()=='.cpr':
        # Cubase writes a null auxiliary track where Nuendo stores a loudness track.
        seed.edits[49330]=(49506,[NULL])
    return seed.document(destination,seed.render())

