import argparse, datetime, json, os, queue, sys, threading, traceback
from contextlib import contextmanager
from pathlib import Path
from ace_reader import read_saved
from convert_project import VERSION, build_xml, check_source, prepare_media, sha
from ace_writer import convert_to_ace
from archive_reader import Archive

DEFAULT_OUTPUT=Path.home()

def convert(source, parent, log=print, reuse_file=None, intermediate=False):
    source=Path(source).resolve();parent=Path(parent).resolve()
    d,snapshot,digest=read_saved(source,log)
    origin=source.parent/'transfer-map.json'
    if origin.is_file():
        lineage=json.loads(origin.read_text(encoding='utf8'))
        if lineage.get('schema')==2:
            baseline={t['ace_track_uuid']:t for t in lineage['tracks']}
            markers={e['ace_clip_uuid']:e for e in lineage.get('events',[]) if e['source_event'].get('marker_kind')}
            for t in [*d['tracks'],*d.get('markerTracks',[]),*d.get('videoTracks',[])]:
                row=baseline.get(t.get('uuid'))
                if row and t.get('color')==row.get('ace_color','#438bc9'):
                    t['_keep_native_color']=True
                    if 'color' in row:t['_export_color']=row['color']
            for track in d.get('markerTracks',[]):
                for clip in track.get('patterns',[]):
                    link=markers.get(clip['uuid'])
                    if link and link['ace_track_uuid']==track['uuid']:
                        original=link['source_event']
                        if clip.get('color')==link.get('ace_color','#ffc394'):
                            clip['_keep_native_color']=True
                            if 'ace_color' in link:clip['_export_color']=original.get('color')
                        clip['_marker_id']=original.get('marker_id')
                        if original.get('marker_kind')=='point' and clip['clipDur']==link.get('ace_marker_ticks'):
                            clip['_point']=True
            for t in d['tracks']:
                row=baseline.get(t.get('uuid'),{})
                if 'fader_value' in row and abs(row['gain']-t.get('gain',0))<1e-6:
                    t['_source_fader']=row['fader_value']
                    if 'source_gain_db' in row:t['_source_anchor']=row['source_gain_db']
    check_source(d)
    parent.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out=parent/(source.stem+'_'+stamp);out.mkdir()
    reuse=json.loads(Path(reuse_file).read_text(encoding='utf8')) if reuse_file else None
    media,videos=prepare_media(d,source,out,log,reuse,intermediate=intermediate)
    if sha(source)!=digest:raise RuntimeError('转换期间 ACE 文件被重新保存，已停止。请在保存完成后重试。')
    log('正在生成原生可编辑 XML…')
    xml=out/(source.stem+'_ACE直接转换.xml')
    report=build_xml(d,snapshot,media,videos,xml)
    if intermediate:
        from offline_native_writer import make_layout
        report['native_layout']=make_layout(d)
    for warning in report.get('warnings',[]):
        if not (intermediate and warning.startswith('轨道存档按')):
            log('提示：'+warning)
    report.update(source_project=str(source),source_sha256=digest,source_bpm=d['tempos'][0]['bpm'])
    if intermediate:
        generated=[xml]
        companion=xml.with_suffix('.plugins.json')
        if companion.exists():generated.append(companion)
        generated.extend(Path(e['output']) for collection in (media,videos) for e in collection.values()
                         if Path(e['output']).is_relative_to(out) and e['output']!=e['source'])
        report['_intermediate_files']={str(p):sha(p) for p in generated}
        return report
    from project_colors import FACTORY
    palette_note = ''
    if tuple(report['palette']) != FACTORY:
        from native_document import assemble
        from native_events import Arrangement
        from project_colors import native_palette
        board=out/'配色导入底板.npr'
        data=assemble(board,[],[],max(1200.,report['duration_seconds']),report['source_bpm'],report['palette'])
        with board.open('xb') as f:
            f.write(data)
        with Arrangement(board) as saved:
            if list(native_palette(saved))!=report['palette']:
                raise ValueError('XML 配色底板回读不一致。')
        report['palette_project']=str(board)
        palette_note='按 ACE 配色显示时，先打开配色导入底板.npr，再导入此 XML。底板仅包含色盘，时间线在 XML 中。\n'
    (out/'转换结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (out/'导入说明.txt').write_text(
        f'ACE → Nuendo / Cubase {VERSION}\n\n'
        f'导入文件：{xml.name}\n{palette_note}'
        '在 Nuendo 中使用“文件 → 导入 → 轨道存档”。\n'
        '选择全部轨道，使用绝对位置。建议导入空白工程，避免内容重复。\n'
        f'工程设置：48 kHz、24 fps、{d["tempos"][0]["bpm"]:g} BPM、4/4。\n'
        'XML 直接引用 ACE 的原始 WAV 和视频，请保留素材路径。需要转码的媒体位于输出目录。\n'
        '导入时选择引用现有媒体，避免再次复制音频。\n\n'
        f'音频轨 {report["audio_tracks"]}；片段 {report["audio_events"]}；禁用片段 {report["muted_events"]}；'
        f'可编辑淡化 {report["native_fades"]}；视频轨 {report["video_tracks"]}；标记 {(report["range_markers"]+report.get("point_markers",0))}；VST3 插入 {report["vst3_inserts"]}。\n\n'+'\n'.join(report.get('warnings',[])),encoding='utf8')
    log(('轨道数据已生成：' if intermediate else '转换完成：')+str(xml))
    return report

