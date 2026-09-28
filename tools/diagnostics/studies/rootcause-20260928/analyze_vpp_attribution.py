"""Analysis v2. Preserve parsed roots, exclusions, matches and raw event files."""
import csv
import json
from collections import Counter,defaultdict
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/diagnostics'))
from analyze_dispatch_graph import parse_trace
from analyze_dispatch_wait import negative_paths,root_entries

def match_envelope(by_tid,tid,begin,end,tolerance=2e-6):
    candidates=[e for e in by_tid.get(tid,[]) if e['begin']-tolerance<=begin and end<=e['end']+tolerance]
    return candidates[0] if len(candidates)==1 else None

def partition(s):
    if negative_paths(s):raise ValueError('inconsistent nested durations')
    empty={'inclusive_us':0,'children':{}}
    c=s['children'];a=c.get('vpp_admission',empty);d=c.get('descriptor_upload',empty)
    w=c.get('vpp_completion_wait',empty);cd=d['children'].get('cdma_call',empty)
    mutex=cd['children'].get('direct_mutex_calls',empty)['inclusive_us']
    return {'admission_us':a['inclusive_us'],'cdma_mutex_us':mutex,
        'cdma_other_us':cd['inclusive_us']-mutex,
        'descriptor_other_us':d['inclusive_us']-cd['inclusive_us'],
        'completion_us':w['inclusive_us'],
        'other_us':s['inclusive_us']-a['inclusive_us']-d['inclusive_us']-w['inclusive_us']}

def stats(values):
    values=sorted(values)
    if not values:return {'count':0}
    return {'count':len(values),'mean':statistics.mean(values),'min':min(values),'p50':statistics.median(values),
        'p95':values[min(len(values)-1,int(len(values)*.95))],'max':max(values)}

def role(e):
    if e['kind']==1 and e['ow']==640:return 'pre_vpp'
    if e['kind']==2 and e['ow']==640:return 'pre_convert'
    if e['kind']==1 and e['ow']==256:return 'thumbnail'
    return 'unclassified'

def load_events(folder):
    outer={};drivers=defaultdict(list);loss=errors=0
    for path in folder.glob('*.csv'):
        tid=int(path.stem)
        with path.open() as f:
            loss+=int(f.readline().split('lost=')[1])
            for raw in csv.DictReader(f):
                e={k:(float(v) if k in ('begin','end') else int(v)) for k,v in raw.items()}
                assert e['end']>=e['begin'];errors+=e['ret']!=0;e['tid']=tid
                if e['kind']==3:drivers[tid].append(e)
                else:outer[(tid,e['id'])]=e
    assert loss==errors==0,(loss,errors)
    for tid,es in drivers.items():
        es.sort(key=lambda e:e['begin'])
        for e in es:
            e['role']='unclassified'
            if e['parent']:
                p=outer[(tid,e['parent'])]
                assert p['begin']<=e['begin']<=e['end']<=p['end']
                e['role']=role(p);p.setdefault('drivers',[]).append(e)
    for p in outer.values():
        nested=sum(e['end']-e['begin'] for e in p.get('drivers',[]))
        assert nested<=p['end']-p['begin']+1e-6
    return outer,drivers

def api_summary(outer,begin,end):
    groups=defaultdict(list)
    for p in outer.values():
        if begin<=p['begin'] and p['end']<=end:groups[role(p)].append(p)
    result={}
    for k,ps in groups.items():
        result[k]={'api_ms':stats([(p['end']-p['begin'])*1000 for p in ps]),
            'driver_ms':stats([sum(e['end']-e['begin'] for e in p.get('drivers',[]))*1000 for p in ps]),
            'driver_counts':dict(Counter(len(p.get('drivers',[])) for p in ps)),
            'descriptor_counts':dict(Counter(e['batch'] for p in ps for e in p.get('drivers',[])))}
    return result

def throughput(samples,stage,begin,end):
    selected={s[stage]['snapshot_monotonic_s']:s[stage] for s in samples
        if stage in s and begin<=s[stage]['snapshot_monotonic_s']<=end}
    ss=[selected[t] for t in sorted(selected)]
    if len(ss)<2:return None
    a,b=ss[0],ss[-1];duration=b['snapshot_monotonic_s']-a['snapshot_monotonic_s']
    count=b['results_readback_completed']-a['results_readback_completed']
    deltas=[y['inferred_count']-x['inferred_count'] for x,y in zip(a['streams'],b['streams'])]
    return {'seconds':duration,'fps':count/duration,'min_stream_fps':min(deltas)/duration,
            'begin':a['snapshot_monotonic_s'],'end':b['snapshot_monotonic_s'],'samples':len(ss)}

