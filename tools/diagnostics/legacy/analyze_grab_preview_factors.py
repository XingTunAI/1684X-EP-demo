#!/usr/bin/env python3
"""Summarize the two-factor preview experiment; never label gate execution as DMA."""
import argparse,json
from pathlib import Path
from statistics import mean

def analyze(root):
    rows=json.loads((root/'comparison.json').read_text())
    for row in rows:
        s=json.loads((root/row['stage']/'worker/summary.json').read_text());ss=s['streams']
        gates=[x['score_gate_measured'] for x in ss]
        frames=sum(x['frames'] for x in gates)
        def metric(key):return sum(x[key]['sum'] for x in gates)/frames if frames else None
        row['readback_breakdown']={key:metric(key) for key in ['execute_ms','submit_ms','sync_ms','score_read_ms','row_read_ms','full_read_ms','host_zero_ms']}
        row['readback_breakdown']['dma_ms_per_frame']=sum(row['readback_breakdown'][k] for k in ['score_read_ms','row_read_ms','full_read_ms'])
        row['readback_breakdown']['bytes_per_frame']=sum(x['total_d2h_bytes'] for x in gates)/frames
        row['readback_breakdown']['row_calls_per_frame']=sum(x['row_read_calls'] for x in gates)/frames
        path=root/(row['stage']+'-wall-samples.jsonl')
        samples=[]
        for line in path.read_text().splitlines():
            x=json.loads(line);t=x.get('snapshot_monotonic_s')
            if t is not None and s['measurement_start_monotonic_s']<=t<s['observed_measurement_end_monotonic_s']:samples.append(x)
        unique={x['snapshot_monotonic_s']:x for x in samples};samples=sorted(unique.values(),key=lambda x:x['snapshot_monotonic_s'])
        if len(samples)>1:
            first,last=samples[0],samples[-1];dt=last['snapshot_monotonic_s']-first['snapshot_monotonic_s']
            row['wall_sampling']={'seconds':dt,'publish_fps':(last['published_frames']-first['published_frames'])/dt,
                'max_stale_channels':max(sum(bool(y['stale']) for y in x['streams']) for x in samples),
                'max_source_age_ms':max(y.get('source_age_ms') or 0 for x in samples for y in x['streams'])}
        row['accounting_complete']=s['accounting_complete']
    groups={}
    for mode in dict.fromkeys(x['mode'] for x in rows):
        rs=[x for x in rows if x['mode']==mode]
        groups[mode]={'runs':len(rs)}
        for key in ['infer_fps','preview_fps_per_stream','tpu_percent','preview_MB_s','result_MB_s']:
            groups[mode][key]=mean(x[key] for x in rs)
        for key in ['output_copy_ms','preview_readback_ms','preview_vpp_ms']:
            groups[mode][key]=mean(x['stage_mean_ms'][key] for x in rs)
        for key in ['dma_ms_per_frame','execute_ms','score_read_ms','row_read_ms','bytes_per_frame','row_calls_per_frame']:
            groups[mode][key]=mean(x['readback_breakdown'][key] for x in rs)
    contrasts=[]
    for rep in sorted(set(x['repeat'] for x in rows)):
        by_mode={x['mode']:x for x in rows if x['repeat']==rep}
        for label,before,after in [('preview_at_wall10','3','preview-only'),('preview_at_wall0','wall-only','unlimited'),('wall_at_preview3','3','wall-only'),('wall_at_unlimited_preview','preview-only','unlimited'),('both_vs_baseline','3','unlimited')]:
            if before not in by_mode or after not in by_mode:continue
            a,b=by_mode[before],by_mode[after]
            item={'repeat':rep,'contrast':label,'before':before,'after':after}
            pairs={'infer_fps':(a['infer_fps'],b['infer_fps']),
                   'preview_readback_ms':(a['stage_mean_ms']['preview_readback_ms'],b['stage_mean_ms']['preview_readback_ms']),
                   'dma_ms_per_frame':(a['readback_breakdown']['dma_ms_per_frame'],b['readback_breakdown']['dma_ms_per_frame'])}
            item['changes']={k:{'delta':y-x,'percent':(y/x-1)*100 if x else None} for k,(x,y) in pairs.items()}
            contrasts.append(item)
    result={'rows':rows,'means':groups,'within_repeat_contrasts':contrasts,'note':'Arithmetic means across equal-duration runs. Short sequential repeats do not establish statistical significance or physical PCIe transfer latency.'}
    (root/'factor-analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args()
    print(json.dumps(analyze(a.root)['means'],indent=2))
