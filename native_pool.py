"""Project-local media for native writeback. Only physical media streams are assets."""
import bisect
import re
from platform_support import media_directory
from pathlib import Path, PureWindowsPath

from ace_file import load_project
from convert_project import sha
from native_events import Arrangement, need


def declarations(data, width, name):
    marker = (len(name)+1).to_bytes(4,'big') + name.encode('ascii') + b'\0'
    for m in re.finditer(re.escape(marker),data):
        start = m.start()-width
        if start>=0 and int.from_bytes(data[start:m.start()],'big') in ((1<<(width*8))-1,(1<<(width*8))-2):
            yield start


def objects(a, data, name):
    seen=set()
    for decl in declarations(data,a.width,name):
        token=(a.archive.ref_bit+decl).to_bytes(a.width,'big')
        starts=[decl,*(m.start() for m in re.finditer(re.escape(token),data) if m.start()>decl)]
        for start in starts:
            kind,body,end,_=a.archive.obj(start,len(data))
            if kind==name and body not in seen:
                seen.add(body)
                yield start,body,end


def physical_files(a):
    data=bytes(a.view)
    paths=set()
    for _,s,e in objects(a,data,'AudioFile'):
        kind,ps,pe,_=a.archive.obj(s+18,e)
        need(kind=='FNPath','媒体池音频流的文件描述不受支持。')
        paths.add((*a.fnpath(ps,pe),'Audio'))
    for _,s,e in objects(a,data,'PVideoClip'):
        _,p=a.text(s,e)
        _,period,p=a.domain(p,e)
        need(period==1 and a.archive.uint(p,4,e)==0,'媒体池视频流的时间域不受支持。')
        kind,ps,pe,_=a.archive.obj(p+4,e)
        need(kind=='FNPath','媒体池视频文件路径不受支持。')
        paths.add((*a.fnpath(ps,pe),'Video'))
    return sorted(paths)


def saved_project_folder(a):
    data=bytes(a.view)
    for _,pool,end in objects(a,data,'PPool'):
        kind,_,_,after=a.archive.obj(pool,end)
        if kind=='GTree' and after+a.width<end:
            kind,start,stop,_=a.archive.obj(after+a.width,end)
            if kind=='FNPath':
                _,directory=a.fnpath(start,stop)
                if Path(directory).is_absolute():return Path(directory)
    return None


def ace_sources(ace, manifest):
    """Resolve pristine samples by hash; old manifests can use a still-present original to verify."""
    ace=Path(ace).resolve()
    result={}
    for old,item in manifest.get('source_media',{}).items():
        sample=(ace.parent/item['path']).resolve()
        if sample.is_file() and sha(sample)==item['sha256']:
            result[Path(old).resolve()]=sample
    if not manifest.get('source_media'):
        project,_=load_project(ace)
        clips={c['uuid']:c for group in ('tracks','videoTracks') for t in project.get(group,[]) for c in t.get('patterns',[])}
        for link in manifest['events']:
            old=link['source_event'].get('path')
            clip=clips.get(link['ace_clip_uuid'])
            if not old or not clip or not clip.get('path'):
                continue
            original=Path(old).resolve()
            sample=(ace.parent/clip['path']).resolve()
            if original not in result and original.is_file() and sample.is_file() and sha(original)==sha(sample):
                result[original]=sample
    return result


