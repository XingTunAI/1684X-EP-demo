import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pipeline_run', Path(__file__).resolve().parents[1] / 'run.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class VerificationTests(unittest.TestCase):
    def test_measurement_records_low_fps_but_rejects_bad_cleanup(self):
        for code, expected in [(0, 'measured'), (-9, 'error')]:
            result = {'status': 'fps_fail', 'fps_status': 'fps_fail', 'exit_codes_after_cleanup': [code]}
            pipeline.evaluate_result(result, [{'ok': True, 'max_schedule_lateness_ms': 60000}], 1, True, 1000)
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['fps_status'], 'not_evaluated')

    def test_acceptance_remains_opt_in(self):
        result = {'status': 'fps_fail', 'fps_status': 'fps_fail', 'exit_codes_after_cleanup': [0]}
        pipeline.evaluate_result(result, [{'ok': True, 'max_schedule_lateness_ms': 60000}], 1, False, 1000)
        self.assertEqual(result['status'], 'fail')

    def check(self, encoded=2, frames=(0, 1), summary=True, mode='encode'):
        with tempfile.TemporaryDirectory() as tmp:
            stream = Path(tmp) / 'stream_00'
            stream.mkdir()
            if summary:
                (stream / 'worker_summary.json').write_text(json.dumps({'frames_analyzed': 2,
                    'frames_submitted': 2 if mode == 'encode' else 0, 'mode': mode}))
            with (stream / 'detections.jsonl').open('w') as out:
                for frame in frames:
                    out.write(json.dumps({'frame': frame, 'completed_monotonic_s': 11 + frame,
                        'service_ms': 30, 'schedule_lateness_ms': 2, 'decode_ms': 5,
                        'analysis_ms': 10, 'output_transfer_ms': 3, 'cpu_postprocess_ms': 2,
                        'transfer_wait_ms': 1, 'draw_ms': 5 if mode == 'encode' else None,
                        'encode_submit_ms': 10 if mode == 'encode' else None}) + '\n')
            probe = json.dumps({'streams': [{'codec_name': 'h264', 'width': 1920,
                               'height': 1080, 'nb_read_frames': str(encoded)}]})
            with patch.object(pipeline.bench, 'capture', return_value=probe) as capture:
                result = pipeline.verify_outputs(Path(tmp), {'measure_start_monotonic': 10,
                    'measure_end_monotonic': 20}, SimpleNamespace(ffprobe='ffprobe', duration=10, warmup=5, mode=mode))[0]
                if mode == 'analysis':
                    capture.assert_not_called()
                return result

    def test_matching_outputs(self):
        result = self.check()
        self.assertTrue(result['ok'])
        self.assertEqual(result['verified_encoded_frames'], 2)
        self.assertEqual(result['service_p95_ms'], 30)

    def test_encoder_silently_loses_frame(self):
        self.assertFalse(self.check(encoded=1)['ok'])

    def test_missing_detection_frame(self):
        result = self.check(frames=(0, 2))
        self.assertFalse(result['ok'])
        self.assertIn('Missing or duplicate', result['error'])

    def test_unfinished_worker_never_passes(self):
        self.assertFalse(self.check(summary=False)['ok'])

    def test_analysis_without_video(self):
        result = self.check(mode='analysis')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['verified_encoded_frames'])
        self.assertIsNone(result['mean_stage_ms']['encode_submit_ms'])
        self.assertEqual(result['mean_stage_ms']['analysis_ms'], 10)
        self.assertEqual(result['mean_stage_ms']['output_transfer_ms'], 3)
        self.assertEqual(result['mean_stage_ms']['cpu_postprocess_ms'], 2)
        self.assertEqual(result['mean_stage_ms']['transfer_wait_ms'], 1)

    def test_analysis_missing_record_fails(self):
        self.assertFalse(self.check(mode='analysis', frames=(0,))['ok'])

    def test_analysis_unfinished_worker_fails(self):
        self.assertFalse(self.check(mode='analysis', summary=False)['ok'])

    def test_report_preserves_slow_window_and_unmeasured_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {'arguments': {'min_fps': 24.5, 'device': 1, 'sources': ['video.mp4'],
                    'target_fps': 25, 'steps': [1], 'warmup': 30, 'duration': 120},
                    'pipeline_arguments': {'mode': 'analysis', 'bmodel': 'model.bmodel',
                    'max_lateness_ms': 1000}, 'model_sha256': 'test'}
            (root / 'config.json').write_text(json.dumps(meta))
            result = {'streams': 1, 'status': 'fail', 'measured_seconds': 120,
                      'windows': [{'seconds': 60, 'fps': [20]}, {'seconds': 60, 'fps': [30]}],
                      'outputs': [{'stream': 'stream_00', 'ok': True, 'mean_stage_ms':
                          {'decode_ms': 10, 'analysis_ms': 20, 'encode_submit_ms': None}}]}
            (root / 'summary.json').write_text(json.dumps([result]))
            pipeline.write_report(root, {'status': 'failed'})
            import csv
            with (root / 'streams.csv').open(encoding='utf-8-sig', newline='') as file:
                row = next(csv.DictReader(file))
            self.assertEqual(float(row['average_fps']), 25)
            self.assertEqual(float(row['minimum_window_fps']), 20)
            self.assertEqual(row['below_threshold_windows'], '1')
            self.assertEqual(row['encode_submit_mean_ms'], '')
            self.assertEqual(row['output_transfer_mean_ms'], '')
            self.assertEqual(row['transfer_wait_mean_ms'], '')
            self.assertIn('未达标', (root / 'report.md').read_text(encoding='utf-8'))
            meta['pipeline_arguments']['measure_only'] = True
            meta['arguments']['sources'] = ['/home/private-location/video.mp4']
            result['status'] = 'measured'
            (root / 'config.json').write_text(json.dumps(meta))
            (root / 'summary.json').write_text(json.dumps([result]))
            pipeline.write_report(root, {'status': 'completed'})
            text = (root / 'report.md').read_text(encoding='utf-8')
            self.assertIn('测量完成', text)
            self.assertNotIn('private-location', text)
            self.assertNotIn('未达标', text)
            with (root / 'streams.csv').open(encoding='utf-8-sig', newline='') as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row['below_threshold_windows'], '')


if __name__ == '__main__':
    unittest.main()
