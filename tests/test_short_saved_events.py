from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
import soundfile as sf
from ace_short_events import marker_ticks, pad_short_audio, visible_seconds


class ShortSavedEvents(unittest.TestCase):
    def test_padded_span_keeps_original_samples_and_an_editable_source_handle(self):
        with TemporaryDirectory() as tmp:
            src=Path(tmp)/'source.wav'
            dst=Path(tmp)/'clip.wav'
            sf.write(src,np.ones(4800,dtype='float32')*.25,48000,subtype='FLOAT')
            event=dict(offset=.02,duration=.007,fade_out={'native':True,'length':0})
            pad_short_audio(src,dst,event)
            data,rate=sf.read(dst)
            self.assertEqual(rate,48000)
            np.testing.assert_array_equal(data[:1296],.25)
            np.testing.assert_array_equal(data[1296:1488],0.)
            np.testing.assert_array_equal(data[1488:],.25)
            self.assertEqual(visible_seconds(event),.011)

    def test_point_marker_survives_minimum_and_regular_marker_keeps_duration(self):
        self.assertGreater(marker_ticks({'duration':0},175)/(175*8),.010)
        self.assertEqual(marker_ticks({'duration':1},175),1400)