def tail_relocations(a,data,tend,first,new_pos):
    ar,w=a.archive,a.width
    updates={}
    # Class descriptors and concrete objects use byte offsets within ARCH. The
    # original prefix (including audio/MIDI/plug-in payloads) is never relocated.
    # Accepted tail relocations are explicit interpolator child headers. Refuse
    # compaction on any other reference into deleted or relocated object data.
    code_points=set()
    for _,s,e in objects(a,data,'MAutoFadeSetting'):
        if s<tend:continue
        p=s+10
        while p<e:
            start=p
            kind,_,_,p=ar.obj(p,e)
            need(kind in ('MLinearInterpolator','MCubicInterpolator') and p<=e,'未识别的全局淡化对象。')
            code_points.add(start)
        need(p==e,'全局淡化对象长度异常。')

    tail=data[tend:]
    # Prove retained tail values cannot refer to a removed/moved object size
    # field or class. Ambiguous values cause a conservative, untrimmed export.
    for i in range(max(0,len(tail)-w+1)):
        token=int.from_bytes(tail[i:i+w],'big')
        value=token & (ar.ref_bit-1)
        pos=tend+i
        if not first<=value<pos:
            continue
        if token & ar.ref_bit:
            if data[value:value+w] not in (ar.declare.to_bytes(w,'big'),(ar.declare-1).to_bytes(w,'big')):
                continue
            try:ar.class_ref(token,pos)
            except ValueError:continue
            target=new_pos(value)
            need(pos in code_points,'后续类引用的结构尚未验证。')
            updates[pos]=(w,ar.ref_bit+target)
        else:
            try:ar.referenced_type(value,pos)
            except ValueError:continue
            raise ValueError('后续对象仍引用了素材池中的对象。')
    return updates