TARGETS = {'Nuendo 工程 (.npr)': 'npr', 'Cubase 工程 (.cpr)': 'cpr', 'Steinberg 轨道存档 (.xml)': 'xml', 'ACE Studio 工程 (.acep)': 'acep'}

@contextmanager
def conversion_slot():
    if os.name != 'nt':
        import fcntl
        from platform_support import user_data
        folder=user_data()
        folder.mkdir(parents=True,exist_ok=True)
        with (folder/'conversion.lock').open('a+b') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('另一个工程正在转换，请等它完成后再开始。') from exc
            yield
        return
    import ctypes
    from ctypes import wintypes as W
    api=ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes=[W.LPVOID,W.BOOL,W.LPCWSTR]
    api.CreateMutexW.restype=W.HANDLE
    api.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
    api.WaitForSingleObject.restype=W.DWORD
    api.ReleaseMutex.argtypes=[W.HANDLE]
    api.CloseHandle.argtypes=[W.HANDLE]
    handle=api.CreateMutexW(None,False,r'Local\ACE-DAW-Transfer-Conversion')
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        state=api.WaitForSingleObject(handle,0)
        if state==258:raise RuntimeError('另一个工程正在转换，请等它完成后再开始。')
        if state not in (0,128):raise ctypes.WinError(ctypes.get_last_error())
        try:yield
        finally:api.ReleaseMutex(handle)
    finally:api.CloseHandle(handle)


@conversion_slot()
def transfer(source, parent, target, log=print, bake_fades=False, static_only=False, reuse_file=None,load_plugins=True):
    from native_projects import PROJECT_TYPES, read_native, write_native
    ext = Path(source).suffix.lower()
    if target == 'acep':
        if ext in PROJECT_TYPES:
            return read_native(source, parent, log, bake_fades, static_only,load_plugins=load_plugins)
        if ext == '.xml':
            return convert_to_ace(source, parent, log, bake_fades, static_only,load_plugins=load_plugins)
    elif ext == '.acep' and target in ('xml', 'npr', 'cpr'):
        report = convert(source, parent, log, reuse_file, intermediate=target != 'xml')
        return report if target == 'xml' else write_native(report, '.' + target, log)
    raise ValueError(f'不支持的转换方向：{ext} → .{target}')

def inspect_source(source, bake_fades=False, static_only=False):
    if Path(source).suffix.lower() in ('.npr', '.cpr'):
        from native_projects import inspect_native
        return inspect_native(source,bake_fades,static_only)
    return Archive(source,bake_fades,static_only).report()

