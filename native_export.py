"""Publish a new project only after reading its complete editable timeline back."""
import math
import os
import tempfile
from platform_support import media_directory
from pathlib import Path
from xml.etree import ElementTree as ET

from archive_reader import Archive
from native_document import Seed, assemble, pack
from native_events import Arrangement
from native_input import NativeInput
from native_inventory import _Archive
from native_mixer import _Fields
from native_pool import objects, physical_files, saved_project_folder
from native_projects import read_header, PROJECT_TYPES
from native_score import Score, fields
from plugin_state import from_envelope, parse_preset
from project_colors import native_palette


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def update_master(data, master):
    if not master or not (master.get('gain') or master.get('plugins')):
        return data
    from plugin_xml import write_inserts
    class LocatedFields(_Fields):
        def fields(self,parent,depth=0):
            start=self.pos
            super().fields(parent,depth)
            self.positions[parent]=(start,self.pos)
    p=16
    label=b''
    while p<len(data):
        base=p+12
        end=base+int.from_bytes(data[p+4:base],'big')
        if data[p:p+4]==b'ROOT':
            label=data[base:end]
        elif data[p:p+4]==b'ARCH' and label==pack('I',7)+b'Devices'+pack('I',11)+b'FAttributes':
            raw=data[base:end]
            kind,start,stop,_=_Archive(raw,8).obj(0,len(raw))
            require(kind=='FAttributes' and stop==len(raw),'主输出数据结构未知。')
            reader=LocatedFields(raw,start,stop)
            reader.positions={}
            tree=ET.Element('devices')
            reader.fields(tree)
            ch=tree.findall('member[@name="VST Mixer"]/list[@name="Output Channels"]/item')
            require(len(ch)==1 and reader.pos==stop,'无法唯一确定模板主输出。')
            edits=[]
            if master.get('gain'):
                db=master['gain']
                require(-20<=db<=0,'新建原生工程的主输出推子超出已校准范围。')
                v=ch[0].find('member[@name="Volume"]')
                v.find('float[@name="AnchorValue"]').set('value',str(db))
                v.find('float[@name="Value"]').set('value',str(685.1803826148858*db+24516.60714534251))
                edits.append((*reader.positions[v],fields(v)))
            if master.get('plugins'):
                folder=ch[0].find('member[@name="InsertFolder"]')
                write_inserts(folder,master['plugins'])
                edits.append((*reader.positions[folder],fields(folder)))
            blob=bytearray(raw)
            for a,b,payload in sorted(edits,reverse=True):
                blob[a:b]=payload
            blob[start-8:start]=pack('Q',len(blob)-start)
            result=bytearray(data[:p+4]+pack('Q',len(blob))+blob+data[end:])
            result[4:12]=pack('Q',len(result)-16)
            return bytes(result)
        p=end
    raise ValueError('缺少模板主输出。')


def plugins(items):
    return [(int(p['slot']),p['cid'],bool(p['enabled'] and not p['bypassed']),
             {k:v for k,v in parse_preset(from_envelope(p['preset']))[1].items() if k in ('Comp','Cont')})
            for p in items]


def verify(path, destination, desired, colors, bpm, expected_media):
    require(PROJECT_TYPES[destination.suffix].encode()+b'\0' in read_header(path)[:1024], '输出宿主不匹配。')
    actual=NativeInput(path,static_only=True)
    require(not actual.errors,'原生工程回读失败：'+'；'.join(actual.errors))
    require(math.isclose(actual.bpm,bpm,rel_tol=1e-7) and actual.timing['sample_rate']==48000
            and actual.timing['frame_type']==2,'工程时间设置不一致。')
    with Arrangement(path) as a:
        require(tuple(colors)==native_palette(a),'原生工程色盘不一致。')
        require(saved_project_folder(a)==destination.parent,'工程保存位置不一致。')
        archive=bytes(a.view)
        paths={a.fnpath(s,e) for _,s,e in objects(a,archive,'FNPath')}
        require((destination.name,media_directory(destination.parent)) in paths and
                (destination.parent.name,media_directory(destination.parent.parent)) in paths,
                '工作目录或工程文件名写入后不一致。')
        assets={Path(directory)/name for name,directory,_ in physical_files(a)}
        require(assets==expected_media,'原生工程媒体池与 ACE 素材引用不一致。')
    count=0
    require(len(desired.tracks)==len(actual.tracks),'轨道数量不一致。')
    def compare(before,after,keys):
        for key in keys:
            val=before.get(key)
            if val is None:
                continue
            saved=after.get(key)
            if isinstance(val,(int,float)) and not isinstance(val,bool):
                ok=saved is not None and math.isclose(val,saved,rel_tol=1e-8,abs_tol=2e-5 if key=='gain' else 1e-8)
            else:
                ok=val==saved
            require(ok,f'{before.get("name","")} 的 {key} 写入后不一致。')
    for tr,saved in zip(desired.tracks,actual.tracks):
        raw_settings=saved
        if 'source_gain_db' in saved:
            raw_settings=dict(saved,gain=saved['source_gain_db'],fader_value=saved['source_fader_value'])
        compare(tr,raw_settings,('type','name','gain','fader_value','pan','mute','color_slot'))
        require(plugins(tr.get('plugins',[]))==plugins(saved.get('plugins',[])),'轨道插入状态不一致。')
        require(len(tr['events'])==len(saved['events']),'片段数量不一致。')
        for ev,got in zip(tr['events'],saved['events']):
            timed=got
            if tr['type']=='marker':
                # Marker coordinates are ticks; the stored tempo period is a float32.
                timed=dict(got,start=got['start']*actual.bpm/bpm,duration=got['duration']*actual.bpm/bpm)
            compare(ev,timed,('name','start','offset','duration','gain','enabled','path','marker_kind','marker_id','color_slot'))
            for side in ('fade_in','fade_out'):
                if side in ev:
                    compare(ev[side],got[side],('length','native'))
                    require(len(ev[side]['points'])==len(got[side]['points']) and
                            all(math.isclose(x,y,abs_tol=1e-9) for p,q in zip(ev[side]['points'],got[side]['points'])
                                for x,y in zip(p,q)), '淡化曲线点不一致。')
            count+=1
    if desired.master:
        compare(desired.master,actual.master,('gain',))
        require(plugins(desired.master['plugins'])==plugins(actual.master['plugins']),'主输出插入状态不一致。')
    return dict(matched_saved_tracks=len(actual.tracks),matched_saved_events=count,
                native_external_media=len(assets),native_write_mode='new_arrangement',native_palette_entries=len(colors))


def create_project(xml, destination, report):
    seed=Seed()
    bpm=report['source_bpm']
    timing=dict(bpm=bpm,numerator=4,denominator=4,sample_rate=48000,frame_type=2)
    expected=Archive(xml,native_timing=timing,native_output=True)
    require(not expected.errors,'轨道数据校验失败：'+'；'.join(expected.errors))
    score=Score(xml,seed)
    media={key[1] for key in score.clips}
    raw=assemble(destination,score.tracks,score.media,score.duration,bpm,report['palette'],seed)
    raw=update_master(raw,expected.master)
    fd,name=tempfile.mkstemp(prefix='.ACE-native-',suffix=destination.suffix,dir=destination.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        result=verify(temp,destination,expected,report['palette'],bpm,media)
        if os.name=='nt':
            temp.rename(destination)
        else:
            os.link(temp,destination)
    finally:
        temp.unlink(missing_ok=True)
    return result

