import json
from pathlib import Path
import statistics

root = Path(__file__).resolve().parent
rows = []
for folder in (root/'evidence').glob('dual-wall*'):
    if not folder.is_dir() or not (folder/'comparison.json').exists():
        continue
    samples = [json.loads(line) for line in (folder/'host-and-status.jsonl').read_text().splitlines()]
    for row in json.loads((folder/'comparison.json').read_text()):
        data = json.loads((folder/row['stage']/'worker/summary.json').read_text())
        start, end = row['measurement_start'], row['measurement_end']
        selected = [s for s in samples if start <= s['monotonic'] < end]
        cpu = {}
        for a, b in zip(selected, selected[1:]):
            before = {line.split()[0]:list(map(int,line.split()[1:9])) for line in a['proc_stat'].splitlines() if line.startswith('cpu')}
            after = {line.split()[0]:list(map(int,line.split()[1:9])) for line in b['proc_stat'].splitlines() if line.startswith('cpu')}
            for key in before:
                diff = [y-x for x,y in zip(before[key],after[key])]
                cpu.setdefault(key, []).append(100*(sum(diff)-diff[3]-diff[4])/sum(diff))
        display_ages=[]
        source_lags=[]
        for sample in selected:
            for stream in sample['status'].get(row['stage'],{}).get('streams',[]):
                if stream.get('has_image') and stream.get('age_seconds') is not None:
                    display_ages.append(stream['age_seconds'])
                if stream.get('decode_source_lag_ms') is not None:
                    source_lags.append(stream['decode_source_lag_ms'])
        row.update(experiment=folder.name,
            accounting_complete=data['accounting_complete'],
            cpu_mean_percent={k:statistics.mean(v) for k,v in cpu.items()},
            actual_preview_fps_per_stream=row['preview_MB_s']*1e6/110592/32,
            max_sampled_preview_age_s=max(display_ages,default=None),
            max_sampled_decode_source_lag_ms=max(source_lags,default=None))
        rows.append(row)
for row in rows:
    paired=next(r for r in rows if r['experiment']==row['experiment'] and r['configuration']==row['configuration'] and r['device']!=row['device'])
    row['dual_measurement_overlap_s']=max(0,min(row['measurement_end'],paired['measurement_end'])-max(row['measurement_start'],paired['measurement_start']))
(root/'analysis.json').write_text(json.dumps(rows,indent=2)+'\n')
for r in rows:
    print(r['stage'],*(round(r[k],3) for k in ['fps','tpu_percent','minimum_stream_fps','maximum_no_result_interval_s','actual_preview_fps_per_stream','dual_measurement_overlap_s']), 'CPU',round(r['cpu_mean_percent']['cpu'],1))
