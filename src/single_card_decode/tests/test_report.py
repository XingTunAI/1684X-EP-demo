import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('decode_report', Path(__file__).resolve().parents[1] / 'report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_measurement_does_not_apply_fps_threshold(self):
        result = {'streams': 1, 'status': 'measured', 'windows': [{'seconds': 60, 'fps': [10]}]}
        stage, rows = report.stage_metrics(result, 24.5, measure_only=True)
        self.assertEqual(stage['total_average_fps'], 10)
        self.assertIsNone(stage['below_threshold_streams'])
        self.assertIsNone(rows[0]['fps_ok'])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'config.json').write_text(json.dumps({'config': {'steps': [1, 2], 'measure_only': True}}))
            (directory / 'summary.json').write_text(json.dumps([result]))
            self.assertFalse(report.write_report(directory))
            text = (directory / 'report.md').read_text(encoding='utf-8')
            self.assertIn('测量完成', text)
            self.assertNotIn('最大测试档位', text)

    def test_duration_weighting_and_worst_stream(self):
        result = {'streams': 2, 'status': 'fps_fail', 'windows': [
            {'seconds': 60, 'fps': [20, 30]}, {'seconds': 120, 'fps': [26, 25]}]}
        stage, rows = report.stage_metrics(result, 24.5)
        self.assertAlmostEqual(rows[0]['average_fps'], 24)
        self.assertAlmostEqual(stage['total_average_fps'], 50 + 2/3)
        self.assertEqual(stage['below_threshold_streams'], 1)
        self.assertEqual(rows[0]['below_threshold_windows'], 1)
        self.assertEqual(stage['minimum_stream_window_fps'], 20)

    def test_missing_data_is_not_zero_or_pass(self):
        stage, rows = report.stage_metrics({'streams': 2, 'status': 'error'}, 24.5)
        self.assertIsNone(stage['total_average_fps'])
        self.assertIsNone(rows[0]['fps_ok'])

    def test_failure_skips_later_stages_and_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'config.json').write_text(json.dumps({'config': {'steps': [1, 2], 'min_fps': 24.5}}))
            (directory / 'summary.json').write_text(json.dumps([{'streams': 1, 'status': 'fps_fail',
                'windows': [{'seconds': 60, 'fps': [23]}]}]))
            self.assertTrue(report.write_report(directory))
            text = (directory / 'report.md').read_text(encoding='utf-8')
            self.assertIn('前档未通过，未执行', text)
            self.assertIn('流 00: 23.00 FPS', text)
            self.assertTrue((directory / 'streams.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertTrue((directory / 'stages.csv').exists())

    def test_empty_run_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'config.json').write_text(json.dumps({'config': {'steps': [1]}}))
            self.assertFalse(report.write_report(directory))
            self.assertIn('等待执行', (directory / 'report.md').read_text(encoding='utf-8'))

    def test_interrupted_between_stages_is_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'config.json').write_text(json.dumps({'config': {'steps': [1, 2]}}))
            (directory / 'summary.json').write_text(json.dumps([{'streams': 1, 'status': 'fps_pass',
                'windows': [{'seconds': 60, 'fps': [25]}]}]))
            (directory / 'run_state.json').write_text(json.dumps({'status': 'incomplete', 'reason': 'interrupted'}))
            self.assertTrue(report.write_report(directory))
            text = (directory / 'report.md').read_text(encoding='utf-8')
            self.assertIn('本轮未完成', text)
            self.assertIn('本轮结束，未执行', text)


if __name__ == '__main__':
    unittest.main()
