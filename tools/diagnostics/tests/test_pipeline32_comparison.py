import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

DIAGNOSTICS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIAGNOSTICS))
spec = importlib.util.spec_from_file_location('pipeline32', DIAGNOSTICS/'run_pipeline32_comparison.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.step = self.root/'worker/run/step_01'
        self.stream = self.step/'stream_00'
        self.stream.mkdir(parents=True)
        self.write(self.step/'summary.json', {'status':'measured', 'outputs':[{'ok':True}],
                  'measure_start_monotonic':10, 'measure_end_monotonic':20})
        self.records = [{'frame':i, 'completed_monotonic_s':t, 'score_gate_read_bytes':1000,
                         'schedule_lateness_ms':t, 'decode_ms':2} for i,t in enumerate([9,10,15,20])]
        self.flush_records()
        samples = [{'monotonic':t, 'returncode':0, 'smi':f'TPU {percent}%'}
                   for t,percent in [(9,100),(10,40),(15,60),(20,0)]]
        (self.root/'telemetry.jsonl').write_text('\n'.join(json.dumps(r) for r in samples))

    def write(self, path, data):
        path.write_text(json.dumps(data))

    def flush_records(self):
        (self.stream/'detections.jsonl').write_text('\n'.join(json.dumps(r) for r in self.records))

    def test_formal_window_excludes_warmup_and_end_boundary(self):
        row = study.summarize(self.root, 'analysis_device', 1)
        self.assertEqual(row['fps'], .2)
        self.assertEqual(row['tpu_percent'], 50)
        self.assertEqual(row['tpu_samples'], 2)
        self.assertEqual(row['result_MB_s'], .0002)
        self.assertIsNone(row['decode_fps'])
        self.assertIsNone(row['stage_mean_ms']['encode_submit_ms'])

    def test_rejects_worker_not_ready_before_window(self):
        self.records = self.records[1:]
        self.flush_records()
        with self.assertRaisesRegex(RuntimeError, 'initialization'):
            study.summarize(self.root, 'analysis_device', 1)

    def test_rejects_missing_stream_instead_of_reporting_partial_success(self):
        with self.assertRaisesRegex(RuntimeError, 'stream count'):
            study.summarize(self.root, 'analysis_device', 32)

    def test_rejects_failed_video_integrity(self):
        self.write(self.step/'summary.json', {'status':'measured', 'outputs':[{'ok':False}]})
        with self.assertRaisesRegex(RuntimeError, 'integrity'):
            study.summarize(self.root, 'encode_bgr', 1)


if __name__ == '__main__':
    unittest.main()
