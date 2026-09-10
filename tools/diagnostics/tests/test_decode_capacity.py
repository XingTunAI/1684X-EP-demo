import importlib.util
from pathlib import Path
import unittest
import tempfile

spec=importlib.util.spec_from_file_location('capacity',Path(__file__).resolve().parents[1]/'run_decode_capacity.py')
capacity=importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)

class SummaryTests(unittest.TestCase):
    def test_weights_windows_and_filters_formal_samples(self):
        result={'status':'measured','measure_start_monotonic':10,'measure_end_monotonic':20,
                'windows':[{'seconds':2,'fps':[10,20]},{'seconds':8,'fps':[20,30]}]}
        samples=[{'monotonic_s':t,'tpu_percent':v} for t,v in [(9,0),(10,100),(12,100),(19,100),(20,0)]]
        s=capacity.summarize(result,samples,True)
        self.assertEqual(s['decode_total_fps'],46)
        self.assertEqual(s['tpu_mean_percent'],100)
        self.assertEqual(s['tpu_valid_samples'],3)
        self.assertTrue(s['full_load_observed'])

    def test_missing_or_failed_samples_do_not_certify_load(self):
        result={'status':'measured','measure_start_monotonic':10,'measure_end_monotonic':20,'windows':[]}
        samples=[{'monotonic_s':11+i,'tpu_percent':100} for i in range(3)]
        for bad in [None,True,float('nan'),101]:
            s=capacity.summarize(result,samples+[{'monotonic_s':15,'tpu_percent':bad}],True)
            self.assertFalse(s['full_load_observed'])
            self.assertEqual(s['tpu_read_failures'],1)
        self.assertIsNone(capacity.summarize(result,[],True)['tpu_mean_percent'])
        self.assertFalse(capacity.summarize(result,samples,False)['full_load_observed'])

    def test_report_keeps_missing_values_and_does_not_claim_hardware_capacity(self):
        r=capacity.summarize({'status':'error','reason':'decoder failed','windows':[]},[],True)
        r.update(streams=32,loaded=True)
        with tempfile.TemporaryDirectory() as directory:
            capacity.write_report(Path(directory),[r],True)
            text=(Path(directory)/'report.md').read_text(encoding='utf-8')
            self.assertIn('| -- | -- | -- | 0/0 | 未满足 | error |',text)
            self.assertIn('不自动认定硬件最大容量',text)

if __name__=='__main__': unittest.main()