def main(root):
    destination=root/'analysis-v2';destination.mkdir(exist_ok=False)
    comparisons=json.loads((root/'comparison.json').read_text())
    events={};report={'runs':[],'captures':[]}
    samples=[]
    with (root/'status-samples.jsonl').open() as f:
        for line in f:samples.append(json.loads(line)['status'])
    for row in comparisons:
        stage=row['stage'];outer,drivers=load_events(root/f'events_{stage}');events[stage]=(outer,drivers)
        report['runs'].append({'stage':stage,'fps':row['fps'],'tpu':row['tpu_percent'],
            'zero_streams':row['zero_streams'],'stale_lifetime':row['dropped_stale_lifetime'],
            'max_gap_s':row['maximum_no_result_interval_s'],
            'api':api_summary(outer,row['measurement_start'],row['measurement_end'])})
    for path in sorted(root.glob('vpp*.trace')):
        cap=json.loads(path.with_suffix('.capture.json').read_text());text=path.read_text()
        for v in cap['cpu_stats'].values():
            for line in v.splitlines():
                if line.startswith(('overrun:','commit overrun:','dropped events:')):assert int(line.split(':')[1])==0
        parsed=parse_trace(text,['bm1686_trigger_vpp']);entries=root_entries(text,['bm1686_trigger_vpp'])
        (destination/(path.stem+'-parsed.json')).write_text(json.dumps(parsed,indent=2)+'\n')
        starts=cap['after_enable_monotonic'];ends=cap['before_disable_monotonic']
        case={'name':path.stem,'capture':cap,'entries':entries,'parser_diagnostics':parsed['diagnostics'],
              'devices':{},'unmatched':[],'negative_roots':[]}
        matches=defaultdict(list);seen=set()
        all_drivers={}
        for device in (0,2):
            outer,by_tid=events[f'traced_device{device}']
            for tid,es in by_tid.items():
                assert tid not in all_drivers
                all_drivers[tid]=[dict(e,device=device) for e in es if e['end']>=starts-1 and e['begin']<=ends+1]
        for c in parsed['calls']:
            if negative_paths(c['stages']):case['negative_roots'].append(c['start_line']);continue
            b=float(c['start_timestamp'])
            # Only an observed return supplies an independently checked end.
            if c['return_timestamp'] is None:case['unmatched'].append({'line':c['start_line'],'reason':'no explicit return'});continue
            e=float(c['return_timestamp'])
            wrapped=match_envelope(all_drivers,c['tid'],b,e)
            if wrapped is None:
                case['unmatched'].append({'line':c['start_line'],'reason':'no unique same-TID wrapper enclosure'});continue
            wrapper_us=(wrapped['end']-wrapped['begin'])*1e6
            if c['duration_us']>wrapper_us+10:
                case['unmatched'].append({'line':c['start_line'],'reason':'graph duration exceeds enclosing wrapper by >10 us',
                    'graph_us':c['duration_us'],'wrapper_us':wrapper_us});continue
            key=(c['tid'],wrapped['id']);assert key not in seen;seen.add(key)
            d=wrapped['device'];r=wrapped['role'];parts=partition(c['stages'])
            matches[(d,r)].append({'tid':c['tid'],'wrapper_id':wrapped['id'],'parent_id':wrapped['parent'],
                'start_line':c['start_line'],'end_line':c['return_line'],
                'graph_duration_us':c['duration_us'],'wrapper_duration_us':(wrapped['end']-wrapped['begin'])*1e6,
                'timestamp_duration_us':(e-b)*1e6,'partition_us':parts,
                'observed_stages':list(c['stages']['children'])})
        for device in (0,2):
            stage=f'traced_device{device}';outer,by_tid=events[stage]
            row=next(x for x in comparisons if x['stage']==stage)
            assert row['measurement_start']<starts<ends<row['measurement_end']
            windows={'capture':(starts,ends),'before':(starts-12,starts-2),'after':(ends+2,ends+12)}
            dd={'windows':{k:{'throughput':throughput(samples,stage,*v),'api':api_summary(outer,*v)} for k,v in windows.items()},'roles':{}}
            for r in ['pre_vpp','pre_convert','thumbnail','unclassified']:
                accepted=matches[(device,r)]
                expected=[e for es in by_tid.values() for e in es if e['role']==r and starts<=e['begin'] and e['end']<=ends]
                dd['roles'][r]={'paired_roots':len(accepted),'complete_wrapper_calls_in_capture':len(expected),
                    'graph_us':stats([x['graph_duration_us'] for x in accepted]),
                    'wrapper_minus_graph_us':stats([x['wrapper_duration_us']-x['graph_duration_us'] for x in accepted]),
                    'timestamp_minus_graph_us':stats([x['timestamp_duration_us']-x['graph_duration_us'] for x in accepted]),
                    'partition_mean_us':{k:statistics.mean(x['partition_us'][k] for x in accepted) for k in accepted[0]['partition_us']} if accepted else {},
                    'stage_presence_counts':dict(Counter(k for x in accepted for k in x['observed_stages']))}
            case['devices'][device]=dd
        (destination/(path.stem+'-matches.json')).write_text(json.dumps({str(k):v for k,v in matches.items()},indent=2)+'\n')
        report['captures'].append(case)
    (destination/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print('wrote',destination)
    for r in report['runs']:
        print(r['stage'],round(r['fps'],2),round(r['tpu'],2),{k:(round(v['api_ms']['mean'],3),round(v['driver_ms']['mean'],3),v['driver_counts']) for k,v in r['api'].items()})
    for cap in report['captures']:
        print(cap['name'],'unmatched',len(cap['unmatched']),'negative',len(cap['negative_roots']))
        for d,dd in cap['devices'].items():
            print('device',d,'FPS windows',{k:round(w['throughput']['fps'],2) if w['throughput'] else None for k,w in dd['windows'].items()})
            for r,v in dd['roles'].items():
                print(r,v['paired_roots'],v['complete_wrapper_calls_in_capture'],{k:round(x/1000,3) for k,x in v['partition_mean_us'].items()},v['wrapper_minus_graph_us'])

if __name__=='__main__':main(Path(sys.argv[1]))
