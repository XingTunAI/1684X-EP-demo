#!/usr/bin/env python3
"""Bounded, single-card readback study; preserve per-stage commands and telemetry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2) + '\n')


def run_stage(root, name, command, device, timeout=180, barrier=False, readback=None):
    folder = root / name
    folder.mkdir()
    command = [str(x).replace('{stage}', str(folder)) for x in command]
    if readback is not None:
        save(folder / 'readback.json', {'readback_enabled': readback})
    save(folder / 'command.json', command)
    print(name, flush=True)
    with (folder / 'stdout.log').open('w') as out, (folder / 'stderr.log').open('w') as err, (folder / 'telemetry.jsonl').open('w') as telemetry:
        p = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
        player = None
        begin = time.monotonic()
        released = False
        try:
            while p.poll() is None:
                if time.monotonic() - begin > timeout:
                    raise TimeoutError(name)
                if barrier and not released and (folder / 'result.json.ready').exists():
                    (folder / 'gate.txt').write_text(str(time.monotonic()+0.5))
                    released = True
                fifo = folder / 'worker/preview.bgr'
                if fifo.exists() and player is None:
                    env = dict(os.environ, DISPLAY=':0', XAUTHORITY='/var/run/lightdm/root/:0', LD_LIBRARY_PATH='/usr/lib/aarch64-linux-gnu', SDL_RENDER_DRIVER='software')
                    player = subprocess.Popen(['/usr/bin/ffplay', '-loglevel', 'error', '-autoexit', '-fs', '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', '1920x1080', '-framerate', '10', '-i', str(fifo)], env=env, stdout=out, stderr=err, start_new_session=True)
                tick = time.monotonic()
                sample = subprocess.run(['bm-smi','--noloop','--text_format',f'--start_dev={device}',f'--last_dev={device}'], capture_output=True, text=True, timeout=5)
                telemetry.write(json.dumps({'monotonic':tick, 'returncode':sample.returncode,'smi':sample.stdout,'stderr':sample.stderr})+'\n')
                telemetry.flush()
                time.sleep(max(0, 1-(time.monotonic()-tick)))
            if p.returncode:
                raise RuntimeError(f'{name}: exit {p.returncode}; see {folder}')
            if player is not None and player.poll() not in (None, 0):
                raise RuntimeError(f'{name}: HDMI player failed with {player.returncode}')
            save(folder/'state.json', {'status':'completed','exit_code':p.returncode})
        finally:
            for child in (p, player):
                if child is not None and child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try: child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL); child.wait()


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('--device', type=int, required=True)
    a.add_argument('--bdf', required=True)
    a.add_argument('--output', type=Path, required=True)
    a.add_argument('--suite', choices=['transfer','inference','wall'], required=True)
    a.add_argument('--duration', type=int, default=15)
    a.add_argument('--repeats', type=int, default=3)
    a.add_argument('--streams', type=int, default=16)
    a.add_argument('--bmodel', type=Path)
    args = a.parse_args()
    if min(args.duration,args.repeats,args.streams)<1 or args.streams>32 or args.device<0:
        a.error('Invalid duration/repeats/streams/device')
    link = Path('/sys/bus/pci/devices') / args.bdf
    speed=(link/'current_link_speed').read_text().strip(); width=(link/'current_link_width').read_text().strip()
    if not speed.startswith('5.0') or width != '1':
        raise RuntimeError('Target must currently negotiate PCIe 2.0 x1')
    smi=subprocess.run(['bm-smi','--noloop','--text_format',f'--start_dev={args.device}',f'--last_dev={args.device}'],capture_output=True,text=True,check=True).stdout
    if args.bdf.lstrip('0') not in smi:
        raise RuntimeError('Device/BDF mismatch: '+smi)
    conflicts=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try:
            exe=(path/'exe').resolve().name
            if exe.endswith('.pcie') or exe in ('ffplay','ffmpeg','test_cdma_perf'):
                conflicts.append([path.name,exe])
        except OSError: pass
    if conflicts: raise RuntimeError('Competing workloads: '+str(conflicts))
    args.output.mkdir(parents=True,exist_ok=False)
    model=args.bmodel or ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel'
    save(args.output/'config.json',dict(vars(args),output=str(args.output),bmodel=str(model),speed=speed,width=width,smi=smi,
         model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),scope='fixed tensor diagnostics or local video; SDK wall-clock timing'))
    artifacts=[Path(__file__), ROOT/'tools/diagnostics/build/inference_probe.pcie', ROOT/'tools/diagnostics/build/duplex_probe.pcie']
    if args.suite=='wall':
        artifacts += [ROOT/'demos/hdmi_wall/build/hdmi_wall.pcie',ROOT/'data/inputs/highway_1080p25.mp4',ROOT/'data/models/score_gate_reducemax_f32.bmodel']
    save(args.output/'artifacts.json',[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in artifacts])
    try:
        for rep in range(args.repeats):
            if args.suite=='transfer':
                for size in [33600,2822400]:
                    modes=['upload','download','serial','duplex','paired']
                    if rep%2: modes.reverse()
                    for mode in modes:
                        run_stage(args.output,f'r{rep}_{size}_{mode}',[ROOT/'tools/diagnostics/build/duplex_probe.pcie',args.device,size,args.duration,3,mode,'{stage}/result.json'],args.device)
            elif args.suite=='inference':
                cases=[('compute',0),('copy',0),('compute-copy',0),('overlap',0),('compute-copy',262144),('overlap',262144)]
                if rep%2: cases.reverse()
                for mode,chunk in cases:
                    run_stage(args.output,f'r{rep}_{mode}_{chunk}',[ROOT/'tools/diagnostics/build/inference_probe.pcie',args.device,model,mode,3,args.duration,'{stage}/gate.txt','{stage}/result.json',chunk],args.device,barrier=True)
            else:
                cases=[('model_only','on',64,0,False,'reuse'),('full','off',0,0,True,'reuse'),
                       ('gate0','on',0,0,True,'reuse'),('gate64','on',64,0,True,'reuse'),
                       ('gate256','on',256,0,True,'reuse'),('preview10','on',64,10,True,'reuse'),
                       ('preview3','on',64,3,True,'reuse'),('baseline','on',64,10,True,'baseline')]
                if rep%2: cases.reverse()
                for name,gate,budget,preview,readback,buffer in cases:
                    command=[ROOT/'demos/hdmi_wall/build/hdmi_wall.pcie','--device',args.device,'--streams',args.streams,
                        '--input',ROOT/'data/inputs/highway_1080p25.mp4','--bmodel',model,'--classnames',ROOT/'data/models/coco.names',
                        '--output','{stage}/worker','--warmup',5,'--duration',args.duration,'--window',args.duration,
                        '--local-eof','loop','--policy','latest','--infer-fps',0,'--max-frame-age-ms',250,
                        '--image-path','auto','--record-mode','summary','--prime-local-decoders','on',
                        '--output-buffer',buffer,'--score-gate',gate,'--cpu-post','selected' if gate=='on' else 'dense',
                        '--gate-merge-budget-kib',budget,'--preview-fps',preview,'--readback-control','{stage}/readback.json']
                    if gate=='on': command+=['--score-gate-model',ROOT/'data/models/score_gate_reducemax_f32.bmodel']
                    run_stage(args.output,f'r{rep}_{name}',command,args.device,timeout=args.duration+120,readback=readback)
        save(args.output/'state.json',{'status':'completed'})
    except BaseException as exc:
        save(args.output/'state.json',{'status':'failed','error':str(exc)})
        raise


if __name__=='__main__': main()
