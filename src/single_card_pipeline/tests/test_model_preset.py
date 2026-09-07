import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('analysis_auto',
    Path(__file__).resolve().parents[3] / 'scripts/run_single_card_analysis_auto.py')
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class ModelPresetTests(unittest.TestCase):
    def test_nano_and_explicit_video(self):
        args = auto.build_arguments(['--model-preset', 'yolov8n', '--input', 'custom.mp4', '--steps', '1,4'])
        self.assertNotIn('--model-preset', args)
        self.assertTrue(args[args.index('--bmodel') + 1].endswith('/yolov8n_int8_1b.bmodel'))
        self.assertEqual(args.count('--input'), 1)
        self.assertEqual(args[-2:], ['--steps', '1,4'])

    def test_default_and_custom_model_override(self):
        args = auto.build_arguments(['--bmodel', 'custom.bmodel'])
        self.assertTrue(args[args.index('--bmodel') + 1].endswith('/yolov8s_int8_1b.bmodel'))
        self.assertEqual(args[-2:], ['--bmodel', 'custom.bmodel'])


if __name__ == '__main__':
    unittest.main()
