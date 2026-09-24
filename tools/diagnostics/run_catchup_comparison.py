#!/usr/bin/env python3
"""Compare preserved baseline and opt-in local keyframe catchup, swapping Gen2 cards."""
import argparse
import hashlib
from argparse import Namespace
import json
from pathlib import Path
import subprocess
import sys

from run_pipeline32_comparison import ROOT, command, preflight, report
from run_readback_study import save
from run_dual_pipeline32 import stop


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--index', type=Path, required=True)
    p.add_argument('--streams', type=int, default=32)
    p.add_argument('--warmup', type=int, default=15)
    p.add_argument('--duration', type=int, default=60)
    a = p.parse_args()
    if not 1 <= a.streams <= 32 or min(a.warmup, a.duration) < 1:
        p.error('Invalid streams or timing')
    a.output = a.output.resolve()
    index = json.loads(a.index.read_text())
    base = dict(streams=a.streams,warmup=a.warmup,duration=a.duration,repeats=0,
                input=Path(index['source']),
                bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
                classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
    bdfs = ['0002:21:00.0','0004:41:00.0']
    evidence = [preflight(Namespace(**base,device=d,bdf=bdfs[d])) for d in (0,1)]
    a.output.mkdir(parents=True,exist_ok=False)
    save(a.output/'preflight.json',evidence)
    plan = []
    for turn in (0,1):
        for device in (0,1):
            fixed = device != turn
            label = 'catchup' if fixed else 'baseline'
            cmd = command(Namespace(**base,device=device), 'wall_preview')
            cmd[0] = str(ROOT/('demos/hdmi_wall/build-catchup/hdmi_wall.pcie' if fixed else '.tools/catchup-baseline/hdmi_wall.pcie'))
            if fixed:
                cmd += ['--local-catchup-index',str(a.index.resolve())]
            plan.append(dict(root=str(a.output),name=f'r{turn}_d{device}_{label}',case='wall_preview',
                             peer_case='baseline' if fixed else 'catchup',pair=turn,device=device,bdf=bdfs[device],
                             streams=a.streams,timeout=a.warmup+a.duration+180,command=cmd))
    save(a.output/'plan.json',plan)
    paths = {item['command'][0] for item in plan} | {str(a.index.resolve()),str(Path(__file__).resolve())}
    save(a.output/'artifacts.json', [dict(path=f,sha256=hashlib.sha256(Path(f).read_bytes()).hexdigest()) for f in sorted(paths)])
    save(a.output/'state.json',{'status':'running'})
    rows = []
    for offset in (0,2):
        children, logs = [], []
        try:
            for i in (offset,offset+1):
                print(plan[i]['name'],flush=True)
                log = (a.output/(plan[i]['name']+'.log')).open('w')
                logs.append(log)
                children.append(subprocess.Popen([sys.executable,str(ROOT/'tools/diagnostics/run_dual_pipeline32.py'),
                    '--worker-plan',str(a.output/'plan.json'),'--worker-index',str(i)],
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
            for child in children:
                child.wait()
            if any(c.returncode for c in children):
                save(a.output/'state.json',{'status':'failed','completed_stages':len(rows)})
                raise RuntimeError('Comparison failed; inspect retained stage logs')
        finally:
            stop(children)
            for log in logs: log.close()
        for i in (offset,offset+1):
            rows.append(json.loads((a.output/plan[i]['name']/'comparison.json').read_text()))
        report(a.output,rows)
    save(a.output/'state.json',{'status':'completed'})


if __name__ == '__main__':
    main()
