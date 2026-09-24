#!/usr/bin/env python3
"""Same-card link-speed DMA ceiling and VPP/DMA coexistence; restores link."""
import hashlib,json,os,signal,subprocess,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/results/dma-vpp-link-20260924'
PORT='0003:30:00.0';EP=Path('/sys/bus/pci/devices/0003:31:00.0')
BIN=ROOT/'tools/diagnostics/build-dma-vpp/dma_vpp_probe.pcie'
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def run(*a):return subprocess.check_output(a,text=True).strip()
def interrupted(*_):raise KeyboardInterrupt('requested stop')
def trace_idle():return Path('/sys/kernel/debug/tracing/current_tracer').read_text().strip()=='nop'
def set_speed(target):
 run('setpci','-s',PORT,'CAP_EXP+30.w='+target+':000f');run('setpci','-s',PORT,'CAP_EXP+10.w=0020:0020');time.sleep(2)
def collect(path,stop):
 counters=Path('/proc/bmsophon/card2/bmsophon2')
 with path.open('w') as f:
  while not stop.is_set():
   row={'monotonic':time.monotonic()}
   for key in ['cdma_in_counter','cdma_out_counter','cdma_in_time','cdma_out_time']:
    try:row[key]=(counters/key).read_text().strip()
    except OSError as e:row[key]=str(e)
   f.write(json.dumps(row)+'\n');f.flush();stop.wait(.25)
def stage(name,case):
 folder=OUT/name;folder.mkdir();mode,size,workers,vpp,rate=case
 cmd=[str(BIN),'2',str(size),str(workers),mode,str(vpp),str(rate),'8',str(folder/'result.json'),'2']
 save(folder/'command.json',cmd);save(OUT/'progress.json',{'current_stage':name,'case':case})
 stop=threading.Event();watch=threading.Thread(target=collect,args=(folder/'driver-counters.jsonl',stop));watch.start();p=None
 try:
  with (folder/'stdout.log').open('w') as out,(folder/'stderr.log').open('w') as err:
   p=subprocess.Popen(cmd,stdout=out,stderr=err,start_new_session=True);code=p.wait(timeout=100)
   if code:raise RuntimeError(f'{name} exited {code}')
  d=json.loads((folder/'result.json').read_text());assert d['data_verified'] and d['seconds']==8
  assert len(d['threads'])==workers*(2 if mode=='duplex' else 0 if mode=='none' else 1)+vpp
  assert all(t['count']>0 for t in d['threads'])
 finally:
  if p and p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
  stop.set();watch.join()
def main():
 signal.signal(signal.SIGTERM,interrupted)
 assert trace_idle(),'Tracing enabled'
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:name=(p/'exe').resolve().name
  except OSError:continue
  if name.endswith('.pcie') or name in ('ffplay','ffmpeg','test_cdma_perf'):raise RuntimeError('Competing worker '+str(p))
 assert '003:31:00.0' in run('bm-smi','--noloop','--text_format','--start_dev=2','--last_dev=2')
 assert (EP/'current_link_speed').read_text().startswith('8.0') and (EP/'current_link_width').read_text().strip()=='1'
 original=run('setpci','-s',PORT,'CAP_EXP+30.w');OUT.mkdir(exist_ok=False)
 state={'status':'running','original_target':original,'binary_sha256':hashlib.sha256(BIN.read_bytes()).hexdigest()};save(OUT/'state.json',state)
 cases=[]
 for size in [32768,1048576,16777216]:
  for mode in ['upload','download','duplex']:cases.append((f'ceiling_{size}_{mode}_w1',(mode,size,1,0,0)))
 for mode in ['upload','download','duplex']:cases.append((f'ceiling_16777216_{mode}_w4',(mode,16777216,4,0,0)))
 for mode in ['upload','download']:cases.append((f'ceiling_67108864_{mode}_w1',(mode,67108864,1,0,0)))
 for n in [2,32]:
  cases.append((f'vpp{n}_alone',('none',110592,0,n,0)))
  for size in [32768,110592,1048576]:cases.append((f'vpp{n}_down40_{size}',('download',size,1,n,40)))
 cases += [('vpp2_up40_110592',('upload',110592,1,2,40))]
 for mode in ['upload','download','duplex']:cases.append((f'vpp2_max_{mode}',(mode,16777216,1,2,0)))
 save(OUT/'plan.json',cases)
 try:
  for b,target in enumerate(['0003','0002','0002','0003']):
   set_speed(target);speed=(EP/'current_link_speed').read_text().strip();width=(EP/'current_link_width').read_text().strip();assert speed.startswith('8.0' if target=='0003' else '5.0') and width=='1'
   save(OUT/f'b{b}-link.json',{'speed':speed,'width':width,'device':2,'bdf':'0003:31:00.0'})
   for name,case in (cases if b<2 else list(reversed(cases))):stage(f'b{b}_{name}',case)
  assert hashlib.sha256(BIN.read_bytes()).hexdigest()==state['binary_sha256'];state['status']='completed'
 except BaseException as e:state.update(status='failed',error=repr(e));raise
 finally:
  try:
   set_speed(original);state['restored_speed']=(EP/'current_link_speed').read_text().strip();assert state['restored_speed'].startswith('8.0')
  except BaseException as e:state.update(status='failed',restore_error=repr(e))
  save(OUT/'state.json',state)
if __name__=='__main__':main()
