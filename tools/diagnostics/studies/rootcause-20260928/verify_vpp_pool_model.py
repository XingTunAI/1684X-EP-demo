"""Conditional two-slot resource-demand bound; input is measured driver time."""
from pathlib import Path
import json

root=Path(__file__).resolve().parent
j=json.loads((root/'vpp-attribution-v2/analysis-v2/summary.json').read_text())
rows=[]
for capture in j['captures']:
    for device in ('0','2'):
        d=capture['devices'][device];roles=d['roles']
        fps=d['windows']['capture']['throughput']['fps']
        service={r:sum(v for k,v in record['partition_mean_us'].items() if k!='admission_us')/1000
                 for r,record in roles.items()}
        seconds=capture['capture']['before_disable_monotonic']-capture['capture']['after_enable_monotonic']
        preview_ratio=roles['thumbnail']['paired_roots']/seconds/fps
        decoder_rate=32*24
        remaining_slot_ms=2000-decoder_rate*service['unclassified']
        demand_ms=service['pre_vpp']+service['pre_convert']+preview_ratio*service['thumbnail']
        assert remaining_slot_ms>0 and demand_ms>0
        bound=remaining_slot_ms/demand_ms
        rows.append({'capture':capture['name'],'device':int(device),
            'service_ms_after_admission':service,'fixed_decode_requests_per_s':decoder_rate,
            'preview_calls_per_detection':preview_ratio,'slot_budget_ms_per_s':2000,
            'remaining_slot_ms_per_s_after_decode':remaining_slot_ms,
            'slot_demand_ms_per_detection_including_preview':demand_ms,
            'approx_vpp_limited_fps':bound,'observed_fps':fps,'difference_percent':(bound/fps-1)*100})
report={'rows':rows,'limits':[
    'This is a resource-budget consistency check conditional on measured service times and preview ratio, not an independently predicted PCIe bandwidth limit.',
    'After-admission driver wall time approximates slot occupancy and includes small work outside the critical section; it is not VPP hardware compute time.',
    'CDMA contention in measured service time depends on this workload; do not extrapolate unchanged to other concurrency levels.',
    'The decoder rate is the independently known 32 streams times24fps, checked against observed calls and decoded-frame counters.',
    'TPU capacity and other work may impose a lower limit; intrusive trace changes throughput.',
]}
(root/'vpp-pool-model.json').write_text(json.dumps(report,indent=2)+'\n')
for r in rows:print(r['capture'],r['device'],round(r['approx_vpp_limited_fps'],3),round(r['observed_fps'],3))
