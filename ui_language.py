"""English UI messages. Filenames, track names and diagnostic details stay intact."""
import re

EN = {
    '在 ACE 与 DAW 之间继续剪辑':'ACE Studio & DAW Project Transfer',
    '独立片段 · 绝对时间 · 电平与静音 · 默认 S 形可编辑淡化':'Editable clips and S-curves · Absolute timing · Levels and mute states',
    '输出格式':'Output format', '源工程或轨道存档（可直接拖入窗口）':'Source project or track archive (drop a file here)',
    '输出位置':'Output folder', '选择文件…':'Browse file…', '选择目录…':'Browse folder…',
    '开始转换':'Convert', '检查源工程':'Inspect source', '打开结果目录':'Open output folder',
    'Nuendo 工程 (.npr)':'Nuendo project (.npr)', 'Cubase 工程 (.cpr)':'Cubase project (.cpr)',
    'Steinberg 轨道存档 (.xml)':'Steinberg track archive (.xml)', 'ACE Studio 工程 (.acep)':'ACE Studio project (.acep)',
    'NPR 可直接在 Nuendo 打开，含配乐轨道和剪辑。':'Open the NPR directly in Nuendo to edit the score.',
    'CPR 可直接在 Cubase 打开，含配乐轨道和剪辑。':'Open the CPR directly in Cubase to edit the score.',
    ' 新建工程内含色盘；写回已有来源时按其色盘取色。':' New projects embed a palette. Writeback uses the source palette.',
    'XML 用于把配乐轨道导入已有工程。含自定义颜色时，可在随附的配色底板中导入。':'Import the XML into an existing project. For custom colors, import it into the supplied palette project.',
    'ACEP 可直接在 ACE Studio 打开。原始 WAV 和视频保留在现有位置。':'Open the ACEP in ACE Studio. The project references your existing WAV and video files.',
    'CPR / NPR 可直接打开。XML 可将轨道加入已有工程，导入时使用目标工程的色盘。':'CPR and NPR open directly. XML adds tracks to a project and uses its color palette.',
    '反向转换：将非默认淡化写入独立音频（这些淡化不再可编辑）':'DAW to ACE: render non-default fades into audio files (those fades become fixed)',
    '只迁移可用的静态混音和剪辑（自动化、不兼容处理等列入报告，声音可能改变）':'Transfer compatible static mix settings and edits; report unsupported processing and automation',
    '仅保存可提取的 VST3 预置，打开 ACE 工程时只载入剪辑和电平':'Save recoverable VST3 presets separately; load only clips and levels in ACE',
    '就绪 · 读取已保存的工程':'Ready · Reads saved projects', '已选择工程 · 可开始转换':'Project selected · Ready to convert',
    '请一次选择一个工程文件':'Select one project at a time.', '请选择 .cpr、.npr、.acep 或 .xml 文件':'Select a .cpr, .npr, .acep or .xml file.',
    '选择转换源文件':'Choose a source project', '选择输出目录':'Choose an output folder', '工程与轨道存档':'Projects and track archives',
    'Nuendo 工程':'Nuendo projects','Cubase 工程':'Cubase projects','ACE 工程':'ACE projects','Steinberg 轨道存档':'Steinberg track archives',
    '请选择文件':'Select a file','请选择已保存的 .acep、.npr、.cpr 或轨道存档 .xml。':'Select a saved ACEP, NPR, CPR or track archive XML.',
    '请选择 .cpr、.npr 或轨道存档 .xml。':'Select a CPR, NPR or track archive XML.', '方向不匹配':'Incompatible source format',
    '正在转换…':'Converting…', '正在转换':'Conversion in progress','请等待转换结束，避免留下不完整文件。':'Wait for the conversion to finish before closing.',
    '检查通过，可以开始转换。':'Source checked and ready to convert.', '检查通过':'Source checked','存在不支持内容':'Unsupported content found',
    '检查未完成':'Could not inspect source', '转换未完成':'Conversion incomplete', '未完成 · 请查看日志':'Incomplete · See the log',
    '原生工程未生成 · 可导入轨道 XML':'Native project unavailable · Track archive available',
    '轨道 XML 已就绪':'Track archive available','完整原因见窗口日志。':'See the log for details.',
    '正在离线读取已保存的 ACE 工程…':'Reading the saved ACE project…','正在生成原生可编辑 XML…':'Writing the editable track archive…',
    '正在建立原生轨道、片段、媒体池和调色板…':'Writing tracks, events, the media pool and the project palette…',
    '另一个工程正在转换，请等它完成后再开始。':'Another conversion is running. Wait for it to finish.',
    '存在未渲染的 MIDI/歌声/乐器轨，请先转换成音频。':'Render MIDI, singing and instrument tracks to audio first.',
    '存在尚未支持的非默认 S 形淡化。':'The project contains a non-default S-curve.',
    '轨道存档按目标工程的调色板显示。要使用随附色盘，请在配色底板中导入 XML。':'The destination controls XML colors. Import into the supplied palette project to use its colors.',
    'VST3 传递完整状态；外部采样/脉冲文件、许可证、插件版本差异需目标宿主自行满足。':'VST3 state is included. The receiving host still needs compatible plug-ins, licenses and external assets.',
    '当前版本只支持固定速度。':'This version requires a constant tempo.',
    '当前版本只支持 4/4。':'This version requires a 4/4 time signature.',
    'XML 没有速度表；以 120 BPM 建立 ACE，音频保持绝对秒位置。':'No tempo map in the XML. ACE will use 120 BPM; audio positions remain in absolute time.',
    '当前版本要求项目采样率 48 kHz。':'Set the project sample rate to 48 kHz.',
    '含视频的工程仅支持已校准的 24 fps 时间基。':'Video projects require a 24 fps timebase.',
    '读取已保存的轨道存档内容；不迁移主输出处理和音频设备路由。':'Reading saved tracks. The archive does not carry master processing or hardware routing.',
    '没有可迁移的音频、视频或范围标记。':'No compatible audio, video or marker tracks found.',
}
PATTERNS = [
    (r'完成 · (\d+) 条音轨 / (\d+) 个片段',r'Complete · \1 audio tracks / \2 clips'),
    (r'音轨 (\d+) · 音频块 (\d+) · 视频 (\d+) · 标记 (\d+)',r'Audio tracks: \1 · Audio clips: \2 · Video: \3 · Markers: \4'),
    (r'正在直接读取 (.+) 中保存的轨道、媒体和混音设置…',r'Reading tracks, media and mix data from \1…'),
    (r'正在离线生成 (.+) 工程…',r'Creating the \1 project…'),
    (r'已回读 (\d+) 条轨道和 (\d+) 个片段及标记。',r'Verified \1 tracks and \2 events and markers.'),
    (r'引用素材 (\d+/\d+)：(.+)',r'Referencing media \1: \2'),
    (r'准备音频 (\d+/\d+)：(.+)',r'Preparing audio \1: \2'),
    (r'复用音频 (\d+/\d+)：(.+)',r'Reusing audio \1: \2'),
    (r'文件回读通过：(\d+) 个音频块、(\d+) 个标记、(\d+) 个工程内 VST3 插入。',r'Verified \1 clips, \2 markers and \3 VST3 inserts.'),
    (r'已单独保存 (\d+) 个总线插入预置。',r'Saved \1 bus insert presets.'),
    (r'已离线回读 (\d+) 条轨道、(\d+) 个片段/标记；已改动 (\d+) 个片段/标记。',r'Verified \1 tracks and \2 events/markers; updated \3.'),
    (r'请用“打开结果目录”找到 (.+)，在 DAW 中导入轨道存档。',r'Use Open output folder to locate \1, then import it as a track archive in your DAW.'),
]
PREFIX = {'此输出格式需要以下源文件：':'This format requires ', '本次选择已生效，偏好保存失败：':'Applied for this session. Could not save preferences: ',
          '工程已保存：':'Project saved: ', '转换完成：':'Conversion complete: ','轨道数据已生成：':'Track data saved: ',
          '缺少音频：':'Missing audio: ','缺少视频：':'Missing video: ','媒体缺失或重定位不唯一：':'Missing or ambiguous media: ',
          '已生成的轨道 XML 位于 ':'Track archive saved to ', '片段超出源音频范围：':'Clip extends beyond the source audio: '}


def display(text, language):
    if language!='en' or not text:
        return text
    if text in EN:
        return EN[text]
    if '\n' in text:
        return '\n'.join(display(line, language) for line in text.split('\n'))
    if text.startswith('提示：'):
        return 'Note: '+display(text[3:],language)
    for source,translated in PREFIX.items():
        if text.startswith(source):
            return translated+text[len(source):]
    for pattern,translated in PATTERNS:
        m=re.fullmatch(pattern,text)
        if m:
            return m.expand(translated)
    return text

