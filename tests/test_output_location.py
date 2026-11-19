import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import app
from archive_reader import Archive
from convert_project import sha


@unittest.skipUnless(sys.platform == 'win32', 'Windows output paths')
class OutputLocationTests(unittest.TestCase):
    def test_both_directions_use_the_selected_system_drive_folder(self):
        local_temp = Path(os.environ['LOCALAPPDATA']) / 'Temp'
        if local_temp.drive.lower() != 'c:':
            self.skipTest('This regression needs a writable test location on C:')
        with tempfile.TemporaryDirectory(dir=local_temp) as tmp:
            root = Path(tmp)
            project = root / '混音.acep'
            project.write_bytes(b'saved project fixture')
            audio = root / '音频.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', ''))
                stream.writeframes(b'\x00' * 4800 * 4)
            fade = {'length': 0, 'shape': {'offsetX': 0, 'offsetY': 0}}
            clip = dict(uuid='clip', path=audio.name, pos=0, clipPos=0, clipDur=.1,
                        enabled=True, timeUnit='sec', fadeIn=fade, fadeOut=fade)
            track = dict(uuid='track', type='audio', gain=0, patterns=[clip])
            project_data = dict(tracks=[track], tempos=[dict(position=0, bpm=120)],
                                timeSignatures=[dict(barPos=0, numerator=4, denominator=4)])
            snapshot = [dict(track=dict(trackUuid='track', trackName='音乐'),
                             clips=[dict(clipUuid='clip', clipName='片段', clipBeginSec=0)])]
            output = root / 'New Project'
            with patch.object(app, 'read_saved', return_value=(project_data, snapshot, sha(project))):
                result = app.transfer(project, output, 'xml', log=lambda _: None)
            xml = Path(result['xml'])
            self.assertEqual(xml.parent.parent, output)
            tracks = Archive(xml)
            self.assertFalse(tracks.errors, tracks.errors)
            media = Path(tracks.tracks[0]['events'][0]['path'])
            self.assertEqual(media,audio)
            self.assertTrue(media.is_file())

            with patch('subprocess.Popen', side_effect=AssertionError('unexpected process launch')):
                reverse = app.transfer(xml, output, 'acep', log=lambda _: None)
                saved = Path(reverse['ace_project'])
                self.assertEqual(saved.parent.parent.parent, output)
                self.assertEqual(reverse['verified_saved_events'], 1)
                from ace_file import load_project
                document, _ = load_project(saved)
                copied = saved.parent / document['tracks'][0]['patterns'][0]['path']
                self.assertEqual(sha(copied), sha(media))
                self.assertTrue(copied.samefile(audio))
                self.assertFalse(list(saved.parent.rglob('*.wav')))
                self.assertEqual(app.transfer(saved, output, 'xml', log=lambda _: None)['audio_events'], 1)
