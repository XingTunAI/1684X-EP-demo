import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bandwidth',
    Path(__file__).resolve().parents[3] / 'scripts/run_pcie_bandwidth.py')
bandwidth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bandwidth)


class BandwidthTests(unittest.TestCase):
    def output(self):
        return '\n'.join(f'{direction} {scope}:Transfer size:0x40000 byte. '
                         'Cost time:250 us, Write Bandwidth:1000.00 MB/s'
                         for direction in ('S2D', 'D2S') for scope in ('sys', 'real')) + \
            '\ndev = 0, cdma transfer test Success.\n'

    def test_upstream_mib_label_is_converted(self):
        rows = bandwidth.parse_metrics(self.output(), 262144, 0)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['reported_MiB_s'], 1000)
        self.assertEqual(rows[0]['MB_s'], 1048.576)

    def test_failure_or_partial_metrics_are_not_bandwidth_results(self):
        for output, size, device in [
                (self.output().replace('Success', 'Failed'), 262144, 0),
                (self.output().replace('D2S real:', 'unmeasured:'), 262144, 0),
                (self.output(), 2822400, 0), (self.output(), 262144, 1)]:
            with self.subTest(size=size, device=device, output=output):
                with self.assertRaises(ValueError):
                    bandwidth.parse_metrics(output, size, device)


if __name__ == '__main__':
    unittest.main()
