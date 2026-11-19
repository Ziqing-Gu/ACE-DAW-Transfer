"""Focused semantic regressions; all generated files live on D:."""
import copy, json, tempfile, unittest
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
import soundfile as sf
import native_xml as n
from archive_reader import Archive
from ace_writer import bake_event

class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=Path(tempfile.mkdtemp(prefix='regression-'))
        cls.audio=cls.base/'source.wav'
        sf.write(cls.audio,np.full((480000,2),2.,dtype=np.float32),48000,subtype='FLOAT')
    def fixture(self,name='input.xml'):
        n.counter=100000;n.runtime=10000
        e=n.event(self.audio,'Event title',1.12345678,2.,.25,False,.5,.5)
        n.val(e,'Volume',10**(-3.5/20))
        root=n.document([n.track('Track title',0,[e])])
        p=self.base/name;E.ElementTree(root).write(p,encoding='utf-8',xml_declaration=True)
        return p,root
    def save(self,root,name):
        p=self.base/name;E.ElementTree(root).write(p,encoding='utf-8',xml_declaration=True);return p
    def test_names_geometry_gain_mute_and_s_curve(self):
        p,_=self.fixture();a=Archive(p);self.assertFalse(a.errors,a.errors)
        e=a.tracks[0]['events'][0]
        self.assertEqual(e['name'],'Event title');self.assertEqual(e['start'],1.12345678)
        self.assertEqual(e['offset'],.25);self.assertEqual(e['duration'],2)
        self.assertAlmostEqual(e['gain'],-3.5);self.assertFalse(e['enabled'])
        self.assertTrue(e['fade_in']['native']);self.assertTrue(e['fade_out']['native'])
    def test_fractional_sample_edit_keeps_adjacent_events_touching(self):
        duration=1000.7/48000
        offset=100.25/48000
        first=n.event(self.audio,'first',0,duration,offset,True,0,0)
        second=n.event(self.audio,'second',duration,1,0,True,0,0)
        archive=Archive(self.save(n.document([n.track('joined',0,[first,second])]),'fractional.xml'))
        self.assertFalse(archive.errors,archive.errors)
        a,b=archive.tracks[0]['events']
        self.assertAlmostEqual(a['offset'],offset,places=12)
        self.assertAlmostEqual(a['duration'],b['start'],places=12)

    def test_linear_is_not_mistaken_for_s(self):
        _,root=self.fixture();curve=root.find('.//obj[@name="FadeIn"]/obj[@name="Curve"]')
        pts=curve.find('list');pts[:]=[pts[0],pts[-1]]
        p=self.save(root,'linear.xml')
        self.assertTrue(Archive(p).errors)
        a=Archive(p,True);self.assertFalse(a.errors,a.errors);self.assertFalse(a.tracks[0]['events'][0]['fade_in']['native'])
        dest=self.base/'baked.wav';bake_event(self.audio,dest,a.tracks[0]['events'][0])
        audio,rate=sf.read(dest,dtype='float32',always_2d=True)
        self.assertEqual(audio[0,0],2.)  # full source and floating peaks retained
        self.assertEqual(audio[int(.25*rate),0],0.)
        self.assertAlmostEqual(float(audio[int(.5*rate),0]),1.,places=6)
        self.assertEqual(audio[int(2*rate),0],2.)  # native fade-out is not baked twice
    def test_overlap_rejected_without_cutting(self):
        _,root=self.fixture();events=root.find('list/obj/obj[@name="Node"]/list[@name="Events"]')
        duplicate=n.clone(events[0]);n.val(duplicate,'Start',1.5);events.append(duplicate)
        self.assertTrue(any('重叠' in e for e in Archive(self.save(root,'overlap.xml')).errors))
    def test_processing_and_missing_media_rejected(self):
        _,root=self.fixture();gain=root.find('.//member[@name="InputGain"]/float[@name="Value"]');gain.set('value','20000')
        self.assertTrue(any('InputGain' in e for e in Archive(self.save(root,'processing.xml')).errors))
        _,root=self.fixture();root.find('.//obj[@class="FNPath"]/string[@name="Name"]').set('value','missing.wav')
        self.assertTrue(any('媒体缺失' in e for e in Archive(self.save(root,'missing.xml')).errors))
    def test_missing_anchor_not_assumed_unity(self):
        _,root=self.fixture();v=root.find('.//member[@name="DeviceAttributes"]/member[@name="Volume"]');v.remove(v.find('float[@name="AnchorValue"]'))
        self.assertTrue(any('AnchorValue' in e for e in Archive(self.save(root,'anchor.xml')).errors))
    def test_negative_time_and_wrong_root_rejected(self):
        _,root=self.fixture();n.val(root.find('.//obj[@class="MAudioEvent"]'),'Start',-1)
        self.assertTrue(Archive(self.save(root,'negative.xml')).errors)
        with self.assertRaises(ValueError):Archive(self.save(E.Element('score-partwise'),'score.xml'))

if __name__=='__main__':unittest.main()
