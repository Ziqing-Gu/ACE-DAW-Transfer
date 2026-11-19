import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from native_events import materialize_native_media


class NativeEventMediaTests(unittest.TestCase):
    def test_edited_regions_with_envelope_and_polarity(self):
        # An edited region is spliced into the full source; event offset is
        # nonzero. Envelope X coordinates remain source sample positions.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root/'take.wav'
            edited = root/'edit.wav'
            sf.write(original,np.full(9,0.25),48000,subtype='FLOAT')
            sf.write(edited,np.array([0.75, 0.5, 1.5, -0.5]),48000,subtype='FLOAT')
            def part(path, offset, length, start):
                return dict(path=str(path),offset=offset,frames=length,position=start,channels=1,rate=48000)
            segments = [part(original,0,3,0),part(edited,1,3,3),part(original,6,3,6)]
            event = dict(path=str(original),offset=3/48000,
                         _native_segments=segments, _native_inverted=True,
                         _native_volume_envelope=[dict(frame=2.,amplitude=1.,shape=0),
                                                  dict(frame=6.,amplitude=0.,shape=0)],
                         losses=[dict(code='polarity',bakeable=True),
                                 dict(code='volume_envelope_linear',bakeable=True)])
            tracks=[dict(events=[event])]
            emitted=materialize_native_media(tracks,root/'render',bake_event_processing=True)
            self.assertEqual(len(emitted),2)
            samples,rate=sf.read(event['path'],dtype='float32')
            self.assertEqual(rate,48000)
            np.testing.assert_array_equal(samples,[-.25,-.25,-.25,-.375,-.75,.125,0.,0.,0.])
            self.assertEqual(event['offset'],3/48000)
            self.assertEqual(event['_native_segments'],segments)

    def test_bypass_baking_and_do_not_approximate_bezier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'take.wav'
            sf.write(source,[.25,.5],48000,subtype='FLOAT')
            curved = dict(path=str(source),_native_volume_envelope=[dict(frame=0,amplitude=.5,shape=1)],
                          losses=[dict(code='volume_envelope_curve',bakeable=False)])
            invert = dict(path=str(source),_native_inverted=True,losses=[dict(code='polarity',bakeable=True)])
            self.assertEqual(materialize_native_media([dict(events=[curved,invert])],root/'bypass'),[])
            self.assertEqual(materialize_native_media([dict(events=[curved])],root/'render',bake_event_processing=True),[])
            self.assertEqual(curved['path'],str(source))
            self.assertEqual(invert['path'],str(source))
