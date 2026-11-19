"""Exercise the installed command-line entry point with generated, relocatable media."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import wave

from ace_file import save_project
from ace_writer import new_track

def run(executable):
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp).resolve()
        audio=base/'弦乐 A.wav'
        with wave.open(str(audio),'wb') as w:
            w.setparams((2,2,48000,0,'NONE','')); w.writeframes(bytes(96000))
        project=json.loads((Path(__file__).resolve().parents[1]/'templates/ace-empty.json').read_text(encoding='utf8'))
        track=new_track('audio','Music')
        fade=dict(length=.04,shape=dict(offsetX=0.,offsetY=0.))
        track['patterns']=[dict(uuid='audio-event',name='Scene 2',path=audio.name,pos=1.,clipPos=.03,clipDur=.35,
                                dur=.5,timeUnit='sec',enabled=False,gain=-2.,fadeIn=fade,fadeOut=fade)]
        mark=new_track('marker','Cues');mark['patterns']=[dict(uuid='marker-event',name='Cut',pos=0.,clipPos=0.,dur=480.,
                                clipDur=480.,timeUnit='tick',enabled=True,color='#a03060')]
        project.update(tracks=[track],videoTracks=[],markerTracks=[mark])
        source=base/'编辑.acep';save_project(source,project)
        def convert(src,fmt):
            receipt=base/(fmt+'.json')
            subprocess.run([str(executable),'--source',str(src),'--output',str(base),'--format',fmt,'--result-file',str(receipt)],check=True)
            return json.loads(receipt.read_text(encoding='utf8'))
        for fmt in ('cpr','npr','xml'):
            result=convert(source,fmt)
            assert result['audio_events']==1 and result['native_fades']==2 and result['range_markers']==1
            dst=Path(result.get('project') or result['xml'])
            assert dst.is_file()
            rev=convert(dst,'acep')
            from ace_file import load_project
            back,_=load_project(rev['ace_project'])
            clip=back['tracks'][0]['patterns'][0]
            assert abs(clip['pos']+clip['clipPos']-1.03)<1e-6 and not clip['enabled']
            assert back['markerTracks'][0]['patterns'][0]['color']=='#a03060'
            assert Path(clip['path']).resolve()==audio
        print('CPR, NPR, XML and ACEP passed.')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path)
    run(parser.parse_args().executable)

