import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from ace_file import load_project
from ace_file_plugins import encode_block, decode_block, make_insert, read_insert
from app import transfer
from archive_reader import Archive
from plugin_state import envelope, from_envelope, make_preset, parse_preset


class OfflineAceTests(unittest.TestCase):
    def test_two_close_point_markers_survive_both_files(self):
        import copy
        import numpy  # initialize media dependencies before blocking process creation
        template = Path(__file__).resolve().parents[1]/'templates'/'Nuendo_Marker_Cycle.xml'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = ET.parse(template)
            events = tree.find('.//obj[@class="MMarkerTrackEvent"]/obj[@name="Node"]/list[@name="Events"]')
            sample = events[0]
            events[:] = []
            for i, (ident, start) in enumerate([(9, 480), (4, 481)]):
                point = copy.deepcopy(sample)
                point.set('class', 'MMarkerEvent')
                point.set('ID', str(980000+i))
                point.remove(point.find('float[@name="Length"]'))
                point.find('float[@name="Start"]').set('value', str(start))
                point.find('int[@name="ID"]').set('value', str(ident))
                point.find('string[@name="Name"]').set('value', f'M{ident}')
                events.append(point)
            xml = root/'markers.xml'
            tree.write(xml, encoding='utf-8')
            with patch('subprocess.Popen', side_effect=AssertionError('unexpected launch')):
                first = transfer(xml,root,'acep',log=lambda _:None)
                document,_ = load_project(first['ace_project'])
                clips = [c for t in document['markerTracks'] for c in t['patterns']]
                self.assertEqual([(c['name'],c['pos']) for c in clips],[('M9',480),('M4',481)])
                self.assertTrue(all(c['clipDur']>=16 for c in clips))
                back = transfer(first['ace_project'],root,'xml',log=lambda _:None)
                archive = Archive(back['xml'])
                self.assertEqual(archive.errors, [])
                rows = [e for t in archive.tracks if t['type']=='marker' for e in t['events']]
                self.assertEqual([(e['marker_id'],e['marker_kind'],round(e['start']*archive.bpm*8)) for e in rows],
                                 [(9,'point',480),(4,'point',481)])

    def test_saved_plugin_components_and_controller_are_byte_exact(self):
        identity='ABCDEF019182FAEB51696E6751736370'
        catalog=[dict(cid=identity, name='QQ Super Compression',vendor='Qing Audio',version='1',
                      typeId='VST3-QQ Super Compression-cf98ce9d-7b9fd373')]
        chunks={b'Comp':bytes(range(256)),b'Cont':b'\x00\xff'}
        plugin=dict(cid=identity,name='Compressor',enabled=True,bypassed=True,preset=envelope(make_preset(identity,chunks)))
        saved=make_insert(plugin,catalog,257)
        reread=read_insert(saved,catalog,0)
        self.assertEqual((reread['name'],reread['bypassed']),('Compressor',True))
        self.assertEqual(parse_preset(from_envelope(reread['preset'])),(identity,chunks))
        self.assertEqual(encode_block(b'\xff'),'1.+C')
        self.assertEqual(decode_block('1.+C'),b'\xff')
