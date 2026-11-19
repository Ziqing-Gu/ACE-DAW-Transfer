"""Read the typed MAudioTrack mixer archive of saved Cubase/Nuendo projects."""
from functools import lru_cache
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from archive_reader import PROCESSING, TEMPLATES, fingerprint, value
from plugin_xml import read_inserts


class MixerFormatError(ValueError):
    pass


class _Fields:
    def __init__(self, data, start, end):
        self.data, self.pos, self.end = data, start, end

    def take(self, n):
        if n < 0 or self.pos + n > self.end:
            raise MixerFormatError('调音台属性越界。')
        p = self.pos
        self.pos += n
        return self.data[p:p+n]

    def num(self, width, signed=False):
        return int.from_bytes(self.take(width),'big',signed=signed)

    def text(self):
        length = self.num(4)
        if length > 8*1024*1024:
            raise MixerFormatError('调音台字符串超出上限。')
        raw = bytes(self.take(length))
        wide = raw.endswith(b'\0\xef\xbb\xbf')
        raw = raw[:-4] if wide else raw.removesuffix(b'\0')
        return raw.decode('utf8'),wide

    def count(self):
        n = self.num(4)
        if n > 1000000 or n > self.end-self.pos:
            raise MixerFormatError('调音台属性数量无效。')
        return n

    def object(self, parent, depth, name=None):
        cls,_ = self.text()
        identifier = self.num(8)
        obj = ET.SubElement(parent,'obj',{'class':cls,'ID':str(identifier)})
        if name is not None: obj.set('name',name)
        self.fields(obj,depth+1)

    def fields(self, parent, depth=0):
        if depth > 64:
            raise MixerFormatError('调音台属性嵌套过深。')
        for _ in range(self.count()):
            name,_ = self.text()
            tag = self.num(2)
            if tag == 1:
                ET.SubElement(parent,'int',name=name,value=str(self.num(8,True)))
            elif tag == 4:
                v = struct.unpack('>d',self.take(8))[0]
                if not math.isfinite(v):
                    raise MixerFormatError('非有限浮点属性：'+name)
                ET.SubElement(parent,'float',name=name,value=format(v,'.50g'))
            elif tag == 8:
                v,wide = self.text()
                node = ET.SubElement(parent,'string',name=name,value=v)
                if wide or not v: node.set('wide','true')
            elif tag == 2:
                typ=self.num(2)
                if typ == 6:
                    self.fields(ET.SubElement(parent,'member',name=name),depth+1)
                elif typ == 7:
                    ET.SubElement(parent,'bin',name=name).text = bytes(self.take(self.num(4))).hex().upper()
                elif typ == 20:
                    self.object(parent,depth,name)
                elif typ == 201:
                    dest=ET.SubElement(parent,'list',name=name,type='obj')
                    for _ in range(self.count()):
                        if self.num(2)!=20:
                            raise MixerFormatError('未识别嵌套属性对象。')
                        self.object(dest,depth+1)
                else:
                    list_types={4:'string',2:'int',3:'float',5:'list'}
                    if typ not in list_types:
                        raise MixerFormatError(f'{name}：未知列表类型 {typ}。')
                    dest=ET.SubElement(parent,'list',name=name,type=list_types[typ])
                    for _ in range(self.count()):
                        item=ET.SubElement(dest,'item')
                        if typ==5: self.fields(item,depth+1)
                        elif typ==2: item.set('value',str(self.num(8,True)))
                        elif typ==3: item.set('value',format(struct.unpack('>d',self.take(8))[0],'.50g'))
                        else:
                            v,wide=self.text()
                            item.set('value',v)
            else:
                raise MixerFormatError(f'{name}：未知属性类型 {tag}。')


def _device(archive, start, end, expected='MAudioTrack'):
    # Device Name is an explicitly length-delimited field of the class. Verify
    # both the parent type and its full size; a string in plugin data is not a
    # channel. Accept only a unique bounded MAudioTrack with this device name.
    signature = struct.pack('>HI',1,15)+b'VST Multitrack\0'
    data, width = archive.view, archive.width
    region = bytes(data[start:end])
    pos = region.find(signature)
    devices = []
    while pos >= 0:
        body = start+pos
        try:
            kind = archive.referenced_type(body-width,body)
            stop = body + archive.uint(body-width,width,end)
            if kind==expected and stop <= end:
                devices.append((body,stop))
        except (ValueError,IndexError):
            pass
        pos = region.find(signature,pos+1)
    if len(devices)!=1:
        raise MixerFormatError('无法唯一定位音轨的原生通道。')
    body,stop = devices[0]
    f = _Fields(data,body+len(signature)+4,stop)
    f.take(4)                       # Host runtime object reference
    attrs=ET.Element('member',name='DeviceAttributes')
    f.fields(attrs)
    flags=f.num(2) if expected=='MAudioTrack' else 0
    if f.pos!=stop:
        raise MixerFormatError('调音台对象尾部含未识别的数据。')
    return attrs,flags,stop