def gui(initial_files=()):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD
    import gui_preferences
    from offline_native_writer import available_native_format
    from native_projects import NativeWriteError
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ACE.DAW.Transfer')
    from ui_language import EN,display
    preferences=gui_preferences.load()
    lang=preferences.get('language','zh')
    def tr(text):return display(str(text),lang)
    def dialog(method,title,message):getattr(messagebox,method)(tr(title),tr(message))
    root=TkinterDnD.Tk();root.title('ACE-DAW 双向迁移 '+VERSION);root.geometry('900x750');root.minsize(800,700)
    if sys.platform=='win32':
        root.iconbitmap(str(Path(__file__).parent/'assets'/'ace-daw-icon.ico'))
    else:
        root.iconphoto(True,tk.PhotoImage(file=str(Path(__file__).parent/'assets'/'ace-daw-icon.png')))
    ui_font='PingFang SC' if sys.platform=='darwin' else 'Microsoft YaHei UI'
    bg='#202328';fg='#e8e9ec';muted='#a8adb6';panel='#2b3038';accent='#8ccbb7'
    root.configure(bg=bg)
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('.',font=(ui_font,10),background=bg,foreground=fg)
    style.configure('TEntry',fieldbackground=panel,foreground=fg,insertcolor=fg,padding=7)
    hover='#36404b'
    style.configure('TCheckbutton',background=bg,foreground=fg,focuscolor=accent,
                    indicatorbackground=panel,indicatorforeground='#17231f',
                    upperbordercolor=muted,lowerbordercolor=muted)
    style.map('TCheckbutton',
              background=[('disabled',bg),('pressed',hover),('active',hover),('focus',bg)],
              foreground=[('disabled',muted),('!disabled',fg)],
              indicatorbackground=[('disabled','selected',muted),('selected',accent),('!selected',panel)])
    style.configure('TCombobox',foreground=fg,background=panel,fieldbackground=panel,
                    arrowcolor=fg,bordercolor=muted,lightcolor=panel,darkcolor=panel,padding=4)
    style.map('TCombobox',fieldbackground=[('disabled',bg),('readonly',panel)],
              foreground=[('disabled',muted),('!disabled',fg)],
              background=[('disabled',bg),('pressed',hover),('active',hover)],
              bordercolor=[('focus',accent)],arrowcolor=[('disabled',muted),('!disabled',fg)],
              selectbackground=[('focus',hover),('!focus',panel)],
              selectforeground=[('disabled',muted),('!disabled',fg)])
    for option,color in [('background',panel),('foreground',fg),('selectBackground',accent),('selectForeground','#17231f')]:
        root.option_add('*TCombobox*Listbox.'+option,color)
    style.configure('TButton',background=panel,foreground=fg,padding=(14,8),borderwidth=0)
    style.map('TButton',background=[('active','#3d4652')])
    style.configure('Accent.TButton',background=accent,foreground='#17231f',font=(ui_font,11,'bold'))
    style.map('Accent.TButton',background=[('active','#a9e4d0'),('disabled','#4e625b')])
    frame=ttk.Frame(root,padding=26);frame.pack(fill='both',expand=True);frame.columnconfigure(0,weight=1)
    ttk.Label(frame,text='在 ACE 与 DAW 之间继续剪辑',font=(ui_font,21,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
    ttk.Label(frame,text='独立片段 · 绝对时间 · 电平与静音 · 默认 S 形可编辑淡化',foreground=muted).grid(row=1,column=0,columnspan=2,sticky='w',pady=(5,18))
    source=tk.StringVar();output=tk.StringVar(value=str(DEFAULT_OUTPUT));status=tk.StringVar(value='就绪 · 读取已保存的工程')
    status_message='就绪 · 读取已保存的工程'
    def set_status(message):
        nonlocal status_message
        status_message=message
        status.set(tr(message))
    target_labels={code:label for label,code in TARGETS.items()}
    formats=tuple(TARGETS.values())
    labels_to_codes=dict(TARGETS)
    labels_to_codes.update({EN[label]:code for label,code in TARGETS.items()})
    def format_code():return labels_to_codes[target.get()]
    target=tk.StringVar(value=tr(target_labels[preferences.get('output_format','xml')]))
    bake=tk.BooleanVar(value=preferences['bake_fades'])
    static=tk.BooleanVar(value=preferences['static_only'])
    presets=tk.BooleanVar(value=preferences['presets_only'])
    def remember(name,value):
        preferences[name]=value
        try:gui_preferences.save(name,value)
        except OSError as exc:set_status('本次选择已生效，偏好保存失败：'+str(exc))
    for key,variable in [('bake_fades',bake),('static_only',static),('presets_only',presets)]:
        variable.trace_add('write',lambda *_,k=key,v=variable:remember(k,v.get()))
    targetbar=ttk.Frame(frame);targetbar.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,16));targetbar.columnconfigure(1,weight=1)
    ttk.Label(targetbar,text='输出格式').grid(row=0,column=0,padx=(0,12))
    choice=ttk.Combobox(targetbar,textvariable=target,values=[tr(label) for label in TARGETS],state='readonly');choice.grid(row=0,column=1,sticky='ew')
    choice.bind('<<ComboboxSelected>>',lambda _:remember('output_format',format_code()))
    ttk.Label(frame,text='源工程或轨道存档（可直接拖入窗口）').grid(row=3,column=0,sticky='w',pady=(0,6))
    e1=ttk.Entry(frame,textvariable=source);e1.grid(row=4,column=0,sticky='ew',padx=(0,10))
    state={'running':False,'folder':None,'source':None}

    def select_source(paths):
        nonlocal formats
        if state['running']:
            return False
        if len(paths) != 1:
            set_status('请一次选择一个工程文件')
            return False
        project = Path(paths[0]).absolute()
        if project.suffix.lower() not in ('.acep', '.cpr', '.npr', '.xml') or not project.is_file():
            set_status('请选择 .cpr、.npr、.acep 或 .xml 文件')
            return False
        source.set(str(project))
        state['source']=project
        output.set(str(project.parent))
        if project.suffix.lower() == '.acep':
            returning=available_native_format(project)
            formats=tuple(dict.fromkeys([returning or 'xml','npr','cpr','xml']))
        else:
            formats=('acep',)
        choice.configure(values=[tr(target_labels[code]) for code in formats])
        preferred=preferences.get('output_format')
        target.set(tr(target_labels[preferred if preferred in formats else formats[0]]))
        set_status('已选择工程 · 可开始转换')
        return True

    def apply_typed_source(event=None):
        text=source.get().strip().strip('"')
        if text and Path(text).absolute()!=state['source']:
            return select_source([text])
        return bool(text)
    e1.bind('<FocusOut>',apply_typed_source)
    e1.bind('<Return>',apply_typed_source)

    def browse_source():
        p=filedialog.askopenfilename(title=tr('选择转换源文件'),filetypes=[(tr(name),ext) for name,ext in [('工程与轨道存档','*.acep *.npr *.cpr *.xml'),('Nuendo 工程','*.npr'),('Cubase 工程','*.cpr'),('ACE 工程','*.acep'),('Steinberg 轨道存档','*.xml')]])
        if p:
            select_source([p])

    def drop_source(event):
        paths = root.tk.splitlist(event.data)
        return COPY if select_source(paths) else REFUSE_DROP

    root.drop_target_register(DND_FILES)
    root.dnd_bind('<<DropEnter>>', lambda event: REFUSE_DROP if state['running'] else COPY)
    root.dnd_bind('<<Drop>>', drop_source)
    b1=ttk.Button(frame,text='选择文件…',command=browse_source);b1.grid(row=4,column=1,sticky='ew')
    ttk.Label(frame,text='输出位置').grid(row=5,column=0,sticky='w',pady=(17,6))
    e2=ttk.Entry(frame,textvariable=output);e2.grid(row=6,column=0,sticky='ew',padx=(0,10))
    def browse_output():
        current=Path(output.get())
        p=filedialog.askdirectory(title=tr('选择输出目录'),initialdir=str(current if current.is_dir() else DEFAULT_OUTPUT.parent))
        if p:output.set(p)
    b2=ttk.Button(frame,text='选择目录…',command=browse_output);b2.grid(row=6,column=1,sticky='ew')
    help_text=tk.StringVar()
    def describe_format(*_):
        fmt=format_code()
        note={
            'npr':'NPR 可直接在 Nuendo 打开，含配乐轨道和剪辑。',
            'cpr':'CPR 可直接在 Cubase 打开，含配乐轨道和剪辑。',
            'xml':'XML 用于把配乐轨道导入已有工程。含自定义颜色时，可在随附的配色底板中导入。',
            'acep':'ACEP 可直接在 ACE Studio 打开。原始 WAV 和视频保留在现有位置。'
        }[fmt]
        note=tr(note)
        if fmt in ('cpr','npr'):
            note+=tr(' 新建工程内含色盘；写回已有来源时按其色盘取色。')
        help_text.set(note)
    target.trace_add('write',describe_format)
    describe_format()
    ttk.Label(targetbar,textvariable=help_text,foreground=muted,wraplength=660,justify='left').grid(row=1,column=1,sticky='w',pady=(5,0))
    ttk.Label(frame,text='CPR / NPR 可直接打开。XML 可将轨道加入已有工程，导入时使用目标工程的色盘。',foreground=muted).grid(row=7,column=0,columnspan=2,sticky='w',pady=(14,10))
    bakebox=ttk.Checkbutton(frame,text='反向转换：将非默认淡化写入独立音频（这些淡化不再可编辑）',variable=bake)
    bakebox.grid(row=8,column=0,columnspan=2,sticky='w',pady=(0,14))
    staticbox=ttk.Checkbutton(frame,text='只迁移可用的静态混音和剪辑（自动化、不兼容处理等列入报告，声音可能改变）',variable=static)
    staticbox.grid(row=9,column=0,columnspan=2,sticky='w',pady=(0,14))
    presetbox=ttk.Checkbutton(frame,text='仅保存可提取的 VST3 预置，打开 ACE 工程时只载入剪辑和电平',variable=presets)
    presetbox.grid(row=10,column=0,columnspan=2,sticky='w',pady=(0,10))
    row=ttk.Frame(frame);row.grid(row=11,column=0,columnspan=2,sticky='ew');row.columnconfigure(2,weight=1)
    q=queue.Queue()
    def start():
        if state['running']:return
        if not source.get().strip():
            dialog('showerror','请选择文件','请选择已保存的 .acep、.npr、.cpr 或轨道存档 .xml。')
            return
        if not apply_typed_source():return
        if not Path(source.get()).is_file():dialog('showerror','请选择文件','请选择已保存的 .acep、.npr、.cpr 或轨道存档 .xml。');return
        src=source.get();dst=output.get();fmt=format_code();baking=bake.get();statics=static.get();load_plugins=not presets.get()
        allowed=('.xml','.cpr','.npr') if fmt=='acep' else ('.acep',)
        if Path(src).suffix.lower() not in allowed:dialog('showerror','方向不匹配','此输出格式需要以下源文件：'+' / '.join(allowed));return
        state['running']=True
        for w in [e1,e2,b1,b2,go,choice,bakebox,staticbox,presetbox,check_button]:w.configure(state='disabled')
        set_status('正在转换…');clear_log()
        def worker():
            try:
                logger=lambda s:q.put(('log',s))
                q.put(('done',transfer(src,dst,fmt,logger,baking,statics,load_plugins=load_plugins)))
            except NativeWriteError as e:q.put(('xml_fallback',(e.xml,str(e))))
            except Exception as e:q.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    go=ttk.Button(row,text='开始转换',style='Accent.TButton',command=start);go.grid(row=0,column=0,sticky='w')
    def inspect():
        if Path(source.get()).suffix.lower() not in ('.xml','.cpr','.npr'):dialog('showinfo','检查源工程','请选择 .cpr、.npr 或轨道存档 .xml。');return
        try:
            report=inspect_source(source.get(),bake.get(),static.get())
            clear_log();append_log(f'音轨 {report["audio_tracks"]} · 音频块 {report["audio_events"]} · 视频 {report["video_events"]} · 标记 {(report["range_markers"]+report.get("point_markers",0))}\n\n')
            append_log('\n'.join(report['errors']+report['warnings']) or '检查通过，可以开始转换。')
            set_status('检查通过' if report['ready'] else '存在不支持内容')
        except Exception as e:dialog('showerror','检查未完成',str(e))
    check_button=ttk.Button(row,text='检查源工程',command=inspect);check_button.grid(row=0,column=1,padx=(8,0))
    ttk.Label(row,textvariable=status,foreground=muted).grid(row=0,column=2,sticky='w',padx=12)
    def open_output():
        if state['folder']:
            from platform_support import open_directory
            open_directory(state['folder'])
    ob=ttk.Button(row,text='打开结果目录',command=open_output,state='disabled');ob.grid(row=0,column=3)
    logbox=tk.Text(frame,height=11,bg='#171a1e',fg=fg,insertbackground=fg,relief='flat',font=(ui_font,9),padx=12,pady=10,wrap='word')
    logbox.grid(row=12,column=0,columnspan=2,sticky='nsew',pady=(18,0));frame.rowconfigure(12,weight=1)
    log_lines=[]
    def clear_log():
        log_lines.clear()
        logbox.delete('1.0','end')
    def append_log(message):
        log_lines.append(message)
        logbox.insert('end',tr(message))
    localized=[]
    def register(parent):
        for child in parent.winfo_children():
            if isinstance(child,(ttk.Label,ttk.Button,ttk.Checkbutton)):
                original=str(child.cget('text'))
                if original in EN:localized.append((child,original))
            register(child)
    register(frame)
    language=tk.StringVar(value='English' if lang=='en' else '中文')
    picker=ttk.Combobox(frame,name='language',values=('中文','English'),textvariable=language,
                        state='readonly',width=10)
    picker.grid(row=0,column=1,sticky='ne')
    def apply_language(event=None):
        nonlocal lang
        selected=format_code()
        lang='en' if language.get()=='English' else 'zh'
        if event is not None:remember('language',lang)
        root.title(('ACE–DAW Transfer ' if lang=='en' else 'ACE-DAW 双向迁移 ')+VERSION)
        for widget,original in localized:widget.configure(text=tr(original))
        choice.configure(values=[tr(target_labels[c]) for c in formats])
        target.set(tr(target_labels[selected]))
        set_status(status_message)
        logbox.delete('1.0','end')
        for line in log_lines:logbox.insert('end',tr(line))
        logbox.see('end')
    picker.bind('<<ComboboxSelected>>',apply_language)
    apply_language()
    def poll():
        try:
            while True:
                kind,value=q.get_nowait()
                if kind=='log':append_log(value+'\n');logbox.see('end')
                else:
                    state['running']=False
                    for w in [e1,e2,b1,b2,go,bakebox,staticbox,presetbox,check_button]:w.configure(state='normal')
                    choice.configure(state='readonly')
                    if kind=='done':
                        state['folder']=value.get('output_directory') or str(Path(value['xml']).parent);ob.configure(state='normal')
                        set_status(f'完成 · {value["audio_tracks"]} 条音轨 / {value["audio_events"]} 个片段')
                    elif kind=='xml_fallback':
                        xml,reason=value
                        state['folder']=str(xml.parent);ob.configure(state='normal')
                        set_status('原生工程未生成 · 可导入轨道 XML')
                        append_log('\n'+reason+'\n');logbox.see('end')
                        dialog('showinfo','轨道 XML 已就绪','请用“打开结果目录”找到 '+xml.name+'，在 DAW 中导入轨道存档。\n\n'+reason.splitlines()[0])
                    else:
                        set_status('未完成 · 请查看日志')
                        append_log('\n'+value+'\n')
                        preview=value if len(value)<650 else value[:620]+'…\n完整原因见窗口日志。'
                        dialog('showerror','转换未完成',preview)
        except queue.Empty:pass
        root.after(150,poll)
    def close():
        if state['running']:dialog('showinfo','正在转换','请等待转换结束，避免留下不完整文件。');return
        root.destroy()
    if initial_files:
        select_source(initial_files)
    root.protocol('WM_DELETE_WINDOW',close);poll();root.mainloop()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source');parser.add_argument('--output');parser.add_argument('--reuse-media-map');parser.add_argument('--result-file');parser.add_argument('--check-runtime',action='store_true')
    parser.add_argument('--direction',choices=['auto','to-ace','to-steinberg'],default='auto');parser.add_argument('--bake-fades',action='store_true');parser.add_argument('--check-source',action='store_true')
    parser.add_argument('--static-only',action='store_true');parser.add_argument('--presets-only',action='store_true')
    parser.add_argument('--format',choices=['xml','npr','cpr','acep'],help='输出格式；未指定时沿用原命令行默认值')
    parser.add_argument('files', nargs='*', metavar='工程文件', help='在图形界面中打开源文件')
    args=parser.parse_args()
    if args.files and (args.source or args.check_runtime):
        parser.error('图形界面的工程文件不能与 --source 或 --check-runtime 同时使用')
    if args.check_runtime:
        import zstandard,cbor2,soundfile,numpy,tkinter
        result={'version':VERSION,'runtime':'ok','ace_access':'direct_file','zstandard':zstandard.__version__}
        if args.result_file:Path(args.result_file).write_text(json.dumps(result),encoding='utf8')
        print(json.dumps(result))
    elif args.source:
        try:
            if args.check_source:report=inspect_source(args.source,args.bake_fades,args.static_only)
            else:
                reverse=args.direction=='to-ace' or (args.direction=='auto' and Path(args.source).suffix.lower() in ('.xml','.npr','.cpr'))
                fmt=args.format or ('acep' if reverse else 'xml')
                if args.direction!='auto' and ((fmt=='acep') != reverse):raise ValueError('命令行 --direction 与 --format 不匹配。')
                report=transfer(args.source,args.output or Path(args.source).resolve().parent,fmt,bake_fades=args.bake_fades,static_only=args.static_only,reuse_file=args.reuse_media_map,load_plugins=not args.presets_only)
            if args.result_file:Path(args.result_file).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps(report,ensure_ascii=False))
        except Exception as e:
            if args.result_file:Path(args.result_file).write_text(json.dumps({'error':str(e)},ensure_ascii=False),encoding='utf8')
            if sys.stderr:print(str(e),file=sys.stderr)
            sys.exit(1)
    else:gui(args.files)
