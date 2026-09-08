import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('stress_decode', Path(__file__).resolve().parents[1] / 'stress_decode.py')
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class DecodeTests(unittest.TestCase):
    def args(self):
        return bench.arguments(['--input', 'example.mp4', '--warmup', '0.2',
                                '--duration', '0.8', '--window', '0.4',
                                '--stall-timeout', '0.5', '--acceptance'])

    def test_default_is_measurement(self):
        self.assertTrue(bench.arguments(['--input', 'example.mp4']).measure_only)

    def test_zero_warmup_is_allowed(self):
        self.assertEqual(bench.arguments(['--input', 'example.mp4', '--warmup', '0']).warmup, 0)

    def test_invalid_warmup_rejected(self):
        for value in ['-1', 'nan', 'inf', '-inf']:
            with self.subTest(value=value), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    bench.arguments(['--input', 'example.mp4', '--warmup=' + value])
                self.assertEqual(caught.exception.code, 2)

    def test_card_decoder_pacing_and_no_encoder(self):
        a = self.args()
        a.device = 1
        cmd = bench.command(a, a.sources[0])
        self.assertEqual(cmd[cmd.index('-sophon_idx') + 1], '1')
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_bm')
        self.assertLess(cmd.index('-c:v'), cmd.index('-i'))
        self.assertIn('-re', cmd)
        self.assertNotIn('-r', cmd)
        self.assertEqual(cmd.count('-c:v'), 1)
        a.throughput = True
        self.assertNotIn('-re', bench.command(a, a.sources[0]))

    def test_bad_steps_rejected(self):
        for steps in ['0', '2,1', '1,1', 'abc']:
            with self.subTest(steps=steps), patch('sys.stderr', new_callable=io.StringIO) as errors:
                with self.assertRaises(SystemExit) as caught:
                    bench.arguments(['--input', 'x', '--steps', steps])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('--steps must', errors.getvalue())

    def test_wrong_video_metadata_rejected(self):
        a = self.args()
        meta = {'streams': [{'width': 1280, 'height': 720, 'codec_name': 'h264', 'avg_frame_rate': '25/1'}]}
        with patch.object(bench, 'capture', side_effect=['version', 'h264_bm', json.dumps(meta)]), patch.object(Path, 'is_file', return_value=True):
            with self.assertRaisesRegex(RuntimeError, '1920x1080'):
                bench.preflight(a)

    def test_sophon_probe_diagnostics(self):
        output = '{\nso addr : /opt/libbmvideo.so\nvpu firmware addr: /opt/firmware.bin\nVERSION=0, REVISION=364638\n"streams": []\n}'
        self.assertEqual(bench.probe_json(output), {'streams': []})
        with self.assertRaises(ValueError):
            bench.probe_json('{\nunexpected error\n"streams": []}')

    def run_fake(self, script, interrupted=None):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'step'
            with patch.object(bench, 'command', return_value=[sys.executable, '-u', '-c', script]), patch.object(bench, 'monitor'):
                result = bench.run_step(self.args(), 1, directory, interrupted or threading.Event())
            self.assertTrue((directory / 'summary.json').exists())
            return result

    def test_success_and_cleanup(self):
        result = self.run_fake('import time\nn=0\nwhile True:\n n+=3\n print("frame="+str(n),flush=True)\n time.sleep(0.05)')
        self.assertEqual(result['status'], 'fps_pass')
        self.assertGreaterEqual(result['measured_seconds'], 0.8)
        self.assertIsNone(result['end_to_end_latency'])
        self.assertEqual(len(result['exit_codes_after_cleanup']), 1)

    def test_slow_stream_fails_fps(self):
        result = self.run_fake('import time\nn=0\nwhile True:\n n+=1\n print("frame="+str(n),flush=True)\n time.sleep(0.1)')
        self.assertEqual(result['status'], 'fps_fail')

    def test_slow_stream_is_measured_without_acceptance(self):
        a = self.args()
        a.measure_only = True
        with patch.object(self, 'args', return_value=a):
            result = self.run_fake('import time\nn=0\nwhile True:\n n+=1\n print("frame="+str(n),flush=True)\n time.sleep(0.1)')
        self.assertEqual(result['status'], 'measured')
        self.assertLess(result['min_stream_window_fps'], 24.5)

    def test_clean_early_exit_is_error(self):
        result = self.run_fake('print("frame=30")')
        self.assertEqual(result['status'], 'error')
        self.assertIn('exited early', result['reason'])

    def test_stalled_process_is_error(self):
        result = self.run_fake('import time; time.sleep(30)')
        self.assertEqual(result['status'], 'error')
        self.assertIn('stalled', result['reason'])

    def test_interruption_is_incomplete(self):
        event = threading.Event()
        event.set()
        result = self.run_fake('print("frame=30")', event)
        self.assertEqual(result['status'], 'incomplete')

    def test_main_writes_reports_and_terminal_state(self):
        with tempfile.TemporaryDirectory() as root:
            fake = {'streams': 1, 'status': 'fps_pass', 'reason': 'test',
                    'windows': [{'seconds': 60, 'fps': [25]}], 'measured_seconds': 60}
            with patch.object(bench, 'preflight', return_value={}), patch.object(bench, 'run_step', return_value=fake), patch('sys.stdout', new_callable=io.StringIO):
                code = bench.main(['--input', 'example.mp4', '--output', root])
            self.assertEqual(code, 0)
            run = next(Path(root).iterdir())
            self.assertEqual(json.loads((run / 'run_state.json').read_text())['status'], 'completed')
            self.assertTrue((run / 'report.md').exists())
            self.assertTrue((run / 'streams.csv').exists())


if __name__ == '__main__':
    unittest.main()
