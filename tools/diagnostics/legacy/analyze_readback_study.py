#!/usr/bin/env python3
"""Summarize only completed measurements, filtering TPU samples to formal windows."""
import argparse
import json
from pathlib import Path
import re
import statistics
import itertools


def summarize(root):
    rows=[]
    for marker in sorted(root.rglob('state.json')):
        folder=marker.parent
        if not (folder/'command.json').exists(): continue
        if json.loads(marker.read_text()).get('status')!='completed': continue
        file=folder/'result.json'
        wall=not file.exists()
        if wall: file=folder/'worker/summary.json'
        result=json.loads(file.read_text())
        if wall and 'measurement_start_monotonic_s' not in result:
            # Legacy YOLO26 records retain exact paced-file timing, but no summary clock.
            config=json.loads((folder/'worker/config.json').read_text())
            anchors=[]
            for stream in result['streams']:
                fps=stream['pacing_fps']
                if fps<=0: raise ValueError('Cannot reconstruct unpaced/RTSP formal window')
                with (folder/'worker'/f"stream_{stream['stream_id']:02d}"/'detections.jsonl').open() as f:
                    for line in itertools.islice(f,20):
                        r=json.loads(line)
                        anchors.append(r['received_monotonic_s']-(r['decode_ms']+r['schedule_lateness_ms'])/1000-r['frame']/fps)
            if not anchors or max(anchors)-min(anchors)>0.0001: raise ValueError('Inconsistent source clock')
            start=statistics.median(anchors)+config['warmup_s']; end=start+result['measured_seconds']
        else:
            start=result['measurement_start_monotonic_s' if wall else 'measurement_start_monotonic']
            end=result['observed_measurement_end_monotonic_s' if wall else 'measurement_end_monotonic']
        samples=[]
        for line in (folder/'telemetry.jsonl').read_text().splitlines():
            sample=json.loads(line)
            values=re.findall(r'(\d+)%',sample['smi'])
            if start<=sample['monotonic']<end and sample['returncode']==0 and len(values)==1:
                samples.append(int(values[0]))
        row={'stage':str(folder.relative_to(root)), 'tpu_mean_percent':statistics.mean(samples) if samples else None,
             'tpu_samples':len(samples)}
        if wall:
            if result['status']!='measured' or not result.get('accounting_complete',result.get('records_complete')):
                raise ValueError('Incomplete wall accounting: '+str(folder))
            decoded=result['total_decoded_fps'] if 'total_decoded_fps' in result else sum(s['decoded_fps'] for s in result['streams'])
            row.update(fps=result['total_completed_fps'],decode_fps=decoded,minimum_stream_fps=result['minimum_stream_fps'])
            for key in ['preprocess_ms','inference_ms','output_copy_ms','cpu_postprocess_ms','preview_vpp_ms','preview_readback_ms']:
                stats=[]
                for s in result['streams']:
                    stat=dict(s.get(key,{}))
                    if 'count' not in stat and stat.get('mean') is not None:
                        stat.update(count=s['completed_measured'],sum=stat['mean']*s['completed_measured'])
                    stats.append(stat)
                count=sum(s.get('count',0) for s in stats)
                row[key]=sum(s.get('sum',0) for s in stats)/count if count else None
            row['accounting_complete']=True
            gates=[s['score_gate_measured'] for s in result['streams'] if 'score_gate_measured' in s]
            frames=sum(g['frames'] for g in gates)
            row['output_MB_s']=sum(g['total_d2h_bytes'] for g in gates)/result['measured_seconds']/1e6
            row['preview_MB_s']=sum(s.get('preview_readback_bytes_measured',0) for s in result['streams'])/result['measured_seconds']/1e6
            row['row_calls_per_frame']=sum(g['row_read_calls'] for g in gates)/frames if frames else 0
            for key in ['execute_ms','score_read_ms','row_read_ms']:
                count=sum(g[key]['count'] for g in gates)
                row['gate_'+key]=sum(g[key]['sum'] for g in gates)/count if count else None
            if not gates and result['slots']:
                # Compact fixed output is read once per completed frame, including warmup.
                bytes_per_frame=sum(s['output_copy_bytes'] for s in result['slots'])/sum(s['frames'] for s in result['slots'])
                row['output_MB_s']=bytes_per_frame*row['fps']/1e6
        else:
            for key in ['mode','copy_chunk_bytes','iterations_per_second','copy_mean_ms','sync_mean_ms','submit_mean_ms','upload_MB_s','download_MB_s','upload_mean_ms','download_mean_ms','output_matches_reference','data_verified']:
                if key in result: row[key]=result[key]
            if result.get('submit_mean_ms') is not None and result.get('sync_mean_ms') is not None:
                row['submit_sync_ms']=result['submit_mean_ms']+result['sync_mean_ms']
        rows.append(row)
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('root',type=Path); a=p.parse_args()
    rows=summarize(a.root)
    (a.root/'analysis.json').write_text(json.dumps(rows,indent=2)+'\n')
    for row in rows: print(json.dumps(row))
