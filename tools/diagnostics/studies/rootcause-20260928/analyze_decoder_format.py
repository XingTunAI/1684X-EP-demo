"""Audit format ablations, including regressions and API request counts."""
from pathlib import Path
from collections import Counter
import json
import re
import sys
from analyze_vpp_attribution import load_events,api_summary

def analyze(root):
    assert json.loads((root/'state.json').read_text())['status']=='completed'
    rows=[]
    samples=[json.loads(line) for line in (root/'host-and-status.jsonl').read_text().splitlines()]
    for row in json.loads((root/'comparison.json').read_text()):
        stage=row['stage'];summary=json.loads((root/stage/'worker/summary.json').read_text())
        assert summary['accounting_complete'] and len(summary['streams'])==32
        assert row['zero_streams']==0
        b,e=row['measurement_start'],row['measurement_end']
        outer,by_tid=load_events(root/f'events_{stage}')
        driver_calls=Counter();descriptors=Counter()
        for es in by_tid.values():
            for x in es:
                if b<=x['begin'] and x['end']<=e:
                    driver_calls[x['role']]+=1;descriptors[x['role']]+=x['batch']
        linear=row['configuration'].startswith('linear');expected=0 if linear else 101
        logs=(root/stage/'stderr.log').read_text()
        overrides=re.findall(r'VPP_DIAG output_format requested=(\d+) effective=(\d+)',logs)
        assert len(overrides)==32 and all((a,b)==('101',str(expected)) for a,b in overrides),overrides
        buffers=re.findall(r'VPP_DIAG extra_frame_buffer_num requested=(\d+) effective=(\d+)',logs)
        if buffers:assert len(buffers)==32 and all(b=='8' for a,b in buffers)
        if linear:assert driver_calls['unclassified']==0
        lags=[];ages=[]
        for s in samples:
            if b<=s['monotonic']<e:
                for stream in s['status'].get(stage,{}).get('streams',[]):
                    if stream.get('decode_source_lag_ms') is not None:lags.append(stream['decode_source_lag_ms'])
                    if stream.get('age_seconds') is not None and stream.get('has_image'):ages.append(stream['age_seconds'])
        result=dict(row,actual_format=expected,extra_buffers=8 if buffers else 2,
            actual_preview_fps_per_stream=row['preview_MB_s']*1e6/110592/32,
            driver_calls_in_formal_window=dict(driver_calls),descriptors_in_formal_window=dict(descriptors),
            driver_calls_per_s={k:n/(e-b) for k,n in driver_calls.items()},
            api=api_summary(outer,b,e),max_sampled_decode_lag_ms=max(lags,default=None),
            max_sampled_preview_age_s=max(ages,default=None),override_count=len(overrides))
        rows.append(result)
        print(stage,round(row['fps'],3),round(row['tpu_percent'],3),
            'pre',round(row['stage_mean_ms']['preprocess_ms'],3),'stale',row['dropped_stale_lifetime'],
            'gap',round(row['maximum_no_result_interval_s'],3),'decode lag',max(lags,default=None),
            'preview',round(result['actual_preview_fps_per_stream'],3),
            'other vpp',driver_calls['unclassified'])
    output=root/'format-analysis.json'
    with output.open('x') as f:json.dump({'rows':rows,'notes':[
        'Events and app completed frames have different formal-window edge populations.',
        'Stale/overwrite counts cover whole run; FPS and API means cover formal window.',
        'Source lag and preview age are sampled maxima, not all-frame latency percentiles.',
        'Removal of unclassified VPP calls is checked explicitly; output correctness uses separate matched-source-frame records.',
    ]},f,indent=2);f.write('\n')

if __name__=='__main__':analyze(Path(sys.argv[1]))
