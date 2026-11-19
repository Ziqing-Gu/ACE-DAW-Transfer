"""Native mixer regression cases. Historical host-export comparisons are optional."""
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET

from native_mixer import _standard_pan, read_native_mixer
from plugin_xml import read_inserts


class NativeMixerTests(unittest.TestCase):
    def test_pan_changes_only_balance_with_verified_panner_identity(self):
        p=ET.Element('member')
        ET.SubElement(p,'string',name='Plugin Name',value='Standard Panner')
        identity=ET.SubElement(p,'member',name='Plugin UID')
        cid=ET.SubElement(identity,'string',name='GUID',value='44E1149EDB3E4387BDD827FEA3A39EE7')
        state=ET.SubElement(p,'bin',name='audioComponent')
        for normalized,expected in ((0,-10),(.25,-5),(.5,0),(1,10)):
            state.text=(struct.pack('<f',normalized)+bytes.fromhex('0000003F040000000200000000000000')).hex()
            self.assertEqual(_standard_pan(p),expected)
        cid.set('value','0'*32)
        with self.assertRaisesRegex(ValueError,'未识别'):_standard_pan(p)

    def test_mono_mode_never_accepts_sidechain_or_unverified_auxiliary(self):
        folder=ET.fromstring('<member><list name="Slot"/></member>')
        item=ET.parse(Path(__file__).resolve().parents[1]/'templates/vst3-insert.xml').getroot()
        folder[0].append(item)
        plug=item.find('member[@name="Plugin"]')
        for direction in ('Input','Output'):
            for bus in plug.findall(f'list[@name="Audio {direction} Arrangement"]/item/list'):
                bus[:]=[ET.Element('item',value='0')]
        self.assertEqual(read_inserts(folder,allow_mono_main=True)[0]['source_main_layout'],'mono')
        with self.assertRaisesRegex(ValueError,'双声道'):read_inserts(folder)
        ET.SubElement(plug,'int',name='SideChain enable',value='1')
        with self.assertRaisesRegex(ValueError,'外部侧链'):
            read_inserts(folder,allow_mono_main=True)

    def test_offline_matches_host_exported_plugin_chain(self):
        from plugin_state import from_envelope
        import os
        if not os.environ.get('ACE_HOST_FIXTURES'):self.skipTest('Optional native host fixtures')
        package=Path(os.environ['ACE_HOST_FIXTURES'])/'converted'
        evidence=package.parent/'native'
        native=package/'Cubase'/'ACE-3VST3.cpr'
        xml=evidence/'reverse-cpr-235941516265'/'from-project.xml'
        if not native.is_file() or not xml.is_file():
            self.skipTest('historical comparison files are not installed')
        channels,warnings,errors,master=read_native_mixer(native,include_master=True)
        self.assertEqual((warnings,errors),([],[]))
        self.assertEqual(master['gain'],0)
        self.assertEqual(master['plugins'],[])
        actual=channels[0]['plugins']
        reference=read_inserts(ET.parse(xml).find('.//member[@name="DeviceAttributes"]/member[@name="InsertFolder"]'))
        self.assertEqual(len(actual),3)
        for result,source in zip(actual,reference):
            self.assertEqual((result['cid'],result['slot'],result['enabled'],from_envelope(result['preset'])),
                             (source['cid'],source['slot'],source['enabled'],from_envelope(source['preset'])))
