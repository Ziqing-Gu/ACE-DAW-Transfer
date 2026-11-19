"""Regression tests for no-host writeback and its proven edit boundaries."""
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from offline_native_writer import Changes, attr_key, make_layout, plan_plugins, same_media, write_project
from plugin_state import envelope, make_preset


class OfflineWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_layout_preserves_order_and_empty_audio_tracks(self):
        project = {'videoTracks':[{'uuid':'v','patterns':[{'uuid':'ve'}]}],
                   'markerTracks':[{'uuid':'m','patterns':[{'uuid':'me'}]}],
                   'tracks':[{'uuid':'empty','type':'audio','patterns':[]},
                             {'uuid':'implicit','type':'instrument','patterns':[]},
                             {'uuid':'a','type':'audio','patterns':[{'uuid':'ae'}]}]}
        rows=make_layout(project)
        self.assertEqual([row['track_uuid'] for row in rows],['v','m','empty','a'])
        self.assertEqual([row['event_uuids'] for row in rows],[['ve'],['me'],[],['ae']])

    def test_no_origin_refuses_without_native_file(self):
        ace=self.folder/'source.acep'
        xml=self.folder/'output.xml'
        out=xml.with_suffix('.cpr')
        ace.touch()
        xml.write_text('<tracklist2/>',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'原生来源'):
            write_project(xml,out,source_bpm=120,source_ace=ace,layout=[])
        self.assertFalse(out.exists())
        self.assertEqual(xml.read_text(encoding='utf8'),'<tracklist2/>')

    def test_opaque_state_can_change_in_its_verified_slot(self):
        identity='1' * 32
        def plugin(content):
            return dict(cid=identity,enabled=True,bypassed=False,
                        preset=envelope(make_preset(identity,{b'Comp':content})))
        field=attr_key('audioComponent',2) + struct.pack('>HI',7,5)
        raw=field+b'alpha'
        arr=SimpleNamespace(view=raw,offset=100)
        edits=Changes(arr)
        plan_plugins(arr,0,len(raw),[plugin(b'alpha')],[plugin(b'bravo')],edits)
        self.assertEqual(edits.writes,{100+len(field):(b'alpha',b'bravo')})
        for invalid in (b'x',b'toolong'):
            with self.assertRaisesRegex(ValueError,'结构变化'):
                plan_plugins(arr,0,len(raw),[plugin(b'alpha')],[plugin(invalid)],Changes(arr))
        arr.view=raw+raw
        with self.assertRaisesRegex(ValueError,'唯一定位'):
            plan_plugins(arr,0,len(arr.view),[plugin(b'alpha')],[plugin(b'bravo')],Changes(arr))

    def test_substituted_media_with_equal_format_is_detected(self):
        import numpy as np
        import soundfile as sf
        src=self.folder/'src.wav';target=self.folder/'target.wav'
        silence=np.zeros((480,2),dtype='float32')
        sf.write(src,silence,48000,subtype='PCM_24')
        sf.write(target,silence,48000,subtype='FLOAT')
        same_media(src,target,'audio',set())
        silence[42,1]=.5
        sf.write(target,silence,48000,subtype='FLOAT')
        with self.assertRaisesRegex(ValueError,'采样内容已替换'):
            same_media(src,target,'audio',set())


if __name__=='__main__':
    unittest.main()
