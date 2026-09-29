#!/usr/bin/env python3
"""Include source freshness AND longest no-result interval in catchup evidence."""
import argparse
import json
from pathlib import Path


def summarize(root):
    rows = json.loads((root/'comparison.json').read_text())
    for row in rows:
        streams = json.loads((root/row['stage']/'worker/summary.json').read_text())['streams']
        stats = [s.get('local_catchup', {}) for s in streams]
        row['catchup_events_measured'] = sum(s.get('events_measured',0) for s in stats)
        row['source_frames_skipped_measured'] = sum(s.get('source_frames_skipped_measured',0) for s in stats)
        row['maximum_no_result_interval_s'] = max(s['analysis_completion_continuity']['max_gap_including_edges_s'] for s in streams)
        costs = [s.get('cost_ms',{}) for s in stats]
        count = sum(s.get('count',0) for s in costs)
        row['catchup_mean_cost_ms'] = sum(s.get('sum',0) for s in costs)/count if count else None
        ages = [s['source_age_ms'] for s in streams]
        count = sum(s['count'] for s in ages)
        row['source_age_mean_ms'] = sum(s['sum'] for s in ages)/count if count else None
    overlaps = []
    for i in range(0,len(rows),2):
        pair = rows[i:i+2]
        if len(pair)==2:
            overlaps.append(max(0,min(r['measurement_end'] for r in pair)-max(r['measurement_start'] for r in pair)))
    result = dict(stages=rows,formal_overlap_seconds=overlaps,
                  note='Low age of delivered results does not imply continuously fresh display. Inspect no-result intervals and skipped source frames.')
    (root/'catchup-analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    print(json.dumps(summarize(parser.parse_args().root),indent=2))
