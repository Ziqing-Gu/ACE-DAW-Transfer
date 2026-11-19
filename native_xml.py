import copy, json, math, struct, sys, shutil, hashlib
from platform_support import media_directory
from pathlib import Path
import xml.etree.ElementTree as E
B=Path(__file__).parent/'templates'
base=E.parse(B/'Nuendo_Scurve_LinearTime.xml').getroot()
at=base.find('./list/obj')
et=at.find('.//obj[@class="MAudioEvent"]')
ft=E.parse(B/'Nuendo_Folder_Template.xml').getroot().find('./list/obj')
vt=E.parse(B/'Nuendo_Video_Template.xml').getroot().find('./list/obj')
counter=100000
runtime=10000
gainmap=json.loads((B/'gain-map-native.json').read_text(encoding='utf-8'))
def uid():
 global counter
 counter+=1
 return str(counter)
def clone(n):
 n=copy.deepcopy(n)
 ids={e.get('ID'):uid() for e in n.iter('obj') if e.get('class')}
 for e in n.iter('obj'):
  if e.get('ID') in ids:e.set('ID',ids[e.get('ID')])
 return n
def val(n,name,v,tag='float'):
 e=n.find(f'{tag}[@name="{name}"]')
 if e is None:e=E.SubElement(n,tag,name=name)
 e.set('value',str(v))
def name(n,s):val(n,'Name',s,'string')
def curve(event,side,seconds,rate=48000):
 old=event.find(f'obj[@name="{side}"]')
 if old is not None:event.remove(old)
 if seconds<=0:return
 f=E.SubElement(event,'obj',{'class':'M'+side,'name':side,'ID':uid()})
 c=E.SubElement(f,'obj',{'class':'MLinearInterpolator','name':'Curve','ID':uid()})
 pts=E.SubElement(c,'list',name='Points',type='list')
 length=seconds*rate
 for i in range(1025):
  x=i/1024;y=x*x*(3-2*x)
  if side=='FadeOut':y=1-y
  p=E.SubElement(pts,'item');val(p,'X',x*length);val(p,'Y',y)
 for k,v in [('XMin',0),('XMax',length),('YMin',0),('YMax',1)]:val(c,k,v)
def wavinfo(path):
 with open(path,'rb')as f:
  assert f.read(4)==b'RIFF';f.seek(12)
  framebytes=6;bits=24;floatfmt=False
  while True:
   ck=f.read(8);assert len(ck)==8
   typ,size=struct.unpack('<4sI',ck)
   if typ==b'fmt ':
    info=f.read(size);format,channels,rate,bps,framebytes,bits=struct.unpack_from('<HHIIHH',info)
    if format==65534:format=struct.unpack_from('<H',info,24)[0]
    floatfmt=format==3
    if size%2:f.seek(1,1)
    continue
   if typ==b'data':return f.tell(),size//framebytes,bits,framebytes,floatfmt,rate,channels
   f.seek(size+(size%2),1)
def event(path,title,start,dur,offset,enabled,fi,fo):
 off,frames,bits,framebytes,floatfmt,rate,channels=wavinfo(path)
 e=clone(et);val(e,'Start',start);val(e,'Length',dur*rate);val(e,'Offset',offset*rate)
 val(e,'Description',title,'string')
 flag=e.find('int[@name="Flags"]')
 if flag is not None:e.remove(flag)
 if not enabled:val(e,'Flags',2,'int')
 ac=e.find('obj[@name="AudioClip"]');name(ac,title)
 # Hitpoints from the six-second tone are not transferable.
 for node in list(ac):
  if node.tag=='list' and node.get('name')=='Events':ac.remove(node)
 p=ac.find('obj[@name="Path"]');name(p,path.name);val(p,'Path',media_directory(path.parent),'string')
 af=ac.find('.//obj[@class="AudioFile"]')
 val(af,'Rate',rate)
 for k,v in [('FrameCount',frames),('DataOffset',off),('Sample Size',bits),('Frame Size',framebytes),('Channels',channels),('Format',131072 if floatfmt else 65536)]:val(af,k,v,'int')
 seg=ac.find('.//list[@name="Segments"]/item');val(seg,'Length',frames,'int')
 curve(e,'FadeIn',fi,rate);curve(e,'FadeOut',fo,rate)
 return e
def track(title,gain,events,muted=False):
 global runtime
 t=clone(at);name(t.find('obj[@name="Node"]'),title)
 ev=t.find('obj[@name="Node"]/list[@name="Events"]');ev.clear();ev.attrib.update(name='Events',type='obj');ev.extend(events)
 val(t,'Length',1800)
 d=t.find('obj[@name="Track Device"]');val(d,'Flags',2 if muted else 0,'int')
 a=d.find('member[@name="DeviceAttributes"]');val(a.find('member[@name="Name"]'),'String',title,'string')
 v=a.find('member[@name="Volume"]');val(v,'AnchorValue',gain)
 if str(gain) in gainmap:val(v,'Value',gainmap[str(gain)]['value'])
 elif gain==0:val(v,'Value',24517)
 else:raise ValueError('Gain has not been calibrated: '+str(gain))
 if muted:val(a,'SoloFlag',1,'int')
 for n in t.findall('.//int[@name="RuntimeID"]'):
  runtime+=1;n.set('value',str(runtime))
 val(a.find('member[@name="OwnInputBus"]'),'Bus UID',int(uid()),'int')
 val(a.find('member[@name="OwnInputBus"]'),'Name',title,'string')
 val(a,'IDString',title,'string')
 # Empty automation is unnecessary; avoid imported parameter references.
 au=t.find('obj[@name="Automation"]')
 if au is not None:t.remove(au)
 return t
def folder(title,tracks,muted=False):
 f=copy.deepcopy(ft);f.find('obj[@name="Node"]/list[@name="Tracks"]').clear()
 f=clone(f);name(f.find('obj[@name="Node"]'),title)
 lst=f.find('obj[@name="Node"]/list');lst.attrib.update(name='Tracks',type='obj');lst.extend(tracks)
 val(f,'Solo',4 if muted else 0,'int');val(f,'Length',1800);val(f,'Expanded',0,'int')
 return f
def document(tracks):
 r=copy.deepcopy(base);l=r.find('list[@name="track"]');l.clear();l.attrib.update(name='track',type='obj');l.extend(tracks)
 val(r.find('obj[@name="Setup"]/member[@name="Length"]'),'Time',1800)
 return r
def video(path,start,duration):
 t=clone(vt);val(t,'Length',1800);name(t.find('obj[@name="Node"]'),'旧版画面参考 · 24 fps')
 e=t.find('.//obj[@class="MVideoEvent"]');val(e,'Start',start);val(e,'Length',duration)
 p=e.find('obj[@name="Node"]');name(p,path.stem)
 f=p.find('obj[@name="Path"]');name(f,path.name);val(f,'Path',media_directory(path.parent),'string')
 return t
def write(r,p):
 E.indent(r,space='  ');E.ElementTree(r).write(p,encoding='utf-8',xml_declaration=True)
 defs=[n.get('ID')for n in r.iter('obj')if n.get('class')];assert len(defs)==len(set(defs)), 'duplicate IDs'
 refs=[n.get('ID')for n in r.iter('obj')if not n.get('class')];assert set(refs)<=set(defs),set(refs)-set(defs)
