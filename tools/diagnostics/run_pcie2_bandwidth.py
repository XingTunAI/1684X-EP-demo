import json,os,hashlib,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
out=root/'bandwidth-results';out.mkdir(exist_ok=False)
ep=Path('/sys/bus/pci/devices/0004:41:00.0')
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert boot=='69109bf2-536f-4c7f-ab3e-9ad5e4f957bc'
def save(p,d):p.write_text(json.dumps(d,indent=2))
def identity():
 assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot
 assert (ep/'current_link_speed').read_text().startswith('5.0') and (ep/'current_link_width').read_text().strip()=='1'
 return {'boot_id':boot,'speed':(ep/'current_link_speed').read_text().strip(),'width':1,'bdf':ep.name}
cases=[(size,mode,1) for size in [32768,1048576,16777216] for mode in ['upload','download','duplex']]
cases += [(16777216,mode,4) for mode in ['upload','download','duplex']]
cases += [(67108864,mode,1) for mode in ['upload','download']]
env=dict(os.environ,LD_LIBRARY_PATH=str(root/'runtime/opt/sophon/libsophon-0.5.1/lib'))
state={'status':'running','identity':identity(),'probe_sha256':hashlib.sha256((root/'dma_vpp_probe.pcie').read_bytes()).hexdigest()}
save(out/'state.json',state)
try:
 for round in range(2):
  for size,mode,workers in (cases if round==0 else list(reversed(cases))):
   identity();name=f'r{round}_{size}_{mode}_w{workers}';folder=out/name;folder.mkdir()
   save(out/'progress.json',{'stage':name})
   cmd=[str(root/'dma_vpp_probe.pcie'),'0',str(size),str(workers),mode,'0','0','8',str(folder/'result.json'),'2']
   save(folder/'command.json',cmd)
   with (folder/'stdout.log').open('w') as a,(folder/'stderr.log').open('w') as b:
    subprocess.run(cmd,env=env,stdout=a,stderr=b,check=True,timeout=90)
   d=json.loads((folder/'result.json').read_text());assert d['data_verified'] and all(t['count']>0 for t in d['threads'])
 state.update(status='completed',final_identity=identity())
except BaseException as e:state.update(status='failed',error=repr(e));raise
finally:save(out/'state.json',state)
