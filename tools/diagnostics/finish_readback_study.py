#!/usr/bin/env python3
"""Continue a completed wall study with bounded admission and other demo checks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from run_readback_study import ROOT, run_stage, save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    a=parser.parse_args(); a.study=a.study.resolve()
    wall=a.study/'wall16'
    deadline=time.monotonic()+1800
    while not (wall/'state.json').exists():
        if time.monotonic()>deadline: raise TimeoutError('Prior wall suite has not completed')
        time.sleep(5)
    if json.loads((wall/'state.json').read_text())['status']!='completed': raise RuntimeError('Prior suite failed')
    device=json.loads((wall/'config.json').read_text())['device']
    with (a.study/'followup-build.log').open('w') as log:
        for command in [['bash','demos/hdmi_wall/build.sh'],['bash','demos/yolov8/build.sh'],['cmake','--build','tools/diagnostics/build','-j2'],['ctest','--test-dir','demos/hdmi_wall/build','--output-on-failure'],['ctest','--test-dir','demos/yolo26/build','--output-on-failure']]:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    paired=a.study/'paired'; paired.mkdir()
    for repeat in range(2):
        for mode in (['serial','paired'] if repeat==0 else ['paired','serial']):
            run_stage(paired,f'r{repeat}_{mode}',[ROOT/'tools/diagnostics/build/duplex_probe.pcie',device,2822400,15,3,mode,'{stage}/result.json'],device)
    admission=a.study/'admission'; admission.mkdir()
    base=json.loads((wall/'r0_preview10/command.json').read_text())
    for key,value in [('--output','{stage}/worker'),('--readback-control','{stage}/readback.json'),('--duration','20'),('--window','20')]:
        base[base.index(key)+1]=value
    for repeat in range(2):
        for limit in ([0,2,4,8] if repeat==0 else [8,4,2,0]):
            run_stage(admission,f'r{repeat}_limit{limit}',base+['--active-limit',str(limit)],device,readback=True)
    # All-frame YOLOv8 correctness and throughput; archived baseline remains unchanged.
    subprocess.run([sys.executable,str(ROOT/'tools/diagnostics/run_yolov8_readback_comparison.py'),
        '--device',str(device),'--legacy-app',str(a.study/'yolov8_baseline.pcie'),
        '--output',str(a.study/'yolov8_8'),'--streams','8','--warmup','10','--duration','30'],check=True,timeout=240)
    compact=a.study/'yolo26_probe'; compact.mkdir()
    model=ROOT/'third_party/sophon-demo/sample/YOLO26/models/BM1684X/yolo26s_fp32_1b.bmodel'
    for repeat in range(2):
        for mode in (['compute','compute-copy','overlap'] if repeat==0 else ['overlap','compute-copy','compute']):
            run_stage(compact,f'r{repeat}_{mode}',[ROOT/'tools/diagnostics/build/inference_probe.pcie',device,model,mode,3,15,'{stage}/gate.txt','{stage}/result.json',0],device,barrier=True)
    video=a.study/'yolo26_video'; video.mkdir()
    for limit in [0,4]:
        command=[ROOT/'demos/yolo26/build/yolo26_streams.pcie','--device',device,'--streams',16,
            '--input',ROOT/'data/inputs/highway_1080p25.mp4','--bmodel',model,'--classnames',ROOT/'data/models/coco.names',
            '--output','{stage}/worker','--warmup',5,'--duration',20,'--window',20,'--local-eof','loop',
            '--image-path','bgr','--pace-fps',0,'--active-limit',limit]
        run_stage(video,f'limit{limit}',command,device)
    subprocess.run([sys.executable,str(ROOT/'demos/decode/stress_decode.py'),'--device',str(device),'--steps','16',
        '--input',str(ROOT/'data/inputs/highway_1080p25.mp4'),'--warmup','5','--duration','30','--window','10',
        '--ffmpeg','/opt/sophon/sophon-ffmpeg-latest/bin/ffmpeg','--ffprobe','/opt/sophon/sophon-ffmpeg-latest/bin/ffprobe',
        '--output',str(a.study/'decode16')],check=True,timeout=120)
    save(a.study/'followup-state.json',{'status':'completed'})


if __name__=='__main__': main()
