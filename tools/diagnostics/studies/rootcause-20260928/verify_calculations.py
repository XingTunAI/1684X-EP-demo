"""Recompute the first synchronous run from raw per-stream counters/timings."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
folder=root/'evidence/dual-wall'
comparisons=json.loads((folder/'comparison.json').read_text())
rows=[]
for dev in [0,2]:
    stage=f'sync10_device{dev}'
    comparison=next(r for r in comparisons if r['stage']==stage)
    data=json.loads((folder/stage/'worker/summary.json').read_text())
    streams=data['streams']; seconds=data['measured_seconds']
    assert len(streams)==32 and data['accounting_complete'] and data['status']=='measured'
    assert all(not s['error'] and s['accounting_complete'] for s in streams)
    frames=sum(s['completed_measured'] for s in streams)
    previews=sum(s['previews_submitted_measured'] for s in streams)
    fps=frames/seconds
    assert abs(fps-comparison['fps'])<1e-9
    def avg(key): return sum(s[key]['sum'] for s in streams)/frames
    cost=avg('service_ms')+avg('preview_ms')
    estimated_fps=32000/cost
    error=100*(estimated_fps/fps-1)
    assert abs(error)<1, '32 continuously occupied cycles is not a close approximation'
    output_bytes=sum(s['score_gate_measured']['total_d2h_bytes'] for s in streams)
    assert abs(output_bytes/seconds/1e6-comparison['result_MB_s'])<1e-9
    assert abs(previews*110592/seconds/1e6-comparison['preview_MB_s'])<1e-9
    busy_fraction=comparison['tpu_percent']/100
    row=dict(device=dev,frames=frames,seconds=seconds,fps=fps,preview_frames=previews,
        preview_fps_per_stream=previews/seconds/32,
        preprocess_ms=avg('preprocess_ms'),inference_ms=avg('inference_ms'),
        postprocess_ms=avg('postprocess_ms'),service_ms=avg('service_ms'),
        preview_amortized_ms=avg('preview_ms'),cycle_ms=cost,
        predicted_fps=estimated_fps,prediction_error_percent=error,
        occupied_processing_threads=fps*cost/1000,
        tpu_equivalent_busy_ms_per_detection=1000*busy_fraction/fps,
        tpu_sampled_idle_seconds=seconds*(1-busy_fraction),
        normalized_full_busy_fps=fps/busy_fraction,
        result_bytes_per_detection=output_bytes/frames,
        result_MB_s=output_bytes/seconds/1e6,
        preview_MB_s=previews*110592/seconds/1e6)
    rows.append(row)
(root/'verified-calculations.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps(rows,indent=2))
