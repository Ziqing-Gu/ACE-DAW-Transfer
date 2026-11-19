"""ACE saved data -> native Steinberg XML. No Nuendo project/export is an input."""
import copy, hashlib, json, math, re, struct
from pathlib import Path
import xml.etree.ElementTree as E
import native_xml as n

VERSION='1.4.10'

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def check_source(d):
    errors=[]
    tempos=d.get('tempos',[])
    if len(tempos)!=1 or tempos[0].get('position')!=0 or not 1<=tempos[0].get('bpm',0)<=1000 or tempos[0].get('bend',0)!=0:errors.append('当前版本只支持固定速度。')
    if d.get('timeSignatures')!=[{'barPos':0,'numerator':4,'denominator':4}]:errors.append('当前版本只支持固定 4/4 拍。')
    if not -70<=d.get('master',{}).get('gain',0)<=6:errors.append('主输出增益超出 ACE 范围。')
    for t in d.get('tracks',[]):
        if not t.get('patterns') and not t.get('_plugins'):continue
        if t.get('type')!='audio':errors.append('存在未渲染的 MIDI/歌声/乐器轨，请先转换成音频。');continue
        if not -10<=t.get('pan',0)<=10 or t.get('solo'):errors.append('轨道声像超出范围，或使用尚未校准的独奏。')
        if t.get('extraInfo'):errors.append('轨道存在未支持的附加处理数据。')
        if not -20<=t.get('gain',0)<=0 and '_source_fader' not in t:errors.append('该轨道增益超出已校准的 -20 至 0 dB 范围，且缺少匹配的原生推子值。')
        for c in t['patterns']:
            if c.get('timeUnit')!='sec':errors.append('音频片段必须使用秒时间单位。')
            if c.get('isStreaming'):errors.append('存在尚未下载完成的流式音频。')
            if not -70<=c.get('gain',0)<=30:errors.append('片段增益超出 -70 至 +30 dB 范围。')
            for key in ['fadeIn','fadeOut']:
                f=c.get(key,{})
                if f.get('shape')!={'offsetX':0.0,'offsetY':0.0}:errors.append('存在尚未支持的非默认 S 形淡化。')
                if f.get('length',0)<0 or f.get('length',0)>c['clipDur']:errors.append('存在超出片段长度的淡化。')
            if c.get('fadeOut',{}).get('crossfade'):errors.append('存在联动交叉淡化，请先转换为独立淡入淡出。')
    for t in d.get('videoTracks',[]):
        for c in t.get('patterns',[]):
            if c['timeUnit']!='sec' or c['clipPos']<0 or not c['enabled'] or not c.get('detached'):
                errors.append('当前版本视频须为秒时间单位、非负源偏移、启用且音频已分离。')
    for t in d.get('markerTracks',[]):
        for c in t.get('patterns',[]):
            if c['timeUnit']!='tick' or not c['enabled'] or c['clipDur']<=0:errors.append('当前版本只支持启用的范围标记。')
    if d.get('chordTrack',{}).get('patterns'):errors.append('当前版本不迁移和弦轨。')
    if errors:raise ValueError('\n'.join(dict.fromkeys(errors)))

