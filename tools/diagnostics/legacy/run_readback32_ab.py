#!/usr/bin/env python3
"""32-stream readback ablation on original footage, forward and reverse order."""
import argparse
from argparse import Namespace
import json
import signal
from pathlib import Path
import threading
import time

from run_pipeline32_comparison import ROOT, command, preflight, report, summarize
from run_readback_study import run_stage, save


def interrupted(*_):
    raise KeyboardInterrupt('requested stop')


def snapshots(folder, stop):
    with (folder/'status-snapshots.jsonl').open('w') as output:
        while not stop.wait(1):
            try:
                row=json.loads((folder/'worker/status.json').read_text())
                output.write(json.dumps(dict(monotonic=time.monotonic(),status=row))+'\n')
                output.flush()
            except (OSError,ValueError):
                pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--device',type=int,default=0)
    p.add_argument('--bdf',default='0002:21:00.0')
    p.add_argument('--warmup',type=int,default=30)
    p.add_argument('--duration',type=int,default=120)
    p.add_argument('--repeats',type=int,default=2)
    a=p.parse_args()
    signal.signal(signal.SIGTERM,interrupted)
    if min(a.warmup,a.duration)<1 or a.repeats < 1:
        p.error('Positive timing and repeats required')
    a.input=a.input.resolve(); a.output=a.output.resolve()
    cfg=Namespace(**{**vars(a),'repeats':0},streams=32,
        bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
        classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
    evidence=preflight(cfg)
    a.output.mkdir(parents=True,exist_ok=False)
    save(a.output/'preflight.json',evidence)
    save(a.output/'state.json',{'status':'running'})
    cases=[('full','off',0,0,True,'reuse'),
           ('gate0','on',0,0,True,'reuse'),
           ('gate64','on',64,0,True,'reuse'),
           ('gate256','on',256,0,True,'reuse'),
           ('preview3','on',64,3,True,'reuse'),
           ('preview10','on',64,10,True,'reuse'),
           ('baseline','on',64,10,True,'baseline'),
           ('model_only','on',64,0,False,'reuse')]
    plan=[(rep,case) for rep in range(a.repeats)
          for case in (cases if rep%2==0 else list(reversed(cases)))]
    save(a.output/'plan.json',plan)
    rows=[]
    try:
        for rep,(case,gate,budget,rate,readback,buffer) in plan:
            name=f'r{rep}_{case}'
            cmd=command(cfg,'wall_preview')
            for key,value in [('--preview-fps',rate),('--score-gate',gate),
                              ('--gate-merge-budget-kib',budget),('--output-buffer',buffer),
                              ('--cpu-post','selected' if gate=='on' else 'dense')]:
                cmd[cmd.index(key)+1]=str(value)
            # run_stage owns this directory; snapshot monitor tolerates its
            # creation/startup and retains the source clock reported by worker.
            stop=threading.Event()
            def watch():
                while not (a.output/name).exists():
                    if stop.wait(.1): return
                snapshots(a.output/name,stop)
            watcher=threading.Thread(target=watch)
            watcher.start()
            try:
                run_stage(a.output,name,cmd,a.device,timeout=a.warmup+a.duration+180,readback=readback)
            finally:
                stop.set(); watcher.join()
            row=summarize(a.output/name,'wall_preview',32)
            streams=json.loads((a.output/name/'worker/summary.json').read_text())['streams']
            row.update(configuration=case,repeat=rep,readback_enabled=readback,preview_fps_cap=rate,
                maximum_no_result_interval_s=max(s['analysis_completion_continuity']['max_gap_including_edges_s'] for s in streams))
            save(a.output/name/'comparison.json',row)
            rows.append(row)
            report(a.output,rows)
            print(json.dumps(row),flush=True)
        save(a.output/'state.json',{'status':'completed'})
    except BaseException as exc:
        save(a.output/'state.json',{'status':'failed','error':str(exc)})
        raise


if __name__=='__main__':main()
