#!/usr/bin/env python3
"""Copy closed-GOP local video segments and verify every decoded frame before indexing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ffmpeg',default='/usr/bin/ffmpeg')
    p.add_argument('--ffprobe',default='/usr/bin/ffprobe')
    a=p.parse_args()
    source=a.input.resolve(); root=a.output.resolve()
    root.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,LD_LIBRARY_PATH='/usr/lib/aarch64-linux-gnu')
    def run(command):
        return subprocess.run(command,env=env,check=True,capture_output=True,text=True,timeout=300).stdout
    def hashes(file):
        text=run([a.ffmpeg,'-nostdin','-v','error','-threads','1','-i',str(file),'-map','0:v:0',
                  '-an','-vsync','0','-pix_fmt','yuv420p','-f','framemd5','-'])
        return [line.split(',')[-1].strip() for line in text.splitlines() if line and not line.startswith('#')]
    info=json.loads(run([a.ffprobe,'-v','error','-select_streams','v:0','-show_entries',
                        'stream=codec_name,width,height,avg_frame_rate,r_frame_rate','-of','json',str(source)]))['streams'][0]
    if info['codec_name']!='h264' or info['avg_frame_rate']!=info['r_frame_rate']:
        raise RuntimeError('This index requires constant-frame-rate H.264')
    num,den=map(int,info['avg_frame_rate'].split('/')); fps=num/den
    print('Creating lossless bitstream-copy segments',flush=True)
    run([a.ffmpeg,'-nostdin','-v','error','-i',str(source),'-map','0:v:0','-an','-c:v','copy',
         '-f','segment','-segment_time','2','-reset_timestamps','1',str(root/'segment_%05d.mp4')])
    reference=hashes(source)
    entries=[]; actual=[]
    for file in sorted(root.glob('segment_*.mp4')):
        values=hashes(file)
        if not values or values!=reference[len(actual):len(actual)+len(values)]:
            raise RuntimeError('Segment frame mismatch; do not use for catchup: '+str(file))
        entries.append(dict(path=str(file),first_frame=len(actual),frames=len(values)))
        actual.extend(values)
    if actual!=reference:
        raise RuntimeError('Segment frame count does not match source')
    stat=source.stat()
    index=dict(version=1,source=str(source),source_bytes=stat.st_size,source_mtime=int(stat.st_mtime),
               source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),fps=fps,frames=len(reference),
               width=info['width'],height=info['height'],verified='all_frames_software_decode_md5_identical',segments=entries)
    (root/'index.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({'frames':len(reference),'segments':len(entries),'index':str(root/'index.json')}),flush=True)


if __name__=='__main__':main()
