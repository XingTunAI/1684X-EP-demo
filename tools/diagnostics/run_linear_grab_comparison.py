#!/usr/bin/env python3
"""Compare the opt-in pre-retrieve selection path with the unchanged path."""
import argparse,json,os,subprocess,hashlib
from pathlib import Path
from run_readback_study import ROOT,run_stage,save
from run_pipeline32_comparison import summarize

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--duration',type=int,default=60);ap.add_argument('--streams',type=int,default=32)
    ap.add_argument('--device',type=int,default=0);ap.add_argument('--steps',default='1,2,2,1')
    a=ap.parse_args();steps=[int(x) for x in a.steps.split(',')]
    if not 1<=a.streams<=32 or a.duration<1 or any(not 1<=x<=120 for x in steps):ap.error('invalid test parameters')
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                if (p/'exe').resolve().name in ('hdmi_wall.pcie','pipeline_worker.pcie'):raise RuntimeError('Existing inference process: '+p.name)
            except FileNotFoundError:pass
    os.environ['PATH']='/opt/sophon/libsophon-current/bin:'+os.environ['PATH']
    a.output.mkdir(parents=True,exist_ok=False);save(a.output/'state.json',{'status':'running'})
    save(a.output/'metadata.json',{'device':a.device,'streams':a.streams,'duration':a.duration,'steps':steps,
        'binary_sha256':hashlib.sha256((ROOT/'demos/hdmi_wall/build-grab-select/hdmi_wall.pcie').read_bytes()).hexdigest(),
        'note':'Linear output format 0, extra buffers 8, preview 10, warmup 30; ABBA stride comparison.'})
    rows=[]
    try:
        for i,step in enumerate(steps):
            name=f'r{i}_every{step}'
            command=[ROOT/'demos/hdmi_wall/build-grab-select/hdmi_wall.pcie',
                '--device',a.device,'--streams',a.streams,'--input',ROOT/'data/inputs/hdmi_wall_demo_loop_2400s.mp4',
                '--bmodel',ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
                '--classnames',ROOT/'data/models/coco.names','--score-gate-model',ROOT/'data/models/score_gate_reducemax_f32.bmodel',
                '--output','{stage}/worker','--warmup',30,'--duration',a.duration,'--window',10,
                '--local-eof','fail','--policy','latest','--infer-fps',0,'--max-frame-age-ms',250,
                '--image-path','auto','--record-mode','summary','--prime-local-decoders','on',
                '--output-buffer','reuse','--score-gate','on','--cpu-post','selected',
                '--gate-merge-budget-kib',64,'--preview-fps',10,'--retrieve-every',step]
            command=['env','LD_PRELOAD='+str(ROOT/'data/results/rootcause-20260928/decoder_format_override_v2.so'),'VPP_DIAG_OUTPUT_FORMAT=0','VPP_DIAG_EXTRA_FRAMES=8']+command
            run_stage(a.output,name,command,a.device,timeout=a.duration+120)
            s=json.loads((a.output/name/'worker/summary.json').read_text());ss=s['streams']
            if s['status'] != 'measured' or not s['accounting_complete']:raise RuntimeError('Incomplete measurement/accounting')
            row={'stage':name,'retrieve_every':step,'decode_fps':sum(x['decoded_fps'] for x in ss),
                'infer_fps':sum(x['completed_fps'] for x in ss),'zero_streams':sum(x['completed_measured']==0 for x in ss),
                'skipped_before_vpp_measured':sum(x['skipped_before_vpp_measured'] for x in ss),
                'maximum_no_result_interval_s':max(x['analysis_completion_continuity']['max_gap_including_edges_s'] for x in ss),
                'errors':[x['error'] for x in ss if x['error']]}
            logs=(a.output/name/'stderr.log').read_text()
            if logs.count('output_format requested=101 effective=0') != 32 or logs.count('extra_frame_buffer_num requested=2 effective=8') != 32: raise RuntimeError('Decoder overrides not verified for 32 streams')
            row.update(summarize(a.output/name,'wall_preview',32))
            row['preview_fps_per_stream']=sum(x['previews_submitted_measured'] for x in ss)/s['measured_seconds']/32
            rows.append(row);save(a.output/'comparison.json',rows);print(json.dumps(row),flush=True)
        save(a.output/'state.json',{'status':'completed'})
    except BaseException as e:
        save(a.output/'state.json',{'status':'failed','error':repr(e)});raise
if __name__=='__main__':main()