def _trackinfo(arc,start,end):
    _,name,_,ne=arc.node(start,end)
    attrs,flags,stop=_device(arc,ne,end)
    return name,attrs,flags,stop


def _automation_count(ctx, after_device, track_end):
    arc=ctx.archive
    # A Track Device is followed by routing metadata and its automation node.
    offset=after_device+4+ctx.width
    kind, start, end, after=arc.obj(offset,track_end)
    if kind!='MAutomationNode' or after>track_end:
        raise MixerFormatError('无法识别自动化数据节点。')
    _, pos=ctx.text(start,end)
    _,_,pos=ctx.domain(pos,end)
    count=arc.uint(pos+2,4,end)
    pos+=6
    if count>min(1000000,(end-pos)//ctx.width):
        raise MixerFormatError('自动化轨道数量异常。')
    total=0
    for _ in range(count):
        kind,base,limit,pos=arc.obj(pos,end)
        if kind!='MAutomationTrackEvent':
            raise MixerFormatError('未识别的自动化轨：'+kind)
        kind,values,values_end,_=arc.obj(base+26,limit)
        if kind!='MAutoListNode':
            raise MixerFormatError('无法读取自动化事件列表。')
        _,pp=ctx.text(values,values_end)
        _,_,pp=ctx.domain(pp,values_end)
        total+=arc.uint(pp,4,values_end)
    return total


def _state_chain(folder, issue, static_only):
    if folder is None:
        return []
    try:
        return read_inserts(folder,allow_mono_main=static_only)
    except (ValueError,TypeError) as exc:
        error=str(exc)
    # Isolate each slot so a post-fader or native-only effect doesn't erase
    # convertible devices before or after it. Keep the actual slot number.
    slots=folder.find('list[@name="Slot"]')
    if slots is None:
        issue('插入区：'+error)
        return []
    channel=ET.Element('member')
    channel.extend(c for c in folder if c is not slots)
    selected=ET.SubElement(channel,'list',name='Slot')
    result=[]
    occupied=0
    for i,item in enumerate(slots):
        selected.append(item)
        if item.find('member[@name="Plugin"]') is not None or value(item,'State','0')!='0':
            occupied+=1
            try:
                result.extend(read_inserts(channel,allow_mono_main=static_only))
            except (ValueError,TypeError) as exc:
                name=value(item.find('member[@name="Plugin"]'),'Plugin Name','未命名')
                issue(f'插槽 {i+1}（{name}）：{exc}')
        selected.remove(item)
        selected.append(ET.Element('item'))
    if not occupied:issue('插入区：'+error)
    return result


def _standard_pan(node):
    if (node is None or value(node,'Plugin Name')!='Standard Panner'
        or value(node.find('member[@name="Plugin UID"]'),'GUID')!='44E1149EDB3E4387BDD827FEA3A39EE7'):
        raise MixerFormatError('未识别的声像器。')
    comp=node.find('bin[@name="audioComponent"]')
    data=bytes.fromhex(comp.text or '') if comp is not None else b''
    if (len(data)!=20 or data[4:]!=bytes.fromhex('0000003F040000000200000000000000')
        or value(node,'Active','1')!='1'):
        raise MixerFormatError('声像器使用未确认的立体声模式或布局。')
    norm,=struct.unpack_from('<f',data)
    if not 0<=norm<=1:
        raise MixerFormatError('声像位置越界。')
    return (norm-0.5)*20


def _known_empty_strip(node):
    return (node is not None and value(node,'Bypass','0')=='0'
            and node.find('list[@name="Slot"]') is not None
            and all(value(slot,'State','?')=='0' and slot.find('member[@name="Plugin"]') is None
                    for slot in node.findall('list[@name="Slot"]/item')))


def _unconnected_sends(node):
    slots=node.find('list[@name="Slot"]')
    return (slots is not None and len(slots)>0 and all(
        set(item.get('name') for item in slot)<={'RuntimeID','Volume','Output','Panner','OldOn'}
        and value(slot,'OldOn','0')=='0' and value(slot.find('member[@name="Output"]'),'Value')=='0'
        for slot in slots))


@lru_cache(maxsize=1)
def _neutral():
    return json.loads((TEMPLATES/'neutral-processing.json').read_text(encoding='utf8'))


def _apply_channel(name, attrs, issue, static_only=False, master=False):
    volume=attrs.find('member[@name="Volume"]')
    gain, fader=value(volume,'AnchorValue'),value(volume,'Value')
    if gain is None or fader is None:
        raise MixerFormatError('缺少已保存的推子增益。')
    gain=float(gain)
    original_gain=gain
    if not math.isfinite(gain) or not -70<=gain<=6:
        issue(f'原轨道电平 {gain:g} dB 超出 ACE 的 -70～+6 dB 范围，已限制到可写区间。')
        gain=max(-70,min(6,gain))
    result=dict(name=name,gain=gain,fader_value=float(fader) if original_gain==gain else None,
                source_gain_db=original_gain,source_fader_value=float(fader),pan=0.,mute=False,solo=False)
    try: result['pan']=_standard_pan(attrs.find('member[@name="Panner"]'))
    except (ValueError,TypeError) as exc: issue(str(exc)+' 声像按居中处理。')
    result['plugins']=_state_chain(attrs.find('member[@name="InsertFolder"]'),issue,static_only)
    for plugin in result['plugins']:
        if plugin.get('source_main_layout')=='mono':
            issue(f"插槽 {plugin['slot']+1}（{plugin['name']}）：原生为单声道输入/输出；"
                  "在 ACE 轨上尝试还原同 CID 静态状态，声道布局差异可能改变声音。")
    labels={'InputFilter':'输入滤波器','InputGain':'输入增益','InputPhase':'极性','ModulatorsFolder':'调制器',
            'StripFolder':'通道条','EQ':'通道 EQ','SendFolder':'发送','foldbackSendFolder':'提示发送',
            'directRoutingFolder':'直接路由','outputBusEnable':'输出总线启用状态'}
    for key in PROCESSING:
        if key=='Panner':continue
        field=attrs.find(f'*[@name="{key}"]')
        if field is None or fingerprint(field) in _neutral().get(key,[]):continue
        # Exact Stereo Out strip from the known 48 kHz empty native template.
        if (master and key=='StripFolder' and fingerprint(field)==
            '17eea942fe777912496037c3225b7b8c6b0cd0707bd6bfd44d1eb4ea187ff027'):continue
        if key=='StripFolder' and _known_empty_strip(field):continue
        if key in ('SendFolder','foldbackSendFolder','directRoutingFolder') and _unconnected_sends(field):continue
        issue(f'{labels.get(key,key)}与已验证的直通设置不同，未迁移。')
    return result


def _device_tree(ctx):
    data,w=ctx.mapping,ctx.width
    p=8+w
    root=b''
    while p<len(data):
        base=p+4+w
        end=base+int.from_bytes(data[p+4:base],'big')
        if base>end or end>len(data):raise MixerFormatError('设备区块越界。')
        tag=data[p:p+4]
        if tag==b'ROOT':root=data[base:end]
        elif tag==b'ARCH' and root==struct.pack('>I',7)+b'Devices'+struct.pack('>I',11)+b'FAttributes':
            from native_inventory import _Archive
            with memoryview(data)[base:end] as view:
                archive=_Archive(view,w)
                kind,start,stop,after=archive.obj(0,len(view))
                if kind!='FAttributes' or after!=len(view):raise MixerFormatError('设备属性类型无效。')
                reader=_Fields(view,start,stop)
                tree=ET.Element('devices')
                reader.fields(tree)
                if reader.pos!=stop:raise MixerFormatError('设备属性未完整读取。')
            return tree
        p=end
    raise MixerFormatError('未识别工程设备区块。')


def _read_master(ctx, issue, static_only=False):
    tree=_device_tree(ctx)
    mix=tree.find('member[@name="VST Mixer"]')
    outputs=mix.findall('list[@name="Output Channels"]/item') if mix is not None else []
    names=[value(x.find('member[@name="Name"]'),'String','未命名') for x in outputs]
    if len(outputs)!=1:
        issue('原工程主输出数量无法映射到 ACE：'+str(len(outputs))+'（'+'、'.join(names)+'）。')
        return None
    name='主输出 '+names[0]
    result=_apply_channel(name,outputs[0],lambda msg:issue(name+'：'+msg),
                          static_only=static_only,master=True)
    result['input_bus_uid']=value(outputs[0].find('member[@name="OwnInputBus"]'),'Bus UID')
    result['channel_name']=names[0]
    if result['pan']!=0:issue(name+'：主输出声像未迁移。')
    return result


def read_native_mixer(project, static_only=False, include_master=False, bus_presets=None):
    """Return saved channels in arrangement order, diagnostics and optional master.

    In strict mode unrepresentable states prevent a successful conversion.
    In static-only mode every dropped state is reported by track and slot.
    """
    from native_events import Arrangement
    tracks, warnings, errors=[],[],[]
    issues=warnings if static_only else errors
    with Arrangement(project) as ctx:
        bus_names={}
        unresolved_devices=[]
        for index,(kind,name,start,end,attrs,node_end,parents) in enumerate(ctx.track_rows()):
            if kind=='MDeviceTrackEvent':
                try:
                    bus,_,_=_device(ctx.archive,node_end,end,'MTrack')
                except (ValueError,TypeError,struct.error):
                    unresolved_devices.append(name)
                    continue
                uid=value(bus.find('member[@name="OwnInputBus"]'),'Bus UID')
                if uid is not None:bus_names[uid]=name
                folder=bus.find('member[@name="InsertFolder"]')
                names=[value(slot,'Plugin Name','未识别插件') for slot in
                       folder.findall('list[@name="Slot"]/item/member[@name="Plugin"]')] if folder is not None else []
                suffix='；其插入为 '+ '、'.join(names) if names else ''
                if names and bus_presets is not None:
                    plugins=_state_chain(folder,lambda msg: issues.append(f'总线「{name}」：'+msg),static_only)
                    if plugins:
                        bus_presets.append(dict(name=name,plugins=plugins))
                issues.append(f'总线「{name}」的路由与通道处理无法在 ACE 还原'+suffix+'。')
                continue
            if kind!='MAudioTrackEvent':continue
            def issue(msg):issues.append(f'轨道 {index+1}「{name}」：'+msg)
            try:
                _,fields,flags,device_end=_trackinfo(ctx.archive,start,end)
                channel=_apply_channel(name,fields,issue,static_only=static_only)
                solo_flag=int(value(fields,'SoloFlag','0'))
                if flags in (0,32) and solo_flag==0:pass
                elif flags in (2,34) and solo_flag==1:channel['mute']=True
                else:issue(f'静音/独奏字段不在已确认范围（Flags={flags}, SoloFlag={solo_flag}），未应用。')
                channel['native_device_flags']=flags
                channel['native_solo_flag']=solo_flag
                channel['output_bus_value']=value(fields.find('member[@name="OutputBusValue"]'),'Value')
                try:
                    points=_automation_count(ctx,device_end,end)
                    if points:issue(f'含 {points} 个自动化事件，只能使用已保存的静态值。')
                except (ValueError,struct.error) as exc:issue('自动化列表未能完整核对：'+str(exc))
            except (ValueError,TypeError,struct.error) as exc:
                issue('通道设置无法完整解码，使用默认推子、声像和空插件链：'+str(exc))
                channel=dict(name=name,gain=0.,fader_value=None,pan=0.,mute=False,solo=False,plugins=[])
            channel['_native_index']=index
            tracks.append(channel)
        try:master=_read_master(ctx,issues.append,static_only)
        except (ValueError,TypeError,struct.error) as exc:
            issues.append('主输出设置无法核对：'+str(exc));master=None
        for device in unresolved_devices:
            if not master or device!=master['channel_name']:
                issues.append(f'设备轨「{device}」的设置无法还原。')
        for track in tracks:
            uid=track.get('output_bus_value')
            if not master or not uid or uid!=master.get('input_bus_uid'):
                route=bus_names.get(uid,'总线 ID '+str(uid))
                issues.append(f'轨道 {track["_native_index"]+1}「{track["name"]}」：原输出为「{route}」，'
                              '在 ACE 中接主输出。')
        if not include_master and master and (master['gain']!=0 or master['plugins']):
            issues.append('当前调用方未接收原生主输出增益或插件链。')
    result=(tracks,warnings,errors)
    return (*result,master) if include_master else result
