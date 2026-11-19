"""Translate saved JUCE VST3 component/controller blocks without loading a plugin."""
import base64
import json
import os
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from plugin_state import cid, envelope, from_envelope, make_preset, parse_preset, state_summary


def reverse_bits(number, width):
    return int(format(number, f'0{width}b')[::-1], 2)


_REVERSE_BYTE = bytes(reverse_bits(i, 8) for i in range(256))
_BASE64 = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_JUCE = b'.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+'
_SYMBOLS = bytes(_JUCE[reverse_bits(i, 6)] for i in range(64))
_ENC = bytes.maketrans(_BASE64, _SYMBOLS)
_DEC = bytes.maketrans(_SYMBOLS, _BASE64)
_FIELDS = {b'Comp': 'IComponent', b'Cont': 'IEditController'}


def encode_block(data):
    encoded = base64.b64encode(data.translate(_REVERSE_BYTE)).rstrip(b'=').translate(_ENC)
    return str(len(data)) + '.' + encoded.decode('ascii')


def decode_block(text):
    size, encoded = text.split('.', 1)
    if not size.isdecimal() or len(encoded) != (int(size)*8+5)//6 or any(ord(c) not in _JUCE for c in encoded):
        raise ValueError('ACE 插件状态块编码不完整。')
    standard = encoded.encode('ascii').translate(_DEC)
    return base64.b64decode(standard + b'='*(-len(standard)%4), validate=True).translate(_REVERSE_BYTE)


def plugin_key(file):
    value = str(file).replace('\\', '/').casefold()
    return value.partition('.vst3/contents/')[0] + ('.vst3' if '.vst3/contents/' in value else '')


def juce_hash(text):
    value = 0
    for char in text:
        value = (31 * value + ord(char)) & 0xffffffff
    return format(value, 'x')


def catalog():
    ace_cache = Path(os.environ.get('LOCALAPPDATA',''))/'timedomain'/'ACE Studio'/'plugin_scan_cache.xml'
    steinberg = Path(os.environ.get('APPDATA',''))/'Steinberg'
    if sys.platform == 'darwin':
        library=Path.home()/'Library'
        cache_roots=[library/'Application Support', library/'Caches', library/'Preferences']
        candidates=[root/vendor/'ACE Studio'/'plugin_scan_cache.xml'
                    for root in cache_roots for vendor in ('timedomain','Time Domain','')]
        ace_cache=next((p for p in candidates if p.is_file()),candidates[0])
        steinberg=library/'Preferences'
    classes = {}
    for cache in steinberg.glob('*/*VST3 Cache/vst3plugins.xml'):
        try:
            nodes = ET.parse(cache).getroot().findall('plugin')
        except (OSError, ET.ParseError):
            continue
        for plugin in nodes:
            for cls in plugin.findall('class'):
                if cls.findtext('category') == 'Audio Module Class':
                    key = (plugin_key(plugin.findtext('path')), cls.findtext('name'))
                    classes.setdefault(key,set()).add(cls.findtext('cid'))
    if not ace_cache.is_file():
        return []
    result = []
    for row in ET.parse(ace_cache).getroot().findall('PLUGIN'):
        if row.get('format') != 'VST3':
            continue
        name, file = row.get('name'), row.get('file')
        if not file or not name or not row.get('uniqueId'):
            continue
        identities = set(classes.get((plugin_key(file), name), ()))
        module = Path(file)/'Contents'/'Resources'/'moduleinfo.json'
        if not identities and module.is_file():
            try:
                info = json.loads(module.read_text(encoding='utf-8-sig'))
                identities = {c['CID'] for c in info['Classes'] if c.get('Name') == name and c.get('Category') == 'Audio Module Class'}
            except (OSError, KeyError, ValueError):
                continue
        if len(identities) != 1:
            continue
        value = dict(cid=cid(identities.pop()), name=name, vendor=row.get('manufacturer',''),
                     version=row.get('version',''), typeId=f'VST3-{name}-{juce_hash(file)}-{row.get("uniqueId")}')
        result.append(value)
    return result


def matching(entries, field, value):
    rows = [row for row in entries if row[field] == value]
    if len(rows) != 1:
        raise ValueError(f'本机扫描记录无法唯一核对 VST3 身份 {value}。')
    return rows[0]


def make_insert(plugin, entries, number):
    ident, chunks = parse_preset(from_envelope(plugin['preset']))
    if ident != plugin['cid'] or set(chunks)-{b'Comp',b'Cont',b'Info'}:
        raise ValueError('VST3 状态与组件身份不符，或含未支持的状态块。')
    desc = matching(entries, 'cid', ident)
    node = ET.Element('VST3PluginState')
    for key, tag in _FIELDS.items():
        if key in chunks:
            ET.SubElement(node, tag).text = encode_block(chunks[key])
    xml = b'<?xml version="1.0" encoding="UTF-8"?> ' + ET.tostring(node, encoding='utf-8')
    return dict(instanceId=f'ace_plugin_{number:016x}', typeId=desc['typeId'], displayName=plugin['name'],
                pluginName=desc['name'], pluginVersion=desc['version'], manufacturer=desc['vendor'],
                enabled=plugin['enabled'], bypassed=plugin['bypassed'], kind=0, pluginFormatName='VST3',
                stateBlob=b'VC2!'+struct.pack('<I',len(xml))+xml+b'\0', cachedLatencySamples=0,
                editorOpen=False, editorWidth=0, editorHeight=0)


def read_insert(insert, entries, slot):
    if insert.get('pluginFormatName') != 'VST3':
        raise ValueError('此效果器不是可离线迁移的 VST3：'+str(insert.get('displayName')))
    desc = matching(entries, 'typeId', insert['typeId'])
    blob = insert['stateBlob']
    if blob[:4] != b'VC2!' or len(blob) < 8:
        raise ValueError('未识别的 VST3 静态状态容器。')
    size, = struct.unpack_from('<I', blob, 4)
    if size > len(blob)-8 or any(blob[8+size:]):
        raise ValueError('VST3 状态长度不一致。')
    node = ET.fromstring(blob[8:8+size])
    if node.tag != 'VST3PluginState' or any(x.tag not in _FIELDS.values() for x in node):
        raise ValueError('未支持的 VST3 状态字段。')
    chunks = {k:decode_block(node.findtext(t)) for k,t in _FIELDS.items() if node.find(t) is not None}
    preset = make_preset(desc['cid'], chunks)
    return dict(**state_summary(preset), name=insert['displayName'], plugin_name=desc['name'],
                vendor=desc['vendor'], typeId=desc['typeId'], slot=str(slot),enabled=insert['enabled'],
                bypassed=insert['bypassed'], preset=envelope(preset))
