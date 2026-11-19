"""Convert native Steinberg documents using their saved contents."""
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from convert_project import sha


PROJECT_TYPES = {'.npr': 'Nuendo', '.cpr': 'Cubase'}


class TrackSelectionMismatch(ValueError):
    """The host exported a different selection from the saved arrangement."""


class NativeWriteError(ValueError):
    def __init__(self,reason,xml):
        self.xml=xml
        super().__init__(f'{reason}\n已生成的轨道 XML 位于 {xml}')


def read_header(path):
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(4096)
    width = {b'RIFF': 4, b'RIF2': 8}.get(header[:4])
    if not width or header[4 + width:12 + width] != b'NUNDROOT':
        raise ValueError(f'工程文件头无效：{path}')
    if int.from_bytes(header[4:4 + width], 'big') + width + 8 != path.stat().st_size:
        raise ValueError(f'工程长度和保存的文件头不一致：{path}')
    return header


def record(path, contents):
    path.write_text(json.dumps(contents, indent=2, ensure_ascii=False), encoding='utf-8')


def inspect_native(source, bake_fades=False, static_only=False):
    from native_input import NativeInput
    return NativeInput(source, bake_fades, static_only).report()


def confirm_saved_tracks(archive, project):
    from native_inventory import read_track_inventory, read_event_counts
    kinds = {'MAudioTrackEvent', 'MVideoTrackEvent', 'MMarkerTrackEvent'}
    root = ET.parse(archive).getroot()
    objects = {e.get('ID'): e for e in root.iter('obj') if e.get('class') and e.get('ID')}
    expected = Counter()
    for track in objects.values():
        if track.get('class') not in kinds:
            continue
        node = track.find('obj[@name="Node"]')
        if node is not None and not node.get('class'):
            node = objects.get(node.get('ID'))
        name = node.find('string[@name="Name"]') if node is not None else None
        if name is None:
            raise ValueError('源轨道没有可核对的名称。')
        expected[(track.get('class'), name.get('value'))] += 1
    saved = Counter((kind, name) for kind, name in read_track_inventory(project) if kind in kinds)
    if not expected or expected != saved:
        raise TrackSelectionMismatch(f'原生工程保存后的轨道与源轨道不一致。缺少 {dict(expected-saved)}，额外 {dict(saved-expected)}。')
    event_types = {'MAudioEvent': 'audio_events', 'MVideoEvent': 'video_events',
                   'MRangeMarkerEvent': 'range_markers'}
    expected_events = dict.fromkeys(event_types.values(), 0)
    for obj in objects.values():
        kind = event_types.get(obj.get('class'))
        if kind:
            expected_events[kind] += 1
    saved_events = read_event_counts(project)
    if expected_events != saved_events:
        raise ValueError(f'原生工程与轨道存档的事件数量不同。工程 {saved_events}，轨道存档 {expected_events}。')
    return sum(expected.values())


def write_native(xml_report, suffix, log):
    xml = Path(xml_report['xml']).resolve()
    destination = xml.with_suffix(suffix)
    if destination.exists():
        raise FileExistsError(f'工程文件已经存在：{destination}')
    result = dict(xml_report, status='in_progress', requested_format=suffix, output_directory=str(xml.parent))
    status = xml.parent / '转换结果.json'
    record(status, result)
    log(f'正在离线生成 {suffix} 工程…')
    try:
        from offline_native_writer import write_project
        if not xml_report.get('source_project') or 'native_layout' not in xml_report:
            raise ValueError('缺少已保存的 ACE 来源与轨道身份，无法写回原生工程。')
        if not xml_report.get('source_bpm') or not xml_report.get('palette'):
            raise ValueError('缺少 ACE 工程速度或调色板信息。')
        from offline_native_writer import available_native_format
        from native_export import create_project
        source=Path(xml_report['source_project'])
        verified=None
        if available_native_format(source)==suffix[1:]:
            try:
                verified=write_project(xml,destination,log=log,source_bpm=xml_report['source_bpm'],
                                       source_ace=source,layout=xml_report['native_layout'])
            except ValueError as exc:
                note='原生来源无法原位写回，已按 ACE 中保存的轨道新建工程。'
                log(note+' 原因：'+str(exc).split('。')[0])
                result.setdefault('warnings',[]).append(note)
        if verified is None:
            log('正在建立原生轨道、片段、媒体池和调色板…')
            verified=create_project(xml,destination,xml_report)
            log(f'已回读 {verified["matched_saved_tracks"]} 条轨道和 {verified["matched_saved_events"]} 个片段及标记。')
        header = read_header(destination)
        if PROJECT_TYPES[suffix].encode('ascii') + b'\0' not in header[:1024]:
            raise ValueError('保存工程的宿主和选择的输出格式不一致。')
        result.update(verified,status='complete', project=str(destination), project_sha256=sha(destination))
        # Only discard files created for this specific intermediate export.
        owned=result.pop('_intermediate_files',{})
        for name,digest in owned.items():
            extra=Path(name).resolve()
            if (verified.get('native_write_mode')=='new_arrangement' and extra.suffix.lower() not in ('.xml','.json')):
                continue
            if extra.is_relative_to(xml.parent) and extra.is_file() and sha(extra)==digest:
                extra.unlink()
        result.pop('native_layout',None)
        if not xml.exists():
            result.pop('xml',None)
            result.pop('xml_sha256',None)
        result['warnings']=[x for x in result.get('warnings',[]) if not x.startswith(('轨道存档按','轨道存档 XML 的主输出'))]
        if verified.get('native_pool_warning'):result['warnings'].append(verified['native_pool_warning'])
        if verified.get('native_approximate_colors'):
            message=f"有 {verified['native_approximate_colors']} 种更改后的颜色用了原工程的相近色。"
            result['warnings'].append(message)
            log(message)
        record(status, result)
        log(f'工程已保存：{destination}')
        return result
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        record(status, result)
        if xml.is_file():raise NativeWriteError(str(exc),xml) from exc
        raise


def read_native(source, parent, log, bake_fades, static_only,load_plugins=True):
    from ace_writer import convert_to_ace
    from native_input import NativeInput
    source = Path(source).resolve()
    read_header(source)
    log(f'正在直接读取 {source.name} 中保存的轨道、媒体和混音设置…')
    archive = NativeInput(source, bake_fades, static_only)
    return convert_to_ace(source, parent, log, bake_fades, static_only, archive=archive,load_plugins=load_plugins)
