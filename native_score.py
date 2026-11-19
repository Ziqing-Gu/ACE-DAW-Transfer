"""Construct audio, video, markers, and channel objects from the generated track archive."""
from pathlib import Path
from xml.etree import ElementTree as ET

from archive_reader import value, number
from native_document import Node, NULL, file_path, pack, string
from native_xml import wavinfo


def fields(parent):
    """Typed mixer attributes, including nested VST3 state, in the host's field order."""
    def object_value(el):
        return string(el.attrib['class'],False)+pack('Q',int(el.attrib['ID']))+fields(el)
    def item(el, subtype):
        if subtype=='obj':
            return pack('H',20)+object_value(el)
        if subtype=='list':
            return fields(el)
        if subtype=='string':
            return string(el.attrib['value'],el.get('wide')=='true')
        return pack('q',int(el.attrib['value'])) if subtype=='int' else pack('d',float(el.attrib['value']))
    data = bytearray(pack('I',len(parent)))
    for el in parent:
        data.extend(string(el.attrib['name'],False))
        if el.tag=='int':
            raw=pack('Hq',1,int(el.attrib['value']))
        elif el.tag=='float':
            raw=pack('Hd',4,float(el.attrib['value']))
        elif el.tag=='string':
            raw=pack('H',8)+string(el.attrib['value'],el.get('wide')=='true')
        elif el.tag=='bin':
            blob=bytes.fromhex(el.text or '')
            raw=pack('HHI',2,7,len(blob))+blob
        elif el.tag=='member':
            raw=pack('HH',2,6)+fields(el)
        elif el.tag=='obj':
            raw=pack('HH',2,20)+object_value(el)
        elif el.tag=='list':
            typ=el.attrib['type']
            code={'string':4,'int':2,'float':3,'list':5,'obj':201}[typ]
            raw=pack('HHI',2,code,len(el))+b''.join(item(x,typ) for x in el)
        else:
            raise ValueError('未知原生混音属性 '+el.tag)
        data.extend(raw)
    return bytes(data)


def geometry(flags, start, length, offset=0):
    return pack('Hddd',int(flags),start,length,offset)


def domain(mode, period):
    return pack('Id',mode,period)


def color_attr(el):
    c=el.find('member[@name="Additional Attributes"]/int[@name="Farb"]')
    return pack('I',0) if c is None else pack('I4sHq',1,b'braF',1,int(c.attrib['value']))


def automation(duration):
    device=Node('MAutomationTrack',bytes.fromhex('0000000000020000000000000000'))
    event=Node('MAutomationTrackEvent',geometry(0,0,duration),
               Node('MAutoListNode',string('')+domain(1,1)+pack('I',0)),
               pack('I',0),device,bytes(20)+pack('I',1025)+bytes(6))
    return Node('MAutomationNode',string('Automation')+domain(1,1)+pack('HI',0,1),event,device,pack('H',0))


def fade(el,side):
    curve=el.find('obj[@name="'+side+'"]/obj[@name="Curve"]')
    if curve is None:
        return NULL
    points=curve.find('list[@name="Points"]')
    payload=pack('I',len(points))+b''.join(pack('dd',number(p,'X'),number(p,'Y')) for p in points)
    payload+=pack('dddd',*(number(curve,k) for k in ('XMin','XMax','YMin','YMax')))
    return Node('M'+side,Node('MLinearInterpolator',payload),pack('d',0.5))


class Score:
    def __init__(self, xml, seed):
        self.tracks=[]
        self.media=[]
        self.clips={}
        self.seed=seed
        root=ET.parse(xml).getroot()
        self.duration=number(root.find('obj[@name="Setup"]/member[@name="Length"]'),'Time')
        for track in root.find('list[@name="track"]'):
            self.tracks.append(self.track(track))

    def source(self, n, typ):
        path_node=n.find('obj[@name="Path"]')
        path=Path(value(path_node,'Path')) / value(path_node,'Name')
        name=value(n,'Name',path.stem)
        key=(typ,path,name)
        if key in self.clips:
            return self.clips[key]
        fn=file_path(path,typ)
        if typ=='audio':
            _,frames,bits,frame_size,_,rate,channels=wavinfo(path)
            audio=Node('AudioFile',pack('QHHHf',frames,bits,frame_size,channels,rate),fn,bytes(8))
            segment=Node('AClusterSegment',audio,pack('QQQ',0,frames,0))
            cluster=Node('AudioCluster',fn,pack('HI',2,1),audio,pack('HI',1,1),segment)
            clip=Node('PAudioClip',string(name)+domain(10,1/rate)+pack('I',0),fn,
                      string('')+bytes.fromhex('0000000000030000000000000000')+pack('I',0),
                      cluster,pack('HI',1,0))
        else:
            clip=Node('PVideoClip',string(name)+domain(1,1)+pack('I',0),fn,
                      bytes(22),Node('VideoFile',fn,bytes(24)))
        self.clips[key]=clip
        self.media.append((typ,name,clip))
        return clip

    def event(self, el):
        kind=el.attrib['class']
        g=geometry(number(el,'Flags'),number(el,'Start'),number(el,'Length'),number(el,'Offset'))
        if kind=='MAudioEvent':
            src=el.find('obj[@name="AudioClip"]')
            clip=self.source(src,'audio')
            rate=number(src.find('.//obj[@class="AudioFile"]'),'Rate')
            envelope=Node('VolumeCurveDataNode',string('')+domain(10,1/rate)+pack('I',0))
            return Node(kind,g,clip,color_attr(el)+pack('i',-1),fade(el,'FadeIn'),fade(el,'FadeOut'),
                        NULL+string(value(el,'Description',''))+
                        pack('If',0,number(el,'Volume',1))+bytes(6),envelope)
        if kind=='MVideoEvent':
            return Node(kind,g,self.source(el.find('obj[@name="Node"]'),'video'),color_attr(el))
        if kind in ('MMarkerEvent','MRangeMarkerEvent'):
            return Node(kind,string(value(el,'Name',''))+pack('I',int(number(el,'ID')))+g+NULL+color_attr(el))
        raise ValueError('无法写入此类原生事件 '+kind)

    def track(self, el):
        kind=el.attrib['class']
        node=el.find('obj[@name="Node"]')
        items=[self.event(e) for e in node.find('list[@name="Events"]')]
        timing=([pack('I',0),self.seed.nodes[611],self.seed.nodes[708]]
                if kind=='MMarkerTrackEvent' else [domain(1,1)])
        listing=Node('MListNode',string(value(node,'Name','')),*timing,pack('I',len(items)),*items)
        if kind=='MAudioTrackEvent':
            dev=el.find('obj[@name="Track Device"]')
            attrs=dev.find('member[@name="DeviceAttributes"]')
            device=Node('MAudioTrack',pack('H',1)+string('VST Multitrack',False)+pack('Ii',1,-88888)+
                        fields(attrs)+pack('H',32 | int(number(dev,'Flags'))))
            tail=[pack('IQ',46,0),automation(self.duration),NULL]
        elif kind in ('MMarkerTrackEvent','MVideoTrackEvent'):
            device=Node('MTrack' if kind=='MMarkerTrackEvent' else 'MVideoTrack',pack('HQ',2,0))
            tail=[pack('IQQ',46,0,0)]
        else:
            raise ValueError('无法写入此类原生轨道 '+kind)
        return Node(kind,geometry(0,0,number(el,'Length')),listing,color_attr(el),device,*tail)

