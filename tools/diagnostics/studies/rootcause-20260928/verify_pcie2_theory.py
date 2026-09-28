"""Check observed stream costs and optimistic PCIe serialization bounds."""
import hashlib
import json
import math
from pathlib import Path
import statistics

root=Path(__file__).resolve().parent
data=json.loads((root/'evidence/dual-wall/sync10_device0/worker/summary.json').read_text())
cfg=json.loads((root/'evidence/dual-wall/sync10_device0/worker/config.json').read_text())
assert cfg['streams']==32 and cfg['business_threads']==64 and cfg['preview_mode']=='sync'
streams=data['streams']; frames=sum(s['completed_measured'] for s in streams)
previews=sum(s['previews_submitted_measured'] for s in streams)
results=sum(s['score_gate_measured']['total_d2h_bytes'] for s in streams)
seconds=data['measured_seconds']
cycles=[{'stream':s['stream_id']+1,'frames':s['completed_measured'],
         'fps':s['completed_measured']/seconds,
         'cycle_mean_ms':(s['service_ms']['sum']+s['preview_ms']['sum'])/s['completed_measured']}
        for s in streams]
rate=5e9*(8/10)/8
assert rate==500_000_000
rows=[]
for size, measured in [(33600,sum(s['score_gate_measured']['score_read_ms']['sum'] for s in streams)/frames),
                       (110592,sum(s['preview_readback_ms']['sum'] for s in streams)/previews)]:
    probes=[json.loads((root/f'pcie2-small-copy-20260928/r{i}_{size}.json').read_text()) for i in [0,1]]
    assert all(p['data_verified'] and p['device']==0 and p['bytes_per_call']==size and p['mode']=='download' for p in probes)
    idle=statistics.mean(p['download_mean_ms'] for p in probes)
    rows.append({'payload_bytes':size,'payload_only_ms':1000*size/rate,
        'optimistic_tlp20_ms':1000*(size+20*math.ceil(size/128))/rate,
        'optimistic_tlp24_ms':1000*(size+24*math.ceil(size/128))/rate,
        'isolated_copy_ms':idle,'video_wall_copy_ms':measured,'wall_over_isolated_ratio':measured/idle})
assert all(r['video_wall_copy_ms']>r['isolated_copy_ms']>r['optimistic_tlp24_ms'] for r in rows)
report={'scope':'Payload-only and ideal full-128B TLP serialization bounds; not total SDK latency or hardware bus utilization.',
    'link_MB_s_after_encoding':rate/1e6,'optimistic_payload_MB_s_with_20B_overhead':rate/1e6*128/148,
    'optimistic_payload_MB_s_with_24B_overhead':rate/1e6*128/152,'per_stream':cycles,
    'weighted_cycle_ms':sum(s['service_ms']['sum']+s['preview_ms']['sum'] for s in streams)/frames,
    'copy_comparisons':rows,'results_bytes_per_detection':results/frames,
    'result_read_ms_per_detection':sum(s['score_gate_measured'][k]['sum'] for s in streams for k in ['score_read_ms','row_read_ms'])/frames,
    'result_payload_only_ms_per_detection':1000*results/frames/rate,
    'result_tlp20_bulk_approx_ms_per_detection':1000*results/frames/rate*148/128,
    'result_tlp24_bulk_approx_ms_per_detection':1000*results/frames/rate*152/128,
    'counted_downlink_MB_s':(results+previews*110592)/seconds/1e6,
    'average_threads_in_stages':{k:sum(s[k]['sum'] for s in streams)/(seconds*1000) for k in ['preprocess_ms','inference_ms','postprocess_ms','preview_ms']}}
(root/'pcie2-theory-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='per_stream'},indent=2))
