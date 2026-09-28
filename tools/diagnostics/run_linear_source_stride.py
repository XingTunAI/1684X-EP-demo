#!/usr/bin/env python3
"""Screen throughput settings with linear decoder output and eight extra buffers."""
import argparse,json,os,hashlib,threading,signal
from pathlib import Path
from run_readback_study import ROOT,run_stage,save
from run_pipeline32_comparison import summarize

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--duration',type=int,default=60);ap.add_argument('--streams',type=int,default=32)
    ap.add_argument('--device',type=int,default=0);ap.add_argument('--modes',default='10,wall-only')
    ap.add_argument('--player-fps',type=int,default=60)
    ap.add_argument('--repeats',type=int,default=1)
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--retrieve-every',type=int,choices=(1,2),default=1)
    a=ap.parse_args();modes=a.modes.split(',')
    if not 1<=a.streams<=32 or a.duration<1 or any(x not in ('3','10','wall10','preview-only','wall-only','unlimited','none') for x in modes) or not 1<=a.player_fps<=120 or not 1<=a.repeats<=10:ap.error('invalid test parameters')
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                if (p/'exe').resolve().name in ('hdmi_wall.pcie','pipeline_worker.pcie'):raise RuntimeError('Existing inference process: '+p.name)
            except FileNotFoundError:pass
    os.environ['PATH']='/opt/sophon/libsophon-current/bin:'+os.environ['PATH']
    a.output.mkdir(parents=True,exist_ok=False);save(a.output/'state.json',{'status':'running'})
    save(a.output/'metadata.json',{'device':a.device,'streams':a.streams,'duration':a.duration,'retrieve_every':a.retrieve_every,'modes':modes,'warmup':30,'player_fps':a.player_fps,'repeats':a.repeats,
        'binary_sha256':hashlib.sha256((ROOT/'demos/hdmi_wall/build-grab-select/hdmi_wall.pcie').read_bytes()).hexdigest(),
        'input':str(a.input),'input_sha256':hashlib.sha256(a.input.read_bytes()).hexdigest(),
        'note':'Linear output 0 and extra decoder buffers 8; controlled retrieval stride.'})
    rows=[]
    try:
        schedule=[(rep,mode) for rep in range(a.repeats) for mode in (modes if rep%2==0 else list(reversed(modes)))]
        for i,(rep,mode) in enumerate(schedule):
            preview,wall={'3':(3,10),'10':(10,10),'wall10':(10,0),'none':(0,0),'preview-only':(-1,10),'wall-only':(3,0),'unlimited':(-1,0)}[mode]
            step=a.retrieve_every
            name=f'r{i}_preview{preview}_wall{wall}'
            command=[ROOT/'demos/hdmi_wall/build-grab-select/hdmi_wall.pcie',
                '--device',a.device,'--streams',a.streams,'--input',a.input,
                '--bmodel',ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel',
                '--classnames',ROOT/'data/models/coco.names','--score-gate-model',ROOT/'data/models/score_gate_reducemax_f32.bmodel',
                '--output','{stage}/worker','--warmup',30,'--duration',a.duration,'--window',10,
                '--local-eof','fail','--policy','latest','--infer-fps',0,'--max-frame-age-ms',250,
                '--image-path','auto','--record-mode','summary','--prime-local-decoders','on',
                '--output-buffer','reuse','--score-gate','on','--cpu-post','selected',
                '--gate-merge-budget-kib',64,'--preview-fps',preview,'--wall-fps',wall,'--retrieve-every',step]
            command=['env','LD_PRELOAD='+str(ROOT/'data/results/rootcause-20260928/decoder_format_override_v2.so'),'VPP_DIAG_OUTPUT_FORMAT=0','VPP_DIAG_EXTRA_FRAMES=8']+command
            stop=threading.Event()
            def sample_wall():
                consecutive_bad = 0
                with (a.output/(name+'-wall-samples.jsonl')).open('w') as log:
                    while not stop.is_set():
                        try:
                            status=json.loads((a.output/name/'worker/status.json').read_text())
                            log.write(json.dumps(status)+'\n');log.flush()
                            stale=sum(x.get('stale',False) for x in status.get('streams',[]))
                            consecutive_bad=consecutive_bad+1 if stale >= max(1,a.streams//2) else 0
                            if consecutive_bad >= 5:
                                save(a.output/'quality-stop.json',{'reason':'At least half of channels STALE for five samples','stage':name,'stale_channels':stale,'status':status})
                                os.kill(os.getpid(),signal.SIGINT)
                                return
                        except (OSError,ValueError):pass
                        stop.wait(1)
            watcher=threading.Thread(target=sample_wall);watcher.start()
            try:
                run_stage(a.output,name,command,a.device,timeout=a.duration+120,player_fps=a.player_fps)
            finally:
                stop.set();watcher.join()
            s=json.loads((a.output/name/'worker/summary.json').read_text());ss=s['streams']
            if s['status'] != 'measured' or not s['accounting_complete']:raise RuntimeError('Incomplete measurement/accounting')
            row={'stage':name,'repeat':rep,'mode':mode,'retrieve_every':step,'decode_fps':sum(x['decoded_fps'] for x in ss),
                'infer_fps':sum(x['completed_fps'] for x in ss),'zero_streams':sum(x['completed_measured']==0 for x in ss),
                'skipped_before_vpp_measured':sum(x['skipped_before_vpp_measured'] for x in ss),
                'maximum_no_result_interval_s':max(x['analysis_completion_continuity']['max_gap_including_edges_s'] for x in ss),
                'errors':[x['error'] for x in ss if x['error']]}
            logs=(a.output/name/'stderr.log').read_text()
            if logs.count('output_format requested=101 effective=0') != a.streams or logs.count('extra_frame_buffer_num requested=2 effective=8') != a.streams: raise RuntimeError('Decoder overrides not verified')
            detail=summarize(a.output/name,'wall_preview',a.streams)
            row.update(preview_cap=preview,wall_cap=wall,player_fps=a.player_fps,tpu_percent=detail['tpu_percent'],minimum_stream_fps=detail['minimum_stream_fps'],
                preview_fps_per_stream=sum(x['previews_submitted_measured'] for x in ss)/s['measured_seconds']/len(ss),
                min_preview_fps=min(x['previews_submitted_measured'] for x in ss)/s['measured_seconds'],
                preview_MB_s=detail['preview_MB_s'],result_MB_s=detail['result_MB_s'],
                stale_drops_lifetime=detail['dropped_stale_lifetime'],stage_mean_ms=detail['stage_mean_ms'])
            rows.append(row);save(a.output/'comparison.json',rows);print(json.dumps(row),flush=True)
        save(a.output/'state.json',{'status':'completed'})
    except BaseException as e:
        save(a.output/'state.json',{'status':'failed','error':repr(e)});raise
if __name__=='__main__':main()