def _lean_tree(a,data):
    """Remove only pool-owned audio/video leaves. Timeline objects precede the pool."""
    ar,w=a.archive,a.width
    pools=list(objects(a,data,'PPool'))
    need(len(pools)==1,'媒体池不唯一。')
    pool_pos,pool,pend=pools[0]
    kind,tree,tend,_=ar.obj(pool,pend)
    need(kind=='GTree','不支持的媒体池树。')
    root_kind,root,rend,p=ar.obj(tree,tend)
    need(root_kind=='GTreeEntry' and p+4==tend,'不支持的素材池根记录。')
    cuts=[]
    updates={}

    def visit(s,e,depth):
        need(depth<128,'媒体池文件夹嵌套过深。')
        tag=ar.uint(s,w,e)
        kind,body,p=('folder',0,s+w)
        if tag:
            kind,body,_,p=ar.obj(s,e)
        count_at=p+2
        count=ar.uint(count_at,4,e)
        p=count_at+4
        need(count<=(e-p)//w,'素材池子项数量异常。')
        children=0
        removed=0
        for _ in range(count):
            at=p
            typ,sub,stop,p=ar.obj(at,e)
            need(typ=='GTreeEntry' and p==stop,'不支持共享的素材池树节点。')
            keep,removed_below=visit(sub,stop,depth+1)
            if keep:
                children+=1
                removed+=removed_below
            else:
                cuts.append((at,p))
                removed+=p-at
        _,p=a.text(p,e)
        need(p+4==e,'素材池节点有未识别的附加字段。')
        if kind in ('PAudioClip','PVideoClip') and body>=pool:
            need(count==0,'未使用的素材同时包含其他资源。')
            return False,0
        need(kind=='folder' or (body<pool and tag<ar.ref_bit),'未知素材池项目，保留其依赖。')
        if removed:
            updates[s-w]=(w,e-s-removed)
            updates[count_at]=(4,children)
        return True,removed

    _,removed=visit(root,rend,0)
    if not removed:
        return data,0
    cuts.sort()
    starts=[s for s,_ in cuts]
    ends=[e for _,e in cuts]
    cumulative=[0]
    for s,e in cuts:cumulative.append(cumulative[-1]+e-s)
    def new_pos(pos):
        i=bisect.bisect_right(ends,pos)
        need(i==len(cuts) or pos<starts[i],'后续对象依赖要裁去的素材。')
        return pos-cumulative[i]

    top,body,last,_=ar.obj(0,len(data))
    need(top=='PArrangement' and last==len(data),'未知编排结构。')
    root_type,mroot,mend,_=ar.obj(body,last)
    need(root_type=='MRoot' and mroot<=pool_pos and pend<=mend,'无法识别素材池的所属对象。')
    for s,e in ((body,last),(mroot,mend),(pool,pend),(tree,tend)):
        updates[s-w]=(w,e-s-removed)

    updates.update(tail_relocations(a,data,tend,starts[0],new_pos))
    edited=bytearray(data)
    for at,(width,value) in updates.items():edited[at:at+width]=value.to_bytes(width,'big')
    parts=[];p=0
    for s,e in cuts:
        parts.append(edited[p:s]);p=e
    parts.append(edited[p:])
    return b''.join(parts),len(cuts)


def replace_archive(project,offset,w,old_size,updated):
    with project.open('r+b') as f:
        f.seek(offset+old_size);suffix=f.read()
        f.seek(offset-w);f.write(len(updated).to_bytes(w,'big'));f.write(updated);f.write(suffix)
        end=f.tell();f.truncate();f.seek(4);f.write((end-w-8).to_bytes(w,'big'))


def compact_pool(project):
    """Compact an unpublished working file. Retain the complete pool on unfamiliar layouts."""
    with Arrangement(project) as a:
        data=bytes(a.view)
        try:compact,removed=_lean_tree(a,data)
        except (ValueError,UnicodeError) as exc:return 0,str(exc)
        offset,w=a.offset,a.width
    if removed:
        replace_archive(project,offset,w,len(data),compact)
        with Arrangement(project) as check:
            _,remaining=_lean_tree(check,bytes(check.view))
            need(not remaining,'素材池写回核对失败。')
            list(check.track_rows())
            physical_files(check)
    return removed,None


def reference_media(project,destination,samples):
    """Store the new pool location so original absolute media paths stay external."""
    with Arrangement(project) as a:
        ar,w=a.archive,a.width
        data=bytes(a.view)
        files=physical_files(a)
        matched=shared=0
        for name,directory,_ in files:
            source=Path(directory)/name
            need(source.is_absolute() and source.is_file(),f'原生工程的外部媒体已移动：{source}。可用 XML 直接引用当前 ACE 采样。')
            ace=samples.get(source)
            if ace:
                need(source==ace or sha(source)==sha(ace),f'ACE 与原生来源采样已不同：{name}。请使用直接引用 ACE 采样的 XML。')
                matched+=1
                shared+=int(source.samefile(ace))
        pools=list(objects(a,data,'PPool'))
        need(len(pools)==1,'无法唯一确定原生工程的媒体池。')
        pool_pos,pool,pend=pools[0]
        kind,_,_,p=ar.obj(pool,pend)
        need(kind=='GTree' and ar.uint(p,w,pend)==0,'媒体池保存位置的结构未确认。')
        name_at=p+w
        kind,body,end,after=ar.obj(name_at,pend)
        need(kind=='FNPath' and body>name_at and after==end,'工程保存位置使用共享路径对象。')
        _,pos=a.text(body,end)
        loc=pos+4
        for _ in range(3):_,loc=a.text(loc,end)
        loc+=6
        _,finish=a.text(loc,end)
        need(finish==end,'工程路径有未识别的额外数据。')
        def text_field(value):
            raw=value.encode('utf8')+bytes.fromhex('00efbbbf')
            return len(raw).to_bytes(4,'big')+raw
        payload=text_field(destination.name)+data[pos:loc]+text_field(media_directory(destination.parent))
        delta=len(payload)-(end-body)
        _,root,last,_=ar.obj(0,len(data))
        kind,mroot,mend,_=ar.obj(root,last)
        need(kind=='MRoot' and root<mroot<=pool_pos<name_at<end<=pend<=mend<=last,'无法定位原生工程路径的包含对象。')
        updates={}
        if delta:
            updates=tail_relocations(a,data,end,body,lambda v:v+delta)
            for s,e in ((root,last),(mroot,mend),(pool,pend),(body,end)):
                updates[s-w]=(w,e-s+delta)
        edited=bytearray(data)
        for at,(size,value) in updates.items():edited[at:at+size]=value.to_bytes(size,'big')
        edited[body:end]=payload
        offset=a.offset
    replace_archive(project,offset,w,len(data),edited)
    with Arrangement(project) as a:
        need(saved_project_folder(a)==destination.parent and physical_files(a)==files,'原生媒体引用回读不一致。')
        list(a.track_rows())
    return dict(native_local_media=0,native_media_from_ace=shared,native_media_verified_against_ace=matched,
                native_external_media=len(files),copied_media_files=0)
