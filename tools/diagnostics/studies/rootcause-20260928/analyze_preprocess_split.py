"""Audit observer events and compare formal-window API time to app stage time."""
import csv
import json
import statistics
from pathlib import Path

root = Path(__file__).resolve().parent / 'preprocess-split'
comparisons = json.loads((root/'comparison.json').read_text())
report = {'controls': [], 'observed': []}
for row in comparisons:
    report['controls'].append({k: row[k] for k in (
        'configuration','device','fps','tpu_percent','minimum_stream_fps',
        'zero_streams','dropped_stale_lifetime','maximum_no_result_interval_s')}
        | {'preprocess_ms': row['stage_mean_ms']['preprocess_ms']})
    if row['configuration'] != 'observed':
        continue
    start, end = row['measurement_start'], row['measurement_end']
    pairs, thumbs, threads, event_counts = [], [], [], {}
    lost = errors = 0
    for path in sorted((root/f"traces_device{row['device']}").glob('*.csv')):
        with path.open() as f:
            lost += int(f.readline().split('lost=')[1])
            events = list(csv.DictReader(f))
        previous = None
        thread_pairs = 0
        for event in events:
            b, e = float(event['begin']), float(event['end'])
            assert e >= b
            errors += int(event['ret']) != 0
            signature = tuple(int(event[k]) for k in ('kind','iw','ih','ow','oh'))
            event_counts[str(signature)] = event_counts.get(str(signature), 0)+1
            if signature == (1,1920,1080,640,640):
                assert previous is None
                previous = (b,e)
            elif signature == (2,640,640,640,640):
                assert previous is not None, (path, event)
                if start <= previous[0] and e <= end:
                    pairs.append({'vpp_ms':(previous[1]-previous[0])*1000,
                                  'convert_ms':(e-b)*1000,
                                  'between_ms':(b-previous[1])*1000})
                    thread_pairs += 1
                previous = None
            elif signature == (1,1920,1080,256,144) and start <= b and e <= end:
                thumbs.append((e-b)*1000)
        assert previous is None, path
        if thread_pairs:
            threads.append({'tid':path.stem,'pairs':thread_pairs})
    assert lost == errors == 0
    assert len(threads) == 32
    stats = {k:statistics.mean(p[k] for p in pairs) for k in pairs[0]}
    stats['sum_ms'] = sum(stats.values())
    stats['app_preprocess_ms'] = row['stage_mean_ms']['preprocess_ms']
    stats['closure_difference_ms'] = stats['app_preprocess_ms']-stats['sum_ms']
    before = next(r for r in comparisons if r['device']==row['device'] and r['configuration']=='baseline_before')
    after = next(r for r in comparisons if r['device']==row['device'] and r['configuration']=='baseline_after')
    mean_fps = (before['fps']+after['fps'])/2
    report['observed'].append({'device':row['device'],'formal_pairs':len(pairs),
        'stats':stats,'formal_thumbnail_calls':len(thumbs),
        'thumbnail_mean_ms':statistics.mean(thumbs),
        'app_thumbnail_vpp_ms':row['stage_mean_ms']['preview_vpp_ms'],
        'zero_return_failures':errors,'lost_events':lost,'threads':threads,
        'all_event_signatures':event_counts,
        'fps_delta_from_control_mean_percent':100*(row['fps']/mean_fps-1)})
report['scope'] = ('Host SDK API wall time including queueing, not device-only execution. '
    'Observer selects complete VPP+convert pairs within formal time; app counts frames '
    'completed within formal time, so edge populations differ slightly. '
    'Controls assess drift/perturbation, not statistical equivalence.')
(root/'api-analysis.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({**report,'observed':[
    {k:v for k,v in r.items() if k not in ('threads','all_event_signatures')}
    for r in report['observed']]},indent=2))
