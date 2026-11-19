import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from ace_file import load_project,save_project
from ace_writer import new_track
from app import transfer
from project_colors import FACTORY,assign_slots,export_palette


class ProjectColors(unittest.TestCase):
    def test_author_colors_on_tracks_and_markers_survive_xml_and_ace(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            doc=json.loads((Path(__file__).resolve().parents[1]/'templates'/'ace-empty.json').read_text(encoding='utf8'))
            audio=new_track('audio','同名')
            marker=new_track('marker','同名')
            audio['color']=marker['color']='#112233'
            marks=[f'#{i:02x}7aaa' for i in range(27)]
            for i,color in enumerate(marks):
                marker['patterns'].append(dict(uuid='marker-'+str(i),name=str(i),pos=i*960,
                    clipPos=0,clipDur=480,dur=480,timeUnit='tick',enabled=True,color=color))
            doc.update(tracks=[audio],markerTracks=[marker],videoTracks=[])
            file=folder/'colors.acep'
            save_project(file,doc)
            with patch('subprocess.Popen',side_effect=AssertionError('unexpected DAW launch')):
                out=transfer(file,folder,'xml',log=lambda _:None)
                xml=Path(out['xml'])
                tracks=ET.parse(xml).findall('./list[@name="track"]/obj')
                def slot(element):
                    return int(element.find('member[@name="Additional Attributes"]/int[@name="Farb"]').get('value'))
                expected,palette=export_palette([audio['color'],*marks])
                self.assertEqual([slot(x) for x in tracks],[expected[audio['color']]]*2)
                self.assertEqual([slot(e) for e in tracks[0].findall('obj[@name="Node"]/list[@name="Events"]/obj')],
                                 [expected[c] for c in marks])
                self.assertEqual(out['xml_color_slots'],28)
                from native_events import Arrangement
                from project_colors import native_palette
                with Arrangement(Path(out['palette_project'])) as saved:
                    self.assertEqual(list(native_palette(saved)),palette)
                for ext in ('cpr','npr'):
                    direct=transfer(file,folder,ext,log=lambda _:None)
                    from native_input import NativeInput
                    saved=NativeInput(Path(direct['project']))
                    self.assertEqual([e['color'] for e in saved.tracks[0]['events']],marks)
                    self.assertFalse(list(Path(direct['project']).parent.glob('*.xml')))
                reverse=transfer(xml,folder,'acep',log=lambda _:None)
                data,_=load_project(reverse['ace_project'])
                self.assertEqual(data['tracks'][0]['color'],audio['color'])
                self.assertEqual(data['markerTracks'][0]['color'],marker['color'])
                self.assertEqual([c['color'] for c in data['markerTracks'][0]['patterns']],marks)
                files=list(Path(reverse['output_directory']).rglob('*'))
                self.assertEqual(sum(f.suffix=='.acep' for f in files),1)
                self.assertFalse(any(f.suffix in ('.xml','.npr','.cpr') for f in files))

    def test_standard_colors_keep_their_slots(self):
        slots=assign_slots(FACTORY,FACTORY)
        self.assertEqual([slots[c] for c in FACTORY],list(range(16)))
