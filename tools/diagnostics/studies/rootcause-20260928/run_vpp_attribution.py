"""Temporary paired API/driver observation with independent live status sampling."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from argparse import Namespace

ROOT=Path('/userdata/1684X-EP-demo')
sys.path.insert(0,str(ROOT/'tools/diagnostics'))
os.environ['PATH']='/opt/sophon/libsophon-current/bin:'+os.environ['PATH']
from run_pipeline32_comparison import command,preflight,summarize
from run_readback_study import run_stage,save
from run_dispatch_wait_ab import Capture,TRACE,KEYS,GROUPS

out=ROOT/'data/results/rootcause-20260928/vpp-attribution-v2'
configs=[Namespace(device=d,bdf=b,expected_link_speed=s,repeats=0,streams=32,warmup=30,duration=90,
    input=ROOT/'data/inputs/hdmi_wall_demo_loop_2400s.mp4',
    bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
    classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
    for d,b,s in [(0,'0002:21:00.0','5.0'),(2,'0003:31:00.0','8.0')]]
evidence=[preflight(c) for c in configs]
out.mkdir(exist_ok=False)
binary=ROOT/'demos/hdmi_wall/build-async/hdmi_wall.pcie'
observer=out.parent/'vpp_attribution.so'
save(out/'preflight.json',{'devices':evidence,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
    'observer_sha256':hashlib.sha256(observer.read_bytes()).hexdigest()})
save(out/'state.json',{'status':'running'})
rows=[];stop=threading.Event();errors=[]

def measure(cfg,case):
    name=f'{case}_device{cfg.device}'
    cmd=command(cfg,'wall_preview');cmd[0]=str(binary);cmd+=['--preview-mode','sync']
    traces=out/f'events_{name}';traces.mkdir()
    cmd=['env','LD_PRELOAD='+str(observer),'PREPROCESS_TRACE_DIR='+str(traces)]+cmd
    run_stage(out,name,cmd,cfg.device,timeout=320,readback=True)
    row=summarize(out/name,'wall_preview',32)
    data=json.loads((out/name/'worker/summary.json').read_text())
    row.update(device=cfg.device,configuration=case,
        maximum_no_result_interval_s=max(s['analysis_completion_continuity']['max_gap_including_edges_s'] for s in data['streams']))
    save(out/name/'comparison.json',row)
    return row

def monitor():
    with (out/'status-samples.jsonl').open('w') as f:
        while not stop.is_set():
            row={'monotonic':time.monotonic(),'status':{}}
            for p in out.glob('*/worker/status.json'):
                try:row['status'][p.parent.parent.name]=json.loads(p.read_text())
                except (OSError,ValueError):pass
            f.write(json.dumps(row)+'\n');f.flush();stop.wait(.25)

class TraceCapture(Capture):
    def prepare(self):
        for key in ('options/funcgraph-tail','options/funcgraph-irqs'):
            self.old[key]=(TRACE/key).read_text().strip()
        save(out/'trace-original.json',dict(self.old,clock=self.clock))
        roots,funcs=GROUPS['vpp']
        assert all(f in self.available for f in funcs)
        self.put('tracing_on',0);self.put('set_ftrace_filter','\n'.join(funcs))
        self.put('set_graph_function','\n'.join(roots));self.put('max_graph_depth',12)
        self.put('buffer_size_kb',16384);self.put('trace_clock','mono')
        self.put('current_tracer','function_graph')
        for k in ('options/funcgraph-abstime','options/funcgraph-proc','options/funcgraph-duration','options/sleep-time','options/funcgraph-tail'):
            self.put(k,1)
        self.put('options/funcgraph-irqs',0)
        save(out/'trace-config.json',{k:(TRACE/k).read_text().strip() for k in KEYS+[
            'options/funcgraph-tail','options/funcgraph-irqs','set_ftrace_filter','set_graph_function','trace_clock']})
    def capture(self,index):
        self.put('trace','')
        meta={'before_enable_monotonic':time.monotonic()}
        self.put('tracing_on',1);meta['after_enable_monotonic']=time.monotonic()
        if stop.wait(8):raise RuntimeError('business ended during capture')
        meta['before_disable_monotonic']=time.monotonic();self.put('tracing_on',0)
        meta['after_disable_monotonic']=time.monotonic()
        meta['cpu_stats']={p.parent.name:p.read_text() for p in (TRACE/'per_cpu').glob('cpu*/stats')}
        (out/f'vpp{index}.trace').write_text((TRACE/'trace').read_text())
        meta['after_export_monotonic']=time.monotonic()
        save(out/f'vpp{index}.capture.json',meta)
        print('capture complete',index,flush=True)

def trace_worker(trace):
    try:
        while not stop.is_set():
            good=[]
            for dev in (0,2):
                try:
                    j=json.loads((out/f'traced_device{dev}/worker/status.json').read_text())
                    good.append(j['results_readback_completed']>32)
                except (OSError,ValueError,KeyError):good.append(False)
            if all(good):break
            stop.wait(.25)
        anchor=time.monotonic();save(out/'trace-anchor.json',{'monotonic':anchor})
        for index,offset in enumerate((50,100)):
            if stop.wait(max(0,anchor+offset-time.monotonic())):raise RuntimeError('business stopped before trace')
            trace.capture(index)
    except BaseException as exc:errors.append(repr(exc))

monitor_thread=threading.Thread(target=monitor);monitor_thread.start()
trace=None
try:
    for case,duration in [('before',90),('traced',120),('after',90)]:
        for c in configs:c.duration=duration
        tracer=None
        if case=='traced':
            trace=TraceCapture();trace.prepare()
            tracer=threading.Thread(target=trace_worker,args=(trace,));tracer.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(measure,c,case) for c in configs]
            for f in fs:
                row=f.result();rows.append(row);save(out/'comparison.json',rows)
                print(json.dumps({k:row[k] for k in ('stage','fps','tpu_percent','zero_streams','dropped_stale_lifetime','stage_mean_ms')}),flush=True)
        if tracer:
            tracer.join();trace.restore();trace=None
            save(out/'trace-restored.json',{k:(TRACE/k).read_text().strip() for k in ('current_tracer','set_ftrace_filter','set_graph_function')})
        if errors:raise RuntimeError(errors)
    save(out/'state.json',{'status':'completed'})
except BaseException as exc:
    save(out/'state.json',{'status':'failed','error':repr(exc)});raise
finally:
    stop.set();monitor_thread.join()
    if trace:trace.restore()
