#!/usr/bin/env python3
"""Compare archived YOLOv8 and shared detector, preserving same-source-frame outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from run_readback_study import ROOT, save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',type=int,required=True)
    parser.add_argument('--legacy-app',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--streams',type=int,default=8)
    parser.add_argument('--duration',type=int,default=30)
    parser.add_argument('--warmup',type=int,default=10)
    parser.add_argument('--variants',nargs='+',choices=['legacy_full','shared_full','shared_gate64'],default=['legacy_full','shared_full','shared_gate64'])
    a=parser.parse_args()
    if a.device<0 or not 1<=a.streams<=32 or min(a.duration,a.warmup)<=0: parser.error('Invalid limits')
    if 'legacy_full' not in a.variants or len(set(a.variants))!=len(a.variants): parser.error('Include legacy_full once for same-frame comparison')
    a.output.mkdir(parents=True,exist_ok=False)
    app=ROOT/'demos/yolov8/build/pipeline_worker.pcie'
    save(a.output/'config.json',dict(device=a.device,streams=a.streams,duration=a.duration,warmup=a.warmup,
         legacy_sha256=hashlib.sha256(a.legacy_app.read_bytes()).hexdigest(),shared_sha256=hashlib.sha256(app.read_bytes()).hexdigest()))
    results=[]
    for name,exe,buffer,gate in [('legacy_full',a.legacy_app,'baseline','off'),('shared_full',app,'reuse','off'),('shared_gate64',app,'reuse','on')]:
        if name not in a.variants: continue
        folder=a.output/name; folder.mkdir(); children=[]; logs=[]; commands=[]
        print(name,flush=True)
        try:
            for i in range(a.streams):
                target=folder/f'stream_{i:02d}'; target.mkdir()
                cmd=[str(exe),f'--device={a.device}',f'--input={ROOT}/data/inputs/highway_1080p25.mp4',
                     f'--bmodel={ROOT}/third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
                     f'--classnames={ROOT}/data/models/coco.names',f'--output={target.resolve()}',
                     '--fps=25','--mode=analysis','--image_path=device-bgr',f'--output_buffer={buffer}']
                if name!='legacy_full': cmd += [f'--score_gate={gate}',f'--score_gate_model={ROOT}/data/models/score_gate_reducemax_f32.bmodel','--gate_merge_budget_kib=64']
                log=(target/'worker.log').open('w'); logs.append(log); commands.append(cmd)
                children.append(subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
            save(folder/'commands.json',commands)
            start=time.monotonic()+a.warmup; end=start+a.duration
            with (folder/'telemetry.jsonl').open('w') as out:
                while time.monotonic()<end:
                    if any(p.poll() is not None for p in children): raise RuntimeError('Worker exited early')
                    tick=time.monotonic()
                    sample=subprocess.run(['bm-smi','--noloop','--text_format',f'--start_dev={a.device}',f'--last_dev={a.device}'],text=True,capture_output=True,timeout=5)
                    out.write(json.dumps({'monotonic':tick,'returncode':sample.returncode,'smi':sample.stdout})+'\n'); out.flush()
                    time.sleep(max(0,1-(time.monotonic()-tick)))
        finally:
            for p in children:
                if p.poll() is None: os.killpg(p.pid,signal.SIGTERM)
            for p in children:
                try: p.wait(timeout=15)
                except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()
            for f in logs: f.close()
        if any(p.returncode for p in children): raise RuntimeError('Worker shutdown failed')
        frames=[]; copy=[]; inference=[]; counts=[]
        for stream in sorted(folder.glob('stream_*')):
            rows=[json.loads(line) for line in (stream/'detections.jsonl').read_text().splitlines()]
            if [r['frame'] for r in rows]!=list(range(len(rows))): raise RuntimeError('Non-contiguous output')
            if not rows or rows[0]['completed_monotonic_s']>=start: raise RuntimeError('Worker was not initialized before formal window')
            summary=json.loads((stream/'worker_summary.json').read_text())
            if summary['frames_analyzed']!=len(rows): raise RuntimeError('Count mismatch')
            selected=[r for r in rows if start<=r['completed_monotonic_s']<end]
            counts.append(len(selected)); frames.extend(selected)
            copy.extend(r['output_copy_ms'] for r in selected); inference.extend(r['inference_ms'] for r in selected)
        import re
        samples=[]
        for line in (folder/'telemetry.jsonl').read_text().splitlines():
            r=json.loads(line); values=re.findall(r'(\d+)%',r['smi'])
            if start<=r['monotonic']<end and r['returncode']==0 and len(values)==1: samples.append(int(values[0]))
        result={'name':name,'fps':len(frames)/a.duration,'minimum_stream_fps':min(counts)/a.duration,
                'copy_mean_ms':sum(copy)/len(copy),'inference_mean_ms':sum(inference)/len(inference),
                'tpu_mean_percent':sum(samples)/len(samples) if samples else None,
                'tpu_samples':len(samples),'measurement_start':start,'measurement_end':end}
        save(folder/'result.json',result); results.append(result)
    comparisons=[]
    for name in ['shared_full','shared_gate64']:
        if name not in a.variants: continue
        matched=0; differing=0; examples=[]
        for i in range(a.streams):
            def read(variant):
                return [json.loads(line) for line in (a.output/variant/f'stream_{i:02d}/detections.jsonl').read_text().splitlines()]
            left,right=read('legacy_full'),read(name)
            for x,y in zip(left,right):
                matched+=1
                if x['detections']!=y['detections']:
                    differing+=1
                    if len(examples)<5: examples.append({'stream':i,'frame':x['frame'],'old':x['detections'],'new':y['detections']})
        comparisons.append({'variant':name,'matched_frames':matched,'different_frames':differing,'examples':examples})
    save(a.output/'summary.json',{'results':results,'same_frame_comparisons':comparisons})
    print(json.dumps(results),flush=True)


if __name__=='__main__': main()