def prepare_media(d,source,out,log=print,reuse=None,intermediate=False):
    import soundfile as sf
    import numpy as np
    media={};videos={};base=Path(source).parent
    req={}
    for t in d['tracks']:
        for c in t.get('patterns',[]):req[c['path']]=max(req.get(c['path'],0),c['clipPos']+c['clipDur'])
    for i,(key,need) in enumerate(req.items(),1):
        src=(base/key).resolve()
        if not src.is_file():raise FileNotFoundError('缺少音频：'+str(src))
        info=sf.info(src)
        if info.channels not in (1,2) or info.subtype not in ['FLOAT','PCM_16','PCM_24','PCM_32']:
            raise ValueError('当前版本需要单声道或双声道 PCM / FLOAT 音频：'+str(src))
        if round(need*info.samplerate)>info.frames:raise ValueError('片段超出源音频范围：'+str(src))
        if info.format in ('WAV','WAVEX'):
            media[key]={'source':str(src),'output':str(src),'file':src.name}
            continue
        if info.frames*info.channels*4+128>=2**32:raise ValueError('单个音频将超过 WAV RIFF 的 4 GB 限制。')
        cached=reuse.get(key) if reuse else None
        if cached and cached.get('source_sha256') and Path(cached['output']).is_file() and sha(src)==cached['source_sha256'] and sha(cached['output'])==cached['output_sha256']:
            media[key]=cached;log(f'复用音频 {i}/{len(req)}：{src.name}');continue
        dst=out/'Audio'/f'{i:02d}_{src.stem}.wav'
        dst.parent.mkdir(exist_ok=True)
        if dst.exists():raise FileExistsError('输出文件已存在：'+str(dst))
        log(f'准备音频 {i}/{len(req)}：{src.name}')
        with sf.SoundFile(src) as a,sf.SoundFile(dst,'w',samplerate=info.samplerate,channels=info.channels,format='WAV',subtype='FLOAT') as b:
            while a.tell()<len(a):b.write(a.read(262144,dtype='float32',always_2d=True))
        # A completed media file is accepted only when its float32 samples match.
        with sf.SoundFile(src) as a,sf.SoundFile(dst) as b:
            while a.tell()<len(a):
                x=a.read(262144,dtype='float32',always_2d=True)
                if not np.array_equal(x,b.read(len(x),dtype='float32',always_2d=True)):raise RuntimeError('音频数据不一致：'+str(src))
        media[key]={'source':str(src),'output':str(dst),'file':dst.name,'source_sha256':sha(src),'output_sha256':sha(dst)}
    for t in d.get('videoTracks',[]):
        for c in t.get('patterns',[]):
            key=c['path']
            if key in videos:continue
            src=(base/key).resolve()
            if not src.is_file():raise FileNotFoundError('缺少视频：'+str(src))
            videos[key]={'source':str(src),'output':str(src)}
    return media,videos

