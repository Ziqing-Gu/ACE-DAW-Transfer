"""VST3 insert states in Steinberg track archives; no vendor-specific decoding."""
import copy
import xml.etree.ElementTree as E
from pathlib import Path
from plugin_state import cid, envelope, from_envelope, make_preset, parse_preset, state_summary

TEMPLATE = Path(__file__).parent/'templates'/'vst3-insert.xml'


def value(node, name, default=None):
    if node is not None:
        for e in node:
            if e.get('name') == name: return e.get('value', default)
    return default


def put(node, tag, name, value):
    e = node.find(f'{tag}[@name="{name}"]')
    if e is None: e = E.SubElement(node, tag, name=name)
    e.set('value', str(value))


def read_inserts(folder, *, allow_mono_main=False):
    """Return static pre-fader stereo VST3 inserts. Reject unrepresented routing."""
    if folder is None: return []
    known={'RuntimeID','Bypass','SeparationPosition','Slot','SummingMode'}
    if any(e.get('name') not in known for e in folder):raise ValueError('插入区存在未支持的设置。')
    if value(folder,'Bypass','0') not in ('0','1'):raise ValueError('未知插入区旁通状态。')
    if folder.find('list[@name="Slot"]') is None:raise ValueError('插入区缺少插槽列表。')
    if value(folder, 'SummingMode', '1') != '1': raise ValueError('尚不支持并行插入处理。')
    separation = int(value(folder, 'SeparationPosition', '14'))
    result = []
    for index, item in enumerate(folder.findall('list[@name="Slot"]/item')):
        plugin = item.find('member[@name="Plugin"]')
        if plugin is None:
            if value(item, 'State', '0') != '0': raise ValueError('插入槽存在未识别的插件数据。')
            continue
        name = value(plugin, 'Plugin Name', 'Unknown plugin')
        if value(plugin,'SideChain enable','0')!='0':
            raise ValueError(name+'：已启用外部侧链，ACE 无法还原此输入。')
        if index >= separation: raise ValueError(name+'：推子后插入暂不能映射到 ACE。')
        if value(item, 'State', '1') != '1': raise ValueError(name+'：未支持的插入槽状态。')
        identity = value(plugin.find('member[@name="Plugin UID"]'), 'GUID', '')
        # VST2/native effects are not inferred from names or a truncated identifier.
        component = plugin.find('bin[@name="audioComponent"]')
        if component is None: raise ValueError(name+'：缺少标准 VST3 组件状态；VST2/内置效果尚未支持。')
        identity = cid(identity)
        main_layouts=[]
        for direction in ('Input','Output'):
            count=int(value(plugin, f'Audio {direction} Count','0'))
            if count < 1 or (direction=='Output' and count != 1):
                raise ValueError(name+'：仅支持单个双声道主输出总线。')
            arrangement = plugin.find(f'list[@name="Audio {direction} Arrangement"]')
            layouts=[tuple(x.get('value') for x in bus.findall('list[@name="Type"]/item'))
                     for bus in arrangement] if arrangement is not None else []
            allowed={('1','2'),('0',)} if allow_mono_main else {('1','2')}
            if len(layouts)!=count or any(layout not in allowed for layout in layouts):
                raise ValueError(name+'：插件声道布局不是已确认的双声道布局。')
            main_layouts.append(layouts[0])
        if allow_mono_main and main_layouts[0]!=main_layouts[1]:
            raise ValueError(name+'：插件主输入/输出布局不一致。')
        patcher = item.find('member[@name="Patcher"]')
        if main_layouts==[('0',),('0',)]:
            count=int(value(plugin,'Audio Input Count','0'))
            if count>2 or (count>1 and patcher is None):
                raise ValueError(name+'：单声道插件辅助输入未能确认为未连接。')
        if patcher is not None:
            for connection in patcher.findall('.//list[@name="Prev Output Connection"]/item')+patcher.findall('.//list[@name="Input Connection"]/item'):
                if value(connection,'ID','0') != '0' or value(connection,'Arrangement Index','0') != '0':
                    raise ValueError(name+'：侧链或自定义插件路由尚未支持。')
        chunks = {b'Comp':bytes.fromhex(component.text or '')}
        controller = plugin.find('bin[@name="editController"]')
        if controller is not None: chunks[b'Cont'] = bytes.fromhex(controller.text or '')
        data = make_preset(identity, chunks)
        active = value(plugin, 'Active','1')
        if active not in ('0','1'): raise ValueError(name+'：未知启用状态。')
        result.append({'name':name,'format':'vst3','slot':index,'enabled':active=='1',
                       'bypassed':value(folder,'Bypass','0')=='1',
                       'preset':envelope(data),**state_summary(data)})
        if main_layouts==[('0',),('0',)]:result[-1]['source_main_layout']='mono'
    return result


def write_inserts(folder, plugins):
    """Build native slots from ACE state, independently of a full DAW export."""
    if len(plugins)>14: raise ValueError('当前版本最多支持 14 个推子前 VST3 插入。')
    slots = folder.find('list[@name="Slot"]')
    if slots is None: raise ValueError('原生模板缺少插入槽。')
    warnings = []
    put(folder,'int','Bypass',0)
    put(folder,'int','SeparationPosition',14)
    for index, plugin in enumerate(plugins):
        identity, chunks = parse_preset(from_envelope(plugin['preset']))
        if identity != cid(plugin['cid']): raise ValueError('插件 CID 与状态不一致。')
        if set(chunks)-{b'Comp',b'Cont',b'Info'}:
            raise ValueError(plugin['name']+'：预置含未支持的程序状态块。')
        item = copy.deepcopy(E.parse(TEMPLATE).getroot())
        p = item.find('member[@name="Plugin"]')
        put(p.find('member[@name="Plugin UID"]'),'string','GUID',identity)
        put(p,'string','Plugin Name',plugin.get('plugin_name',plugin['name']))
        put(p,'string','IDString',identity+'-'+str(index))
        put(p,'int','Active',int(plugin['enabled'] and not plugin['bypassed']))
        # BypassTag belongs to the concrete plugin and must never be copied from Pro-R.
        tag = p.find('int[@name="BypassTag"]')
        if tag is not None: p.remove(tag)
        for key, field in ((b'Comp','audioComponent'),(b'Cont','editController')):
            node = p.find(f'bin[@name="{field}"]')
            if key not in chunks:
                if node is not None: p.remove(node)
            else:
                if node is None: node = E.SubElement(p,'bin',name=field)
                hexdata = chunks[key].hex().upper()
                # Nuendo rejects a long single-line <bin>, although it is valid XML.
                node.text = '\n'+'\n'.join(hexdata[i:i+64] for i in range(0,len(hexdata),64))+'\n'
        if plugin['bypassed']:
            warnings.append(plugin['name']+'：ACE 宿主旁通映射为 DAW 插件停用，完整状态保留；不等同于宿主旁通按钮。')
        slots[index] = item
    return warnings
