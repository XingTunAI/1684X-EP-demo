import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('diagnostics',
    Path(__file__).resolve().parents[3] / 'scripts/run_inference_diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.TestCase):
    def test_failed_worker_terminates_other_workers(self):
        failed = Mock()
        failed.poll.return_value = 1
        running = Mock()
        running.poll.return_value = None
        args = SimpleNamespace(app=Path('probe'), device=1, bmodel=Path('model'), warmup=3, duration=10)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                diagnostics.subprocess, 'Popen', side_effect=[failed, running]):
            with self.assertRaisesRegex(RuntimeError, 'before start barrier'):
                diagnostics.run_stage(args, Path(tmp), 'compute', 2)
        running.terminate.assert_called_once()
        running.wait.assert_called_once_with(timeout=5)
        failed.terminate.assert_not_called()

    def test_compute_does_not_report_fake_copy_bandwidth(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            diagnostics.save(directory, [{'mode': 'compute', 'processes': 1,
                'total_iterations_per_second': 300, 'read_MB_per_second': None, 'outputs_ok': True,
                'workers': [{'submit_mean_ms': 0.1, 'sync_mean_ms': 3.2, 'copy_mean_ms': None}]}],
                {'status': 'completed'})
            import csv
            with (directory / 'stages.csv').open(encoding='utf-8-sig', newline='') as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row['copy_mean_ms'], '')
            self.assertEqual(row['read_MB_per_second'], '')
            self.assertEqual(json.loads((directory / 'run_state.json').read_text())['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
