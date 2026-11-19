"""Read Steinberg track archives into an explicit, conservative transfer model."""
import copy, hashlib, json, math, struct
from platform_support import stored_directory
from pathlib import Path
import xml.etree.ElementTree as E

TEMPLATES = Path(__file__).parent/'templates'
PROCESSING = ('InputFilter','InputGain','InputPhase','ModulatorsFolder',
              'StripFolder','EQ','SendFolder','Panner','foldbackSendFolder','directRoutingFolder','outputBusEnable')

def canonical(node):
    if node is None: return None
    return [node.tag, {k:v for k,v in node.attrib.items() if k != 'ID'},
            ''.join((node.text or '').split()) if node.tag == 'bin' else '',
            [canonical(c) for c in node if c.get('name') not in ('RuntimeID','Editor Size Count')]]

def fingerprint(node):
    return hashlib.sha256(json.dumps(canonical(node),sort_keys=True).encode()).hexdigest()

def value(node, name, default=None):
    if node is not None:
        for e in node:
            if e.get('name') == name and 'value' in e.attrib: return e.get('value')
    return default

def number(node, name, default=0):
    result=float(value(node,name,default))
    if not math.isfinite(result): raise ValueError('XML 存在非有限数值：'+name)
    return result

class Archive:
    def __init__(self,path,bake_fades=False,static_only=False,native_timing=None,*,native_output=False):
        self.native_output=native_output
        self.path=Path(path).resolve();self.errors=[];self.warnings=['仅迁移轨道存档中的内容；不迁移主输出处理和音频设备路由。'];self.tracks=[]
        if self.path.suffix.lower()!='.xml': raise ValueError('检查 XML 需要选择 Steinberg 轨道存档 .xml。原生工程请从主转换入口打开。')
        raw=self.path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('不接受含外部实体的 XML。')
        self.root=E.fromstring(raw);self.source_sha256=hashlib.sha256(raw).hexdigest();self.master=None
        from project_colors import xml_hints
        self.color_hints=xml_hints(raw)
        if self.root.tag!='tracklist2': raise ValueError('这不是 Steinberg 轨道存档 XML（MusicXML 不能替代）。')
        self.ids={e.get('ID'):e for e in self.root.iter('obj') if e.get('class')}
        self.neutral=json.loads((TEMPLATES/'neutral-processing.json').read_text(encoding='utf8'))
        self.bake_fades=bake_fades;self.static_only=static_only;self.bpm=120.;self.rate=number(self.root.find('obj[@name="Setup"]'),'SampleRate',48000)
        if self.rate!=48000:self.errors.append('当前版本要求项目采样率 48 kHz。')
        tempos=list(self.root.iterfind('.//obj[@class="MTempoEvent"]'))
        if native_timing is not None:
            self.bpm=native_timing['bpm']
            if not 1<=self.bpm<=1000:self.errors.append('工程速度超出 ACE 可迁移范围。')
            if (native_timing['numerator'],native_timing['denominator'])!=(4,4):self.errors.append('当前版本只支持 4/4 工程。')
            if native_timing['frame_type']!=2:self.errors.append('当前版本只支持 24 fps 工程。')
            if native_timing['sample_rate']!=48000:self.errors.append('当前版本要求项目采样率 48 kHz。')
        elif tempos:
            bpms={number(e,'BPM') for e in tempos}
            if len(bpms)!=1 or not 1<=next(iter(bpms))<=1000:self.errors.append('当前版本只支持固定速度。')
            else:self.bpm=next(iter(bpms))
        else:self.warnings.append('XML 没有速度表；以 120 BPM 建立 ACE，音频保持绝对秒位置。')
        for e in self.root.iterfind('.//obj[@class="MTimeSignatureEvent"]'):
            if (number(e,'Numerator'),number(e,'Denominator'))!=(4,4):self.errors.append('当前版本只支持 4/4。')
        setup=self.root.find('obj[@name="Setup"]')
        if number(setup,'FrameType',2)!=2:self.errors.append('当前版本仅确认 24 fps 视频工程。')
        top=self.root.find('list[@name="track"]')
        if top is None:raise ValueError('XML 缺少轨道列表。')
        for t in top:self.read_track(self.resolve(t))
        if any('color_slot' in item for tr in self.tracks for item in [tr,*tr['events']]) and not self.color_hints:
            self.warnings.append('轨道 XML 只有色号时按标准工程调色板解释；自定义颜色可从 CPR/NPR 的工程调色板读取。')
        if not self.tracks:self.errors.append('没有可迁移的音频、视频或范围标记。')
        # Only an exact generated XML hash may reuse the ACE-side parameter table.
        # A DAW edit/export invalidates it; never overwrite new edits with stale data.
        companion=self.path.with_suffix('.plugins.json')
        if companion.is_file():
            data=json.loads(companion.read_text(encoding='utf8'))
            if data.get('xml_sha256')==self.source_sha256:
                if 'master' in data:
                    from plugin_state import parse_preset,from_envelope
                    master=data['master']
                    if not -70<=master['gain']<=6:raise ValueError('主输出推子值无效。')
                    for plugin in master['plugins']:
                        if parse_preset(from_envelope(plugin['preset']))[0]!=plugin['cid']:raise ValueError('主输出插件状态身份不匹配。')
                    self.master=master
                audio=[t for t in self.tracks if t['type']=='audio']
                if len(audio)!=len(data['tracks']):raise ValueError('插件附表轨道数不一致。')
                for track,original in zip(audio,data['tracks']):
                    if track['name']!=original['name'] or len(track.get('plugins',[]))!=len(original['plugins']):raise ValueError('插件附表与 XML 不一致。')
                    for plugin,saved in zip(track['plugins'],original['plugins']):
                        if plugin['cid']!=saved['cid'] or plugin['preset']['data']!=saved['preset']['data']:
                            # Info chunks are not processing state; compare Comp/Cont below.
                            from plugin_state import parse_preset,from_envelope
                            a=parse_preset(from_envelope(plugin['preset']))[1];b=parse_preset(from_envelope(saved['preset']))[1]
                            if plugin['cid']!=saved['cid'] or any(a.get(k)!=b.get(k) for k in (b'Comp',b'Cont')):raise ValueError('插件附表状态不一致。')
                        plugin.update(parameters=saved.get('parameters'),enabled=saved['enabled'],bypassed=saved['bypassed'],typeId=saved.get('typeId'),vendor=saved.get('vendor'))
            else:self.warnings.append('插件附表与当前 XML 哈希不同；仅使用当前 XML 状态，不回填旧参数。')
        self.errors=list(dict.fromkeys(self.errors));self.warnings=list(dict.fromkeys(self.warnings))

    def resolve(self,e):
        if e is None or e.get('class'):return e
        result=self.ids.get(e.get('ID'))
        if result is None:raise ValueError('XML 对象引用缺失：'+str(e.get('ID')))
        return result

    def child(self,e,name):return self.resolve(e.find(f'obj[@name="{name}"]')) if e is not None else None

    def media(self,node):
        p=self.child(node,'Path');name=value(p,'Name','');folder=value(p,'Path','')
        candidate=stored_directory(folder)/name
        if not candidate.is_absolute():candidate=self.path.parent/candidate
        if not candidate.is_file():
            alternatives=[self.path.parent/name,self.path.parent/'Audio'/name,self.path.parent/'Video'/name]
            valid=[p for p in alternatives if p.is_file()]
            if len(valid)==1:candidate=valid[0]
            else:self.errors.append('媒体缺失或重定位不唯一：'+str(candidate))
        return str(candidate.resolve())

    def fade(self,event,side,rate):
        f=self.child(event,side)
        if f is None:return {'length':0.,'native':True,'points':[]}
        c=self.child(f,'Curve')
        if c is None or c.get('class')!='MLinearInterpolator':
            self.errors.append('尚未支持的淡化曲线类型；请使用已确认的 S 形预置或先渲染。');return {'length':0.,'native':False,'points':[]}
        pts=[[number(p,'X'),number(p,'Y')] for p in c.findall('list/item')]
        maximum=number(c,'XMax');minimum=number(c,'XMin')
        if maximum<=0 or abs(minimum)>1e-8 or len(pts)<2 or any(b[0]<=a[0] for a,b in zip(pts,pts[1:])) or abs(pts[0][0])>1e-8 or abs(pts[-1][0]-maximum)>1e-6:
            self.errors.append('淡化控制点范围无效。');return {'length':0.,'native':False,'points':[]}
        norm=[[x/maximum,y] for x,y in pts]
        # Check the whole interpolated curve, not merely its endpoints.
        import numpy as np
        u=np.linspace(0,1,2049);actual=np.interp(u,[p[0] for p in norm],[p[1] for p in norm])
        expected=u*u*(3-2*u)
        if side=='FadeOut':expected=1-expected
        native=float(np.max(np.abs(actual-expected)))<2e-6
        if not native and not self.bake_fades:self.errors.append('存在非 ACE 默认 S 形淡化；可选“写入音频”模式，或使用已确认预置。')
        if not native:self.warnings.append('非默认淡化将写入独立浮点音频；这些淡化不能再用 ACE 手柄编辑。')
        return {'length':maximum/rate,'native':native,'points':norm}

    def read_track(self,t):
        kind=t.get('class');node=self.child(t,'Node');title=value(node,'Name',kind)
        if kind=='MFolderTrackEvent':
            self.warnings.append('文件夹结构在 ACE 展开为普通轨道；不保留编组关系。')
            children=node.find('list[@name="Tracks"]')
            if children is not None:
                for child in children:self.read_track(self.resolve(child))
            return
        types={'MAudioTrackEvent':'audio','MVideoTrackEvent':'video','MMarkerTrackEvent':'marker'}
        if kind not in types:self.errors.append(f'不支持轨道 {title}（{kind}）；MIDI、插件、总线须先处理。');return
        typ=types[kind];result={'type':typ,'name':title,'events':[],'gain':0.}
        from project_colors import read_xml_color
        result.update(read_xml_color(t,self.color_hints))
        domain=node.find('member[@name="Domain"]');unit=number(domain,'Type',1)
        if typ in ('audio','video') and unit!=1:self.errors.append(f'{title}：音视频轨需为绝对时间域。')
        device=self.child(t,'Track Device')
        if typ=='audio':
            a=device.find('member[@name="DeviceAttributes"]') if device is not None else None
            if a is None:self.errors.append(title+'：缺少通道设置。');return
            volume=a.find('member[@name="Volume"]')
            if volume is None or value(volume,'AnchorValue') is None:
                self.errors.append(title+'：缺少已确认的通道 dB 参数 AnchorValue。')
            result['gain']=number(volume,'AnchorValue')
            expected=24517 if result['gain']==0 else 685.1803826148858*result['gain']+24516.60714534251
            fader=number(volume,'Value',expected)
            if not 0<=fader<=32768:
                self.errors.append(title+'：轨道推子超出已知范围。')
            if not self.native_output and not -70.00001<=result['gain']<=6.00001:
                (self.warnings if self.static_only else self.errors).append(title+'：轨道增益超出 ACE 范围，需限制到可写区间。')
                result.update(source_gain_db=result['gain'],source_fader_value=fader)
            if not self.native_output:result['gain']=min(6,max(-70,result['gain']))
            if 'source_gain_db' not in result:result['fader_value']=fader
            flags=(number(device,'Flags'),number(a,'SoloFlag'))
            if flags not in ((0,0),(2,1)):
                self.errors.append(title+'：静音/独奏标志无法完整迁移。')
            result['mute']=flags==(2,1)
            from plugin_xml import read_inserts
            try:result['plugins']=read_inserts(a.find('member[@name="InsertFolder"]'))
            except (ValueError,TypeError) as error:self.errors.append(title+'：'+str(error));result['plugins']=[]
            if result['plugins']:self.warnings.append('VST3 保留静态完整状态；外部素材、许可证和插件版本需目标电脑满足。单个插件内部旁通保留在状态中，ACE 宿主按钮不一定显示同样状态。')
            for key in PROCESSING:
                e=next((x for x in a if x.get('name')==key),None)
                if key=='Panner' and e is not None:
                    e=copy.deepcopy(e)
                    state=e.find('bin[@name="audioComponent"]')
                    if state is not None and state.text:
                        data=bytes.fromhex(state.text)
                        if len(data)==20 and data[4:]==bytes.fromhex('0000003F040000000200000000000000'):
                            result['pan']=struct.unpack_from('<f',data)[0]*20-10
                            if not -10<=result['pan']<=10:self.errors.append(title+'：声像超出 ACE 范围。')
                            state.text=(struct.pack('<f',.5)+data[4:]).hex().upper()
                if e is not None and fingerprint(e) not in self.neutral.get(key,[]):self.errors.append(f'{title}：{key} 不是已确认的中性设置，不能完整迁移。')
            for auto in t.findall('.//obj[@class="MAutoListNode"]'):
                if any(len(x) for x in auto if x.tag=='list'):
                    if self.static_only:self.warnings.append(title+'：按所选模式仅迁移静态值；音量/插件等自动化不在 ACE 播放，原始数据保留在来源 XML，声音可能不同。')
                    else:self.errors.append(title+'：存在自动化数据；可选仅迁移静态值，或先渲染。')
            for variants in t.findall('.//obj[@class="MTrackVariationCollection"]/list'):
                if len(variants)>1:self.errors.append(title+'：存在多个 Track Versions，当前版本不能完整迁移。')
        events=node.find('list[@name="Events"]')
        for raw in ([] if events is None else events):
            e=self.resolve(raw);cls=e.get('class');start=number(e,'Start')+number(t,'Start')
            item={'start':start,'name':value(e,'Description',value(e,'Name',title)),'enabled':not bool(int(number(e,'Flags'))&2),'gain':0.}
            if typ=='audio' and cls=='MAudioEvent':
                ac=self.child(e,'AudioClip');cluster=self.child(ac,'Cluster')
                item['name']=value(e,'Description',value(ac,'Name',title))
                streams=cluster.find('list[@name="Substreams"]') if cluster is not None else None
                if streams is None or len(streams)!=1:self.errors.append(title+'：不支持多流或合成音频片段。');continue
                af=self.resolve(streams[0]);rate=number(af,'Rate',48000)
                item.update(path=self.media(ac),offset=number(e,'Offset')/rate,duration=number(e,'Length')/rate,gain=20*math.log10(number(e,'Volume',1)) if number(e,'Volume',1)>0 else -999)
                channels=number(af,'Channels',2)
                if rate<=0 or channels not in (1,2):self.errors.append(title+'：只支持单声道或双声道音频。')
                if not -70<=item['gain']<=30:self.errors.append(title+'：片段增益超出 ACE 范围。')
                segments=cluster.findall('list[@name="Segments"]/item')
                if len(segments)!=1 or number(segments[0],'Offset') or number(segments[0],'Start'):self.errors.append(title+'：存在分段音频源或离线处理，请先转成独立文件。')
                known={'Start','Length','Offset','Flags','Priority','Description','AudioClip','Inverted','VolCurve','FadeIn','FadeOut','Volume','Color','SnapOffset'}
                for field in e:
                    if field.get('name') not in known:self.errors.append(title+'：尚未支持的事件字段 '+str(field.get('name')))
                if number(e,'Inverted'):self.errors.append(title+'：反相尚未支持。')
                vol=self.child(e,'VolCurve')
                if vol is not None and any(len(x) for x in vol if x.tag in ('list','obj')):self.errors.append(title+'：存在片段包络。')
                attrs=ac.find('member[@name="Additional Attributes"]')
                if attrs is not None:
                    allowed={'GridDef','HPFilterMode','HPPeakThreshold','HPThreshold','StretchPreset','OrigPath'}
                    for field in attrs:
                        if field.get('name') not in allowed:self.errors.append(title+'：尚未支持的源处理 '+str(field.get('name')))
                item['fade_in']=self.fade(e,'FadeIn',rate);item['fade_out']=self.fade(e,'FadeOut',rate)
                if max(item['fade_in']['length'],item['fade_out']['length'])>item['duration']+1e-7:self.errors.append(title+'：淡化超出片段长度。')
                import soundfile as sf
                if Path(item['path']).is_file():
                    info=sf.info(item['path'])
                    if info.samplerate!=rate or info.channels!=channels:self.errors.append(title+'：素材采样率/声道与工程不匹配。')
                    if item['offset']+item['duration']>info.duration+1/info.samplerate:self.errors.append(title+'：片段超出源文件。')
            elif typ=='video' and cls=='MVideoEvent':
                item['name']=value(self.child(e,'Node'),'Name',title)
                item.update(path=self.media(self.child(e,'Node')),offset=number(e,'Offset'),duration=number(e,'Length'))
                if not item['enabled']:self.errors.append('禁用视频尚未支持。')
            elif typ=='marker' and cls in ('MRangeMarkerEvent','MMarkerEvent'):
                factor=60/(self.bpm*480) if unit==0 else 1
                point=cls=='MMarkerEvent'
                item.update(read_xml_color(e,self.color_hints))
                item.update(start=start*factor,duration=0. if point else number(e,'Length')*factor,offset=0.,
                            marker_kind='point' if point else 'range',marker_id=int(number(e,'ID',len(result['events'])+1)))
                if not item['enabled']:self.errors.append('禁用标记尚未支持。')
            else:self.errors.append(f'{title}：不支持事件 {cls}（含音频 Part、点标记等）。');continue
            if item['start']<0 or item.get('offset',0)<0 or (item['duration']<=0 and item.get('marker_kind')!='point'):self.errors.append(title+'：负时间或非正长度不受支持。')
            result['events'].append(item)
        # ACE does not mix overlapping audio on one lane. Do not silently trim or sum it.
        ordered=sorted(result['events'],key=lambda x:x['start'])
        end=-1
        for item in ordered:
            if typ!='marker' and item['start']<end-1e-9:self.errors.append(title+'：存在重叠事件，请先在宿主分轨（ACE 单轨导入会裁切）。')
            end=max(end,item['start']+item['duration'])
        self.tracks.append(result)

    def report(self):
        return {'source_xml':str(self.path),'source_sha256':self.source_sha256,'bpm':self.bpm,
                'audio_tracks':sum(t['type']=='audio' for t in self.tracks),
                'audio_events':sum(len(t['events']) for t in self.tracks if t['type']=='audio'),
                'video_events':sum(len(t['events']) for t in self.tracks if t['type']=='video'),
                'range_markers':sum(e.get('marker_kind')!='point' for t in self.tracks if t['type']=='marker' for e in t['events']),
                'point_markers':sum(e.get('marker_kind')=='point' for t in self.tracks if t['type']=='marker' for e in t['events']),
                'vst3_inserts':sum(len(t.get('plugins',[])) for t in self.tracks),
                'warnings':self.warnings,'errors':self.errors,'ready':not self.errors}
