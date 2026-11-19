"""Opaque VST3 state transport. Plugin identity is the full class CID, never a name.

No vendor-specific parameter layouts are decoded or rewritten. The preset container
layout follows Steinberg's VST3 PresetFile specification (little endian).
"""
import base64
import hashlib
import re
import struct


MAX_STATE = 256 * 1024 * 1024


def cid(value):
    value = str(value).upper()
    if not re.fullmatch(r'[0-9A-F]{32}', value):
        raise ValueError('VST3 插件身份必须为完整的 32 位十六进制 CID。')
    return value


def parse_preset(data):
    if not isinstance(data, bytes) or not 56 <= len(data) <= MAX_STATE:
        raise ValueError('VST3 预置长度无效或超过 256 MB。')
    if data[:4] != b'VST3' or struct.unpack_from('<i', data, 4)[0] != 1:
        raise ValueError('不是受支持的 VST3 预置容器。')
    identity = cid(data[8:40].decode('ascii'))
    offset = struct.unpack_from('<q', data, 40)[0]
    if not 48 <= offset <= len(data)-8 or data[offset:offset+4] != b'List':
        raise ValueError('VST3 预置缺少有效状态块目录。')
    count = struct.unpack_from('<i', data, offset+4)[0]
    if not 1 <= count <= 128 or offset+8+20*count != len(data):
        raise ValueError('VST3 预置目录长度不一致。')
    chunks = {}; spans = []
    for i in range(count):
        key, start, length = struct.unpack_from('<4sqq', data, offset+8+i*20)
        if key in chunks or start < 48 or length < 0 or start+length > offset:
            raise ValueError('VST3 状态块重复或超出容器范围。')
        if length and any(start < b and start+length > a for a, b in spans):
            raise ValueError('VST3 状态块发生重叠。')
        spans.append((start, start+length))
        chunks[key] = data[start:start+length]
    if b'Comp' not in chunks:
        raise ValueError('VST3 预置缺少音频组件状态。')
    return identity, chunks


def make_preset(identity, chunks):
    identity = cid(identity)
    if b'Comp' not in chunks or any(not isinstance(k, bytes) or len(k) != 4 for k in chunks):
        raise ValueError('VST3 状态块格式无效。')
    body = bytearray(); entries = []
    for key, data in chunks.items():
        if not isinstance(data, bytes): raise TypeError('Plugin state must be bytes')
        entries.append(struct.pack('<4sqq', key, 48+len(body), len(data)))
        body.extend(data)
    result = (b'VST3'+struct.pack('<i',1)+identity.encode('ascii')+
              struct.pack('<q',48+len(body))+body+b'List'+struct.pack('<i',len(entries))+b''.join(entries))
    result = bytes(result)
    parse_preset(result)
    return result


def envelope(data):
    return {'dtype':'u8','count':len(data),'data':base64.b64encode(data).decode('ascii')}


def from_envelope(blob):
    if blob.get('dtype') != 'u8' or not 0 <= blob.get('count',-1) <= MAX_STATE:
        raise ValueError('插件状态数据封装无效。')
    data = base64.b64decode(blob['data'], validate=True)
    if len(data) != blob['count']: raise ValueError('插件状态传输长度不一致。')
    return data


def state_summary(data):
    identity, chunks = parse_preset(data)
    return {'format':'vst3', 'cid':identity, 'preset_sha256':hashlib.sha256(data).hexdigest(),
            'chunks':{key.decode('ascii'):{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
                      for key, value in chunks.items()}}


def capture_chain(call, track_uuid):
    chain = call('fx_read','list',trackUuid=track_uuid).get('inserts',[])
    result = []
    for entry in chain:
        if entry.get('missing'): raise ValueError('插件缺失，无法读取完整状态：'+entry['name'])
        if entry.get('format') != 'vst3':
            raise ValueError('当前插件状态迁移仅支持 VST3；不能将 VST2/AU/内置效果按名称替代：'+entry['name'])
        exported = call('audio_plugin_read','export-preset',trackUuid=track_uuid,
                        slot=str(entry['slot']),format='plugin-format')
        if exported['extension'] != 'vstpreset': raise ValueError('插件未返回 VST3 预置。')
        data = from_envelope(exported['blob'])
        params = call('audio_plugin_read','get-params',trackUuid=track_uuid,slot=str(entry['slot']))
        result.append({**entry,**state_summary(data),'preset':envelope(data),
                       'parameters':params['params']})
    return result


def candidate_plugin(plugin, catalog):
    """Names only find a candidate; exported CID is verified before applying state."""
    matches = [p for p in catalog if p.get('format') == 'vst3' and
               (p.get('typeId') == plugin.get('typeId') or p.get('name') == plugin.get('plugin_name',plugin['name'])) and
               (not plugin.get('vendor') or p.get('vendor') == plugin['vendor'])]
    if len(matches) != 1:
        raise ValueError('ACE 中缺少同格式插件，或有多个同名插件：'+plugin['name'])
    return matches[0]


def restore_chain(call, track_uuid, plugins, catalog):
    results = []
    for plugin in plugins:
        candidate = candidate_plugin(plugin,catalog)
        inserted = call('fx_edit','add',trackUuid=track_uuid,type=candidate['typeId'])['insert']
        slot = str(inserted['slot'])
        exported = call('audio_plugin_read','export-preset',trackUuid=track_uuid,slot=slot,format='plugin-format')
        actual_cid, _ = parse_preset(from_envelope(exported['blob']))
        if actual_cid != cid(plugin['cid']):
            raise ValueError('插件 CID 不匹配，已停止，未套用任何状态：'+plugin['name'])
        data = from_envelope(plugin['preset'])
        if parse_preset(data)[0] != actual_cid: raise ValueError('插件状态与清单身份不一致。')
        call('audio_plugin_edit','import-preset',trackUuid=track_uuid,slot=slot,blob=envelope(data))
        # Read parameters before host bypass/disable switches can affect them.
        actual = call('audio_plugin_read','get-params',trackUuid=track_uuid,slot=slot)
        expected = plugin.get('parameters')
        if expected:
            got = {p['paramId']:p['value'] for p in actual['params']}
            differences = [p['name'] for p in expected if p['paramId'] not in got or abs(got[p['paramId']]-p['value'])>1e-6]
            if differences: raise ValueError('插件参数回读不一致：'+plugin['name']+' / '+', '.join(differences[:8]))
        set_result = call('fx_edit','set',trackUuid=track_uuid,slot=slot,
                         enabled=plugin['enabled'],bypassed=plugin['bypassed'],name=plugin['name'])['insert']
        if set_result['enabled'] != plugin['enabled'] or set_result['bypassed'] != plugin['bypassed']:
            raise ValueError('插件启用/旁通状态回读不一致：'+plugin['name'])
        readback = call('audio_plugin_read','export-preset',trackUuid=track_uuid,slot=slot,format='plugin-format')
        saved_data = from_envelope(readback['blob'])
        results.append({'name':plugin['name'],'slot':inserted['slot'],'cid':actual_cid,
                        'enabled':set_result['enabled'],'bypassed':set_result['bypassed'],
                        'source_state':state_summary(data),'ace_state':state_summary(saved_data),
                        'parameter_check':'matched' if expected else 'source_has_no_parameter_table',
                        'readback_parameters':actual['params']})
    return results
