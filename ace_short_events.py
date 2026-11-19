"""ACE drops clips up to 10 ms when reading a saved project."""
import math


def visible_seconds(event):
    duration=event['duration']
    return .011 if duration<=.010000001 else duration


def marker_ticks(event,bpm):
    ticks_per_second=bpm*8
    ticks=round(event['duration']*ticks_per_second)
    # An 11-tick marker at 120 BPM disappeared on reopen. A saved 16-tick
    # marker at 175 BPM survived. Keep both a tick and a time minimum.
    return max(ticks, 16, math.ceil(.011*ticks_per_second))


def pad_short_audio(source,destination,event):
    """Preserve the original start/offset and mute just the added tail."""
    import numpy as np
    import soundfile as sf
    with sf.SoundFile(source) as inp:
        rate=inp.samplerate
        end=round((event['offset']+event['duration'])*rate)
        padded=math.ceil((event['offset']+visible_seconds(event))*rate)
        total=max(len(inp),padded)
        fmt='RF64' if total*inp.channels*4+128>=2**32 else 'WAV'
        with sf.SoundFile(destination,'x',format=fmt,subtype='FLOAT',channels=inp.channels,samplerate=rate) as out:
            for pos in range(0,total,262144):
                n=min(262144,total-pos)
                data=np.zeros((n,inp.channels),dtype='float32')
                samples=inp.read(n,dtype='float32',always_2d=True)
                data[:len(samples)]=samples
                a,b=max(pos,end),min(pos+n,padded)
                if a<b:data[a-pos:b-pos]=0
                # An editable fade-out at the padded edge would finish too late.
                fade=event['fade_out']
                if fade['native'] and fade['length']>0:
                    t=(np.arange(pos,pos+n)/rate-event['offset']-(event['duration']-fade['length']))/fade['length']
                    t=np.clip(t,0,1)
                    envelope=1-t*t*(3-2*t)
                    envelope[np.arange(pos,pos+n)>=padded]=1
                    data*=envelope[:,None]
                out.write(data)