def build_xml(d,snapshot,media,videos,destination):
    check_source(d);n.counter=100000;n.runtime=10000
    from project_colors import FACTORY,export_palette,xml_color
    def color(item):return item.get('_export_color',item.get('color'))
    items=[t for t in d['tracks'] if t.get('type')=='audio']
    items.extend(t for group in ('videoTracks','markerTracks') for t in d.get(group,[]) if t.get('patterns'))
    items.extend(c for track in d.get('markerTracks',[]) for c in track.get('patterns',[]))
    slots,palette=export_palette(color(item) for item in items)
    st={x['track']['trackUuid']:x['track'] for x in snapshot};sc={c['clipUuid']:c for x in snapshot for c in x['clips']}
    tracks=[];events=fades=muted=0;plugin_count=0;warnings=[]
    for i,t in enumerate([t for t in d['tracks'] if t.get('type')=='audio'],1):
        gain=t['gain'];n.gainmap[str(gain)]={'value':24517 if gain==0 else struct.unpack('f',struct.pack('f',685.1803826148858*gain+24516.60714534251))[0]}
        ev=[]
        for c in t['patterns']:
            q=sc[c['uuid']];start=c['pos']+c['clipPos']
            if abs(start-q['clipBeginSec'])>1e-8:raise ValueError('ACE 数据与时间线快照不一致。')
            event=n.event(Path(media[c['path']]['output']),q['clipName'],start,c['clipDur'],c['clipPos'],c['enabled'],c['fadeIn']['length'],c['fadeOut']['length'])
            if c.get('gain',0):n.val(event,'Volume',10**(c['gain']/20))
            ev.append(event)
            events+=1;muted+=not c['enabled'];fades+=sum(c[k]['length']>0 for k in ['fadeIn','fadeOut'])
        tr=n.track(st[t['uuid']]['trackName'],gain,ev,muted=t.get('mute',False))
        xml_color(tr,color(t),slots)
        a=tr.find('obj[@name="Track Device"]/member[@name="DeviceAttributes"]')
        if '_source_fader' in t:n.val(a.find('member[@name="Volume"]'),'Value',t['_source_fader'])
        if '_source_anchor' in t:n.val(a.find('member[@name="Volume"]'),'AnchorValue',t['_source_anchor'])
        if t.get('pan'):
            pan=a.find('member[@name="Panner"]/bin[@name="audioComponent"]')
            packed=bytes.fromhex(pan.text)
            pan.text='\n'+(struct.pack('<f',(t['pan']+10)/20)+packed[4:]).hex().upper()+'\n'
        from plugin_xml import write_inserts
        plugins=t.get('_plugins',[])
        if plugins:
            warnings.extend(write_inserts(a.find('member[@name="InsertFolder"]'),plugins))
            plugin_count+=len(plugins)
            for x in tr.findall('.//int[@name="RuntimeID"]'):
                n.runtime+=1;x.set('value',str(n.runtime))
        n.val(a,'IDString',f'ACE_TRANSFER_T{i:03d}','string')
        for x in tr.findall('.//int[@name="CVPortID"]'):x.set('value',str(5521633218689777184+i))
        for x in tr.findall('.//obj[@class="PChordTrackParam"]'):n.val(x,'uniqueID',int(x.get('ID')),'int')
        tracks.append(tr)
    video_tracks=[]
    for t in d.get('videoTracks',[]):
        ve=[];tr=None
        for c in t.get('patterns',[]):
            v=n.video(Path(videos[c['path']]['output']),c['pos']+c['clipPos'],c['clipDur'])
            n.name(v.find('.//obj[@class="MVideoEvent"]/obj[@name="Node"]'),sc[c['uuid']]['clipName'])
            if c['clipPos']:n.val(v.find('.//obj[@class="MVideoEvent"]'),'Offset',c['clipPos'])
            if tr is None:tr=v
            ve.append(v.find('.//obj[@class="MVideoEvent"]'))
        if tr is not None:
            xml_color(tr,color(t),slots)
            n.name(tr.find('obj[@name="Node"]'),st[t['uuid']]['trackName'])
            el=tr.find('obj[@name="Node"]/list[@name="Events"]');el[:]=ve;video_tracks.append(tr)
    marker_template=E.parse(n.B/'Nuendo_Marker_Cycle.xml').getroot()
    marker_tracks=[];markers=0;points=0
    for t in d.get('markerTracks',[]):
        if not t.get('patterns'):continue
        tr=n.clone(marker_template.find('./list/obj[@class="MMarkerTrackEvent"]'))
        xml_color(tr,color(t),slots)
        node=tr.find('obj[@name="Node"]');n.name(node,st[t['uuid']]['trackName'])
        el=node.find('list[@name="Events"]');sample=copy.deepcopy(el[0]);el[:]=[]
        assigned={c['_marker_id'] for c in t['patterns'] if c.get('_marker_id') is not None}
        next_id=1
        for c in t['patterns']:
            ident=c.get('_marker_id')
            if ident is None:
                while next_id in assigned:next_id+=1
                ident=next_id
                next_id+=1
            e=n.clone(sample)
            xml_color(e,color(c),slots)
            if c.get('_point'):
                e.set('class','MMarkerEvent')
                e.remove(e.find('float[@name="Length"]'))
                points+=1
            else:
                n.val(e,'Length',c['clipDur'])
                markers+=1
            n.val(e,'Start',c['pos']+c['clipPos']);n.name(e,sc[c['uuid']]['clipName']);n.val(e,'ID',ident,'int');el.append(e)
        marker_tracks.append(tr)
    root=n.document(video_tracks+marker_tracks+tracks)
    if tuple(palette)!=FACTORY:
        warnings.append('轨道存档按目标工程的调色板显示。要使用随附色盘，请在配色底板中导入 XML。')
    if any(palette[slot]!=c for c,slot in slots.items()):
        warnings.append('工程超过 40 种颜色，多出的颜色使用相近色。')
    root.append(n.clone(marker_template.find('obj[@class="PMarkerAttributeSettings"]')))
    bpm=d['tempos'][0]['bpm']
    for tempo in root.findall('.//obj[@class="MTempoEvent"]'):n.val(tempo,'BPM',bpm)
    for tempo in root.findall('.//obj[@class="MTempoTrackEvent"]'):n.val(tempo,'RehearsalTempo',bpm)
    ends=[c['pos']+c['clipPos']+c['clipDur'] for t in d['tracks'] for c in t.get('patterns',[])]
    ends.extend(c['pos']+c['clipPos']+c['clipDur'] for t in d.get('videoTracks',[]) for c in t.get('patterns',[]))
    ends.extend((c['pos']+c['clipPos']+c['clipDur'])/(bpm*8) for t in d.get('markerTracks',[]) for c in t.get('patterns',[]))
    duration=max(ends,default=0)+10
    for tr in tracks+video_tracks:n.val(tr,'Length',duration)
    for tr in marker_tracks:n.val(tr,'Length',duration*bpm*8)
    n.val(root.find('obj[@name="Setup"]/member[@name="Length"]'),'Time',duration)
    defs=[x.get('ID') for x in root.iter('obj') if x.get('class')]
    refs=[x.get('ID') for x in root.iter('obj') if not x.get('class')]
    if len(defs)!=len(set(defs)) or not set(refs)<=set(defs):raise ValueError('XML 对象引用不完整。')
    E.indent(root,space='   ')
    text=E.tostring(root,encoding='unicode').replace(' />','/>')
    # Nuendo's importer requires its native structural tag serialization.
    text=re.sub(r'<(member|list|obj)(\s[^<>]*?)/>',lambda m:'<'+m[1]+m[2]+'></'+m[1]+'>',text)
    text='<?xml version="1.0" encoding="utf-8"?>\n'+text+'\n'
    destination=Path(destination)
    if destination.exists():raise FileExistsError('不会覆盖已有 XML：'+str(destination))
    destination.write_bytes(text.replace('\n','\r\n').encode('utf8'));E.parse(destination)
    master=d.get('master',{})
    master_extra=bool(master.get('_plugins') or master.get('gain',0))
    if plugin_count or master_extra:
        warnings.append('VST3 传递完整状态；外部采样/脉冲文件、许可证、插件版本差异需目标宿主自行满足。')
        companion={'schema':1,'xml_sha256':sha(destination),'tracks':[{'name':st[t['uuid']]['trackName'],'plugins':t.get('_plugins',[])} for t in d['tracks'] if t.get('type')=='audio']}
        if master_extra:
            companion['master']={'name':'主输出','gain':master.get('gain',0),'plugins':master.get('_plugins',[])}
            warnings.append('轨道存档 XML 的主输出无法随轨道导入 DAW，主输出推子及插件保存在同名 .plugins.json 内，可由本工具读回 ACE。')
        destination.with_suffix('.plugins.json').write_text(json.dumps(companion,ensure_ascii=False,indent=2),encoding='utf8')
    return {'tool_version':VERSION,'audio_tracks':len(tracks),'audio_events':events,'muted_events':muted,'native_fades':fades,'video_tracks':len(video_tracks),'range_markers':markers,'point_markers':points,'audio_files':len(media),'vst3_inserts':plugin_count,'warnings':warnings,'xml':str(destination),'xml_sha256':sha(destination),'duration_seconds':duration,'palette':palette,'source_colors':len(slots),'xml_color_slots':len(set(slots.values())),'origin':'ACE saved project data; generated directly by converter'}
