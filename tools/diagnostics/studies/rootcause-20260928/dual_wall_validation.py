"""Temporary experiment orchestration; reuses existing, deployed diagnostics."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from argparse import Namespace

ROOT = Path('/userdata/1684X-EP-demo')
sys.path.insert(0, str(ROOT/'tools/diagnostics'))
os.environ['PATH'] = '/opt/sophon/libsophon-current/bin:' + os.environ['PATH']
from run_pipeline32_comparison import command, preflight, summarize
from run_readback_study import run_stage, save

out = ROOT/'data/results/rootcause-20260928/dual-wall-validation'
configs = []
for device, bdf, speed in [(0,'0002:21:00.0','5.0'),(2,'0003:31:00.0','8.0')]:
    configs.append(Namespace(device=device,bdf=bdf,expected_link_speed=speed,repeats=0,
        streams=32,warmup=30,duration=60,
        input=ROOT/'data/inputs/hdmi_wall_demo_loop_2400s.mp4',
        bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
        classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel'))
evidence = [preflight(c) for c in configs]
out.mkdir(exist_ok=False)
binary = ROOT/'demos/hdmi_wall/build-async/hdmi_wall.pcie'
save(out/'preflight.json', {'devices':evidence,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
cases=[('vpp10','sync',10),('async3repeat','async',3),('sync10repeat','sync',10)]
save(out/'plan.json',cases)
save(out/'state.json',{'status':'running'})
rows=[]
def measure(cfg,case,mode,rate):
    name=f'{case}_device{cfg.device}'
    cmd=command(cfg,'wall_preview')
    cmd[0]=str(binary)
    cmd += ['--preview-mode',mode]
    if case == 'vpp10': cmd += ['--preview-stage','vpp']
    cmd[cmd.index('--preview-fps')+1]=str(rate)
    run_stage(out,name,cmd,cfg.device,timeout=255,readback=True)
    row=summarize(out/name,'wall_preview',32)
    data=json.loads((out/name/'worker/summary.json').read_text())
    row.update(device=cfg.device,configuration=case,
        maximum_no_result_interval_s=max(s['analysis_completion_continuity']['max_gap_including_edges_s'] for s in data['streams']))
    save(out/name/'comparison.json',row)
    return row
stop=threading.Event()
def monitor():
    with (out/'host-and-status.jsonl').open('w') as f:
        while not stop.is_set():
            row={'monotonic':time.monotonic(),'proc_stat':Path('/proc/stat').read_text(),
                 'meminfo':Path('/proc/meminfo').read_text(),'loadavg':Path('/proc/loadavg').read_text(),'status':{}}
            for p in out.glob('*/worker/status.json'):
                try: row['status'][p.parent.parent.name]=json.loads(p.read_text())
                except (OSError,ValueError): pass
            f.write(json.dumps(row)+'\n'); f.flush()
            stop.wait(1)
t=threading.Thread(target=monitor);t.start()
try:
    for case,mode,rate in cases:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(measure,c,case,mode,rate) for c in configs]
            for future in futures:
                row=future.result();rows.append(row)
                save(out/'comparison.json',rows)
                print(json.dumps(row),flush=True)
    save(out/'state.json',{'status':'completed'})
except BaseException as exc:
    save(out/'state.json',{'status':'failed','error':repr(exc)})
    raise
finally:
    stop.set();t.join()
