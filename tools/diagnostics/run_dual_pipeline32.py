#!/usr/bin/env python3
"""Cross over two Gen2 x1 cards under explicitly concurrent host load."""
import argparse
from argparse import Namespace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from run_pipeline32_comparison import ROOT, command, preflight, report, summarize
from run_readback_study import run_stage, save

PAIRS = [('encode_bgr','wall_preview'), ('analysis_device','wall_preview'),
         ('wall_preview_no_age_limit','wall_preview'),
         ('analysis_bgr','encode_bgr'), ('wall_no_preview','wall_preview')]


def interrupted(*_):
    raise KeyboardInterrupt('requested stop')


def worker(plan_path, index):
    item = json.loads(plan_path.read_text())[index]
    root = Path(item['root'])
    signal.signal(signal.SIGTERM, interrupted)
    try:
        run_stage(root,item['name'],item['command'],item['device'],timeout=item['timeout'],
                  readback=True if item['case'].startswith('wall_') else None)
        result = summarize(root/item['name'],item['case'],item['streams'])
        result.update(device=item['device'],bdf=item['bdf'],pair=item['pair'],
                      concurrent_peer_case=item['peer_case'],scope='two-card concurrent host load')
        save(root/item['name']/'comparison.json',result)
    except BaseException as exc:
        save(root/item['name']/'comparison_failure.json',{'error':str(exc) or type(exc).__name__})
        raise


def stop(children):
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid,signal.SIGINT)
    for child in children:
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL)
            child.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path)
    p.add_argument('--devices',type=int,nargs=2,default=[0,1])
    p.add_argument('--bdfs',nargs=2,default=['0002:21:00.0','0004:41:00.0'])
    p.add_argument('--warmup',type=int,default=30)
    p.add_argument('--duration',type=int,default=60)
    p.add_argument('--streams',type=int,default=32)
    p.add_argument('--pairs',type=int,nargs='+',default=list(range(len(PAIRS))))
    p.add_argument('--turns',type=int,nargs='+',choices=[0,1],default=[0,1])
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--worker-plan',type=Path)
    p.add_argument('--worker-index',type=int)
    a = p.parse_args()
    if a.worker_plan:
        return worker(a.worker_plan,a.worker_index)
    if a.output is None or len(set(a.devices)) != 2 or len(set(a.bdfs)) != 2:
        p.error('Need a new output directory and two distinct cards')
    if min(a.warmup,a.duration) < 1 or not 1 <= a.streams <= 32 or any(i not in range(len(PAIRS)) for i in a.pairs):
        p.error('Invalid timing, stream count or pair index')
    a.output = a.output.resolve()
    base = dict(streams=a.streams,warmup=a.warmup,duration=a.duration,repeats=4,
                input=ROOT/'data/inputs/highway_1080p25.mp4',
                bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
                classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
    plan = []
    for pair in a.pairs:
        for turn in a.turns:
            cases = PAIRS[pair] if turn == 0 else tuple(reversed(PAIRS[pair]))
            for side,(device,bdf) in enumerate(zip(a.devices,a.bdfs)):
                case = cases[side]
                cfg = Namespace(**base,device=device,bdf=bdf)
                plan.append(dict(root=str(a.output),name=f'p{pair}_r{turn}_d{device}_{case}',
                                 case=case,peer_case=cases[1-side],pair=pair,turn=turn,
                                 device=device,bdf=bdf,streams=a.streams,
                                 command=command(cfg,case),timeout=a.warmup+a.duration+600))
    if a.dry_run:
        print(json.dumps(plan,indent=2))
        return
    if a.output.exists():
        p.error('Output already exists')
    evidence = [preflight(Namespace(**base,device=d,bdf=b)) for d,b in zip(a.devices,a.bdfs)]
    a.output.mkdir(parents=True)
    save(a.output/'preflight.json',evidence)
    save(a.output/'plan.json',plan)
    save(a.output/'state.json',{'status':'running','scope':'dual-card concurrent crossover'})
    signal.signal(signal.SIGTERM,interrupted)
    rows,failures,overlaps = [],[],[]
    try:
        for offset in range(0,len(plan),2):
            children,logs = [],[]
            try:
                for index in (offset,offset+1):
                    item = plan[index]
                    print(item['name'],flush=True)
                    log = (a.output/(item['name']+'.log')).open('w')
                    logs.append(log)
                    children.append(subprocess.Popen([sys.executable,__file__,'--worker-plan',str(a.output/'plan.json'),
                                                       '--worker-index',str(index)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
                while any(child.poll() is None for child in children):
                    time.sleep(1)
            finally:
                stop(children)
                for log in logs:
                    log.close()
            current = []
            for index,child in zip((offset,offset+1),children):
                item = plan[index]
                file = a.output/item['name']/'comparison.json'
                if child.returncode or not file.exists():
                    failures.append({'stage':item['name'],'exit_code':child.returncode})
                else:
                    result = json.loads(file.read_text())
                    current.append(result)
                    rows.append(result)
            if len(current) == 2:
                start = max(r['measurement_start'] for r in current)
                end = min(r['measurement_end'] for r in current)
                overlaps.append({'stages':[r['stage'] for r in current],
                                 'formal_overlap_seconds':max(0,end-start),
                                 'start_offset_seconds':abs(current[0]['measurement_start']-current[1]['measurement_start'])})
            report(a.output,rows)
            save(a.output/'overlap.json',overlaps)
            save(a.output/'state.json',{'status':'running','completed_stages':len(rows),'failures':failures})
            # Verify no orphan hardware work remains before starting another pair.
            if failures:
                raise RuntimeError('A concurrent stage failed; inspect logs before continuing')
        save(a.output/'state.json',{'status':'completed','completed_stages':len(rows),'failures':failures})
    except BaseException as exc:
        save(a.output/'state.json',{'status':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
                                  'error':str(exc),'completed_stages':len(rows),'failures':failures})
        raise


if __name__ == '__main__':
    main()
