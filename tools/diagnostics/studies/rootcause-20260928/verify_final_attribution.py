"""Recompute final tables from audited phase/format summaries; never overwrite output."""
from pathlib import Path
import hashlib
import json
from statistics import mean

ROOT = Path(__file__).resolve().parent
inputs = {}


def read(name):
    p = ROOT / name
    raw = p.read_bytes()
    inputs[name] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


trace = read('vpp-attribution-v2/analysis-v2/summary.json')
phases = []
for capture in trace['captures']:
    sums = {}
    for device in ('0', '2'):
        roles = capture['devices'][device]['roles']
        keys = roles['pre_vpp']['partition_mean_us'].keys()
        sums[device] = {
            k.removesuffix('_us') + '_ms': sum(roles[r]['partition_mean_us'][k] for r in ('pre_vpp', 'pre_convert')) / 1000
            for k in keys
        }
        sums[device]['total_ms'] = sum(sums[device].values())
    delta = {k: sums['0'][k] - sums['2'][k] for k in sums['0']}
    phases.append({'capture': capture['name'], 'device_mean_ms': sums, 'difference_ms': delta,
                   'admission_difference_over_total_difference': delta['admission_ms'] / delta['total_ms']})

formats = read('decoder-format32-buffers8/format-analysis.json')['rows']
dual = {}
for device in (0, 2):
    linear = [r for r in formats if r['device'] == device and r['actual_format'] == 0]
    compressed = [r for r in formats if r['device'] == device and r['actual_format'] == 101]
    assert len(linear) == 2 and len(compressed) == 1
    assert all(r['extra_buffers'] == 8 and r['zero_streams'] == 0 and
               r['dropped_stale_lifetime'] == 0 for r in linear + compressed)
    assert all(r['driver_calls_in_formal_window'].get('unclassified', 0) == 0 for r in linear)
    c = compressed[0]
    dual[str(device)] = {
        'linear_mean_fps': mean(r['fps'] for r in linear),
        'linear_mean_tpu_percent': mean(r['tpu_percent'] for r in linear),
        'linear_mean_preprocess_ms': mean(r['stage_mean_ms']['preprocess_ms'] for r in linear),
        'linear_mean_actual_preview_fps_per_stream': mean(r['actual_preview_fps_per_stream'] for r in linear),
        'compressed_fps': c['fps'], 'compressed_tpu_percent': c['tpu_percent'],
        'compressed_actual_preview_fps_per_stream': c['actual_preview_fps_per_stream'],
        'linear_gain_percent': (mean(r['fps'] for r in linear) / c['fps'] - 1) * 100,
    }

same = read('samecard-format/format-analysis.json')['rows']
assert read('samecard-format/state.json')['restored_speed'].startswith('8.0')
assert len(same) == 6
same_result = []
for fmt in (101, 0):
    high = [r for r in same if r['actual_format'] == fmt and r['link_gt_s'] == 8]
    low = [r for r in same if r['actual_format'] == fmt and r['link_gt_s'] == 5]
    assert len(high) == 2 and len(low) == 1
    for r in high + low:
        assert r['zero_streams'] == 0 and r['extra_buffers'] == 8
    same_result.append({'format': fmt,
        'high_8GT_mean_fps': mean(r['fps'] for r in high),
        'low_5GT_fps': low[0]['fps'],
        'low_rate_loss_percent': (1 - low[0]['fps'] / mean(r['fps'] for r in high)) * 100,
        'rows': [{k: r[k] for k in ('stage', 'link_gt_s', 'fps', 'tpu_percent',
                 'dropped_stale_lifetime', 'maximum_no_result_interval_s',
                 'actual_preview_fps_per_stream', 'max_sampled_decode_lag_ms')} for r in high + low]})

bandwidth = {'gen2_B_s': 5e9 * .8 / 8, 'gen3_B_s': 8e9 * 128 / 130 / 8}
theory = dict(bandwidth,
    descriptor_B=336,
    descriptor_ideal_us_gen2=336 / bandwidth['gen2_B_s'] * 1e6,
    descriptor_ideal_us_gen3=336 / bandwidth['gen3_B_s'] * 1e6,
    fixed_background_requests_per_s=32 * 24,
    buffer_increment_unaligned_bytes=6 * 32 * 1920 * 1080 * 3 // 2)

report = {'inputs_sha256': inputs, 'trace_preprocess_partition': phases,
          'dual_buffers8': dual, 'samecard': same_result, 'theory': theory,
          'dual_linear_fps_gap_percent': (1 - dual['0']['linear_mean_fps'] / dual['2']['linear_mean_fps']) * 100,
          'notes': ['Means of API types are added, not individual frame-paired trace hardware times.',
                    'Same-card standalone load is separate from simultaneous dual-card load.',
                    'PCIe serialization lower bounds do not predict wall-clock API duration.',
                    'Buffer memory is an unaligned estimate, not measured device memory usage.']}
with (ROOT / 'final-verification.json').open('x') as f:
    json.dump(report, f, indent=2)
    f.write('\n')
print(json.dumps({'dual': dual, 'samecard': same_result, 'theory': theory}, indent=2))
