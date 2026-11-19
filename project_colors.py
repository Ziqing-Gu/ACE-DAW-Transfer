"""Track and marker color slots in Steinberg archives and RGB values in ACE."""
import re
import xml.etree.ElementTree as ET

# Event Colors in the local Cubase and Nuendo 14 factory projects, in slot order.
FACTORY = tuple('#'+s for s in (
    'e53636 e57636 e5ba3b d5e84c 8de536 51d83c 35dd5f 33d697 '
    '30cccc 40aae8 5d80ea 796aed a056ea cf44e5 e536b9 e53679').split())


def rgb(value):
    return value.lower() if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value) else None


def assign_slots(colors, palette):
    """Give different requested colors different available slots, reserving exact matches."""
    ordered=sorted({value for c in colors if (value:=rgb(c))})
    palette=tuple(palette)
    slots={c:palette.index(c) for c in ordered if c in palette}
    available=set(range(len(palette)))-set(slots.values())
    def channels(c):
        return tuple(int(c[i:i+2],16) for i in (1,3,5))
    for color in ordered:
        if color in slots:continue
        wanted=channels(color)
        def distance(i):
            return (sum(w*(a-b)**2 for a,b,w in zip(wanted,channels(palette[i]),(3,6,2))),i)
        slot=min(available or range(len(palette)),key=distance)
        slots[color]=slot
        available.discard(slot)
    return slots


def export_palette(colors, limit=40):
    ordered = sorted({v for c in colors if (v := rgb(c))})
    palette = list(FACTORY)
    first = [c for c in ordered if c in FACTORY]
    first.extend(c for c in ordered if c not in FACTORY)
    slots = assign_slots(first[:len(FACTORY)], FACTORY)
    for c, i in slots.items():
        palette[i] = c
    for c in first[len(FACTORY):]:
        if len(palette) < limit:
            slots[c] = len(palette)
            palette.append(c)
        else:
            slots[c] = assign_slots([c],palette)[c]
    return slots, palette


def xml_color(node, color, slots):
    color=rgb(color)
    if color is None:return
    attrs=node.find('member[@name="Additional Attributes"]')
    if attrs is None:attrs=ET.SubElement(node,'member',name='Additional Attributes')
    field=attrs.find('int[@name="Farb"]')
    if field is None:field=ET.SubElement(attrs,'int',name='Farb')
    slot=slots[color]
    field.set('value',str(slot))
    # A standard comment is ignored by the host. It lets the converter recover
    # the exact author RGB when a track archive only carries a palette index.
    node.append(ET.Comment(f'ACE-DAW-RGB {node.get("ID")} {slot} {color}'))


def xml_hints(data):
    return {(obj.decode(),int(slot)):color.decode() for obj,slot,color in re.findall(
        rb'<!--ACE-DAW-RGB ([0-9]+) ([0-9]+) (#[0-9a-f]{6})-->',data)}


def read_xml_color(node, hints):
    field=node.find('member[@name="Additional Attributes"]/int[@name="Farb"]')
    if field is None:return {}
    try:slot=int(field.get('value',''))
    except ValueError:return {}
    if slot<0:return {}
    color=hints.get((node.get('ID'),slot))
    if color is None and slot<len(FACTORY):color=FACTORY[slot]
    return dict(color_slot=slot,color=color)


def native_palette(a):
    from native_pool import objects
    for _,start,end in objects(a,bytes(a.view),'UColorSet'):
        name,p=a.text(start,end)
        if name!='Event Colors':continue
        count=a.archive.uint(p,4,end)
        if not 1<=count<=1024:return ()
        palette=[]
        p+=4
        for _ in range(count):
            _,p=a.text(p,end)
            argb=a.archive.uint(p,4,end)
            palette.append(f'#{argb & 0xffffff:06x}')
            p+=4
        return tuple(palette)
    return ()


def color_field(a,p,end):
    """Walk only immediate FourCC attributes. Object payloads may contain unrelated colors."""
    count=a.archive.uint(p,4,end)
    p+=4
    for _ in range(count):
        field=bytes(a.view[p:p+4])
        kind=a.archive.uint(p+4,2,end)
        p+=6
        if field==b'braF' and kind==1:
            a.archive.uint(p,8,end)
            return p
        if kind in (1,4):p+=8
        elif kind in (8,0x12,0x22,0x82,0x92,0xa2):
            _,_,_,p=a.archive.obj(p,end)
        else:return None
    return None


def native_color(a,pos,end,palette):
    field=color_field(a,pos,end)
    if field is None:return {}
    slot=int.from_bytes(a.view[field:field+8],'big',signed=True)
    if slot<0:return {}
    return dict(color_slot=slot,color=palette[slot] if slot<len(palette) else None)
