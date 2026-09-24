import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('study_analysis', Path(__file__).parents[1] / 'analyze_readback_study.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class StudyAnalysisTests(unittest.TestCase):
    def test_legacy_video_clock_and_weighted_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); stage=root/'limit0'; worker=stage/'worker'; stream=worker/'stream_00'; stream.mkdir(parents=True)
            (stage/'state.json').write_text('{"status":"completed"}')
            (stage/'command.json').write_text('[]')
            (worker/'config.json').write_text('{"warmup_s":5}')
            (stream/'detections.jsonl').write_text(json.dumps({'received_monotonic_s':100.003,'decode_ms':2,'schedule_lateness_ms':1,'frame':0})+'\n')
            result={'status':'measured','records_complete':True,'measured_seconds':10,'total_completed_fps':20,'minimum_stream_fps':20,
                'streams':[{'stream_id':0,'pacing_fps':25,'decoded_fps':20,'completed_measured':200,'output_copy_ms':{'mean':0.3}}],
                'slots':[{'frames':300,'output_copy_bytes':2160000}]}
            (worker/'summary.json').write_text(json.dumps(result))
            (stage/'telemetry.jsonl').write_text(json.dumps({'monotonic':106,'returncode':0,'smi':'50%'})+'\n')
            row=analysis.summarize(root)[0]
            self.assertAlmostEqual(row['output_copy_ms'],0.3)
            self.assertAlmostEqual(row['output_MB_s'],0.144)
            self.assertEqual(row['tpu_mean_percent'],50)

    def test_only_successful_in_window_samples_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); stage=root/'r0_compute'; stage.mkdir()
            (stage/'state.json').write_text(json.dumps({'status':'completed'}))
            (stage/'command.json').write_text('[]')
            (stage/'result.json').write_text(json.dumps({'measurement_start_monotonic':10,
                'measurement_end_monotonic':20,'iterations_per_second':100,'mode':'compute'}))
            samples=[(9,0,'100%'),(10,0,'20%'),(15,1,'100%'),(16,0,'60%'),(20,0,'100%')]
            (stage/'telemetry.jsonl').write_text(''.join(json.dumps({'monotonic':t,'returncode':rc,'smi':s})+'\n' for t,rc,s in samples))
            rows=analysis.summarize(root)
            self.assertEqual(rows[0]['tpu_mean_percent'],40)
            self.assertEqual(rows[0]['tpu_samples'],2)
            (stage/'state.json').write_text(json.dumps({'status':'failed'}))
            self.assertEqual(analysis.summarize(root),[])


if __name__=='__main__': unittest.main()
