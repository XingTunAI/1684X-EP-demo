"""Same device/slot 8→5→8GT/s format ablation, restoring original link in finally."""
from argparse import Namespace
from pathlib import Path
import hashlib,json,os,signal,subprocess,sys,threading,time

ROOT=Path('/userdata/1684X-EP-demo');BASE=ROOT/'data/results/rootcause-20260928'
sys.path.insert(0,str(ROOT/'tools/diagnostics'))
os.environ['PATH']='/opt/sophon/libsophon-current/bin:'+os.environ['PATH']
from run_readback_study import run_stage,save
from run_pipeline32_comparison import command,preflight,summarize
from run_dispatch_wait_ab import conflicts

def cmd(*args):return subprocess.check_output(args,text=True,timeout=15).strip()
def interrupted(*_):raise KeyboardInterrupt('stop requested; restoring link')
for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
cfg=Namespace(device=2,bdf='0003:31:00.0',expected_link_speed='8.0',streams=32,repeats=0,warmup=30,duration=60,
    input=ROOT/'data/inputs/hdmi_wall_demo_loop_2400s.mp4',
    bmodel=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
    classnames=ROOT/'data/models/coco.names',gate_model=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
evidence=preflight(cfg)
endpoint=Path('/sys/bus/pci/devices')/cfg.bdf;port='0003:30:00.0'
original=cmd('setpci','-s',port,'CAP_EXP+30.w');assert int(original,16)&15==3
assert Path('/sys/kernel/debug/tracing/current_tracer').read_text().strip()=='nop'
out=BASE/'samecard-format';out.mkdir(exist_ok=False)
binary=ROOT/'demos/hdmi_wall/build-async/hdmi_wall.pcie'
save(out/'preflight.json',{'device':evidence,'port':port,'original_target':original,
    'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [binary,BASE/'vpp_attribution.so',BASE/'decoder_format_override_v2.so']}})
plan=[('b0',8,[101,0]),('b1',5,[0,101]),('b2',8,[101,0])]
save(out/'plan.json',{'batches':plan,'extra_buffers':8,'warmup':30,'duration':60,
    'reason':'Separate link rate from physical card/slot. Other cards idle. Temporary per-process format override, no driver replacement.'})
state={'status':'running'};save(out/'state.json',state)
stop=threading.Event();rows=[]
def monitor():
    with (out/'host-and-status.jsonl').open('w') as f:
        while not stop.is_set():
            item={'monotonic':time.monotonic(),'status':{}}
            for p in out.glob('*/worker/status.json'):
                try:item['status'][p.parent.parent.name]=json.loads(p.read_text())
                except (OSError,ValueError):pass
            f.write(json.dumps(item)+'\n');f.flush();stop.wait(1)
t=threading.Thread(target=monitor);t.start()
def speed(target):
    assert not conflicts(),conflicts()
    cmd('setpci','-s',port,'CAP_EXP+30.w='+target+':000f')
    cmd('setpci','-s',port,'CAP_EXP+10.w=0020:0020')
    time.sleep(2)
    actual=(endpoint/'current_link_speed').read_text().strip()
    expected='8.0' if int(target,16)&15==3 else '5.0'
    assert actual.startswith(expected),actual
    assert (endpoint/'current_link_width').read_text().strip()=='1'
    return actual
try:
    for batch,rate,formats in plan:
        actual=speed('0003' if rate==8 else '0002')
        for fmt in formats:
            case=('linear_' if fmt==0 else 'compressed_')+batch;name=case+'_device2'
            state.update(stage=name,actual_speed=actual);save(out/'state.json',state)
            events=out/f'events_{name}';events.mkdir()
            c=command(cfg,'wall_preview');c[0]=str(binary);c+=['--preview-mode','sync']
            c=['env','LD_PRELOAD='+str(BASE/'vpp_attribution.so')+':'+str(BASE/'decoder_format_override_v2.so'),
                'PREPROCESS_TRACE_DIR='+str(events),'VPP_DIAG_OUTPUT_FORMAT='+str(fmt),'VPP_DIAG_EXTRA_FRAMES=8']+c
            run_stage(out,name,c,2,timeout=255,readback=True)
            row=summarize(out/name,'wall_preview',32);s=json.loads((out/name/'worker/summary.json').read_text())
            row.update(device=2,configuration=case,link_gt_s=rate,
                maximum_no_result_interval_s=max(x['analysis_completion_continuity']['max_gap_including_edges_s'] for x in s['streams']))
            save(out/name/'comparison.json',row);rows.append(row);save(out/'comparison.json',rows)
            print(json.dumps({k:row[k] for k in ('stage','link_gt_s','fps','tpu_percent','zero_streams','dropped_stale_lifetime','maximum_no_result_interval_s','stage_mean_ms')}),flush=True)
    state['status']='completed'
except BaseException as exc:state.update(status='failed',error=repr(exc));raise
finally:
    stop.set();t.join()
    state['restored_speed']=speed(original)
    state['restored_target']=cmd('setpci','-s',port,'CAP_EXP+30.w')
    save(out/'state.json',state)
