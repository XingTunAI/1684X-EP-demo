import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('_shared_yolo_runner', Path(__file__).resolve().parents[1] / 'yolo_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakeProcess:
    def __init__(self, pid, returncode=None, ignore_term=False):
        self.pid = pid
        self.returncode = returncode
        self.ignore_term = ignore_term

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('fake child', timeout)
        return self.returncode


class PlanTests(unittest.TestCase):
    def test_single_card_defaults(self):
        for family in ('yolov8', 'yolo26'):
            with self.subTest(family=family):
                plan = runner.build_plan(runner.arguments(family, []), 'example')
                self.assertEqual([c['device'] for c in plan['cards']], [0])
                self.assertEqual(plan['streams_per_card'], 1)
                self.assertTrue(plan['run_directory'].endswith(str(Path('data/results') / family / 'example')))
                self.assertEqual(plan['timing_semantics']['duration_seconds'], 60)
                self.assertEqual(plan['protection_timeout_seconds'], 785)
                self.assertIn(str(Path('sample') / ('YOLOv8_plus_det' if family == 'yolov8' else 'YOLO26') / 'datasets'), plan['input'])
                self.assertIn('/' + ('yolov8s_int8_1b.bmodel' if family == 'yolov8' else 'yolo26s_fp32_1b.bmodel'), plan['bmodel'].replace('\\', '/'))

    def test_dual_card_duration_and_backend_contracts(self):
        for family in ('yolov8', 'yolo26'):
            args = runner.arguments(family, ['--devices', '0,1', '--duration', '300', '--warmup', '0', '--streams', '4'])
            plan = runner.build_plan(args, 'example')
            self.assertEqual(len(plan['cards']), 2)
            self.assertEqual(plan['protection_timeout_seconds'], 1020)
            for device, card in enumerate(plan['cards']):
                command = card['command']
                self.assertEqual(command[command.index('--device') + 1], str(device))
                self.assertEqual(float(command[command.index('--duration') + 1]), 300)
                self.assertEqual(float(command[command.index('--warmup') + 1]), 0)
                self.assertEqual(float(command[command.index('--window') + 1]), 10)
                self.assertEqual(command[command.index('--output') + 1], card['output_directory'])
                self.assertTrue(card['output_directory'].endswith('device_' + str(device)))
                self.assertNotIn('--infer-fps', command)
                if family == 'yolov8':
                    self.assertEqual(command[0], sys.executable)
                    self.assertTrue(command[1].endswith(str(Path('demos/yolov8/runner.py'))))
                    self.assertEqual(command[command.index('--steps') + 1], '4')
                    self.assertIn('--measure-only', command)
                    self.assertEqual(command[command.index('--mode') + 1], 'analysis')
                    self.assertTrue(command[command.index('--app') + 1].endswith(str(Path('demos/yolov8/build/pipeline_worker.pcie'))))
                else:
                    self.assertTrue(command[0].endswith(str(Path('demos/yolo26/build/yolo26_streams.pcie'))))
                    self.assertEqual(command[command.index('--streams') + 1], '4')
                    for name, value in (('--pace-fps', '0'), ('--image-path', 'bgr'), ('--local-eof', 'loop')):
                        self.assertEqual(command[command.index(name) + 1], value)

    def test_four_cards_and_arbitrary_order(self):
        for ids in ('0,1,2,3', '3,0', '2'):
            plan = runner.build_plan(runner.arguments('yolo26', ['--devices', ids]))
            self.assertEqual([c['device'] for c in plan['cards']], [int(x) for x in ids.split(',')])

    def test_relative_paths_use_repo_and_explicit_model_names(self):
        args = runner.arguments('yolov8', ['--input', 'data/inputs/custom.mp4', '--bmodel', 'data/models/custom.bmodel',
                                          '--classnames', 'data/models/custom.names', '--duration', '0.25'])
        self.assertEqual(args.input, runner.ROOT / 'data/inputs/custom.mp4')
        self.assertEqual(args.bmodel, runner.ROOT / 'data/models/custom.bmodel')
        self.assertEqual(args.classnames, runner.ROOT / 'data/models/custom.names')
        self.assertEqual(runner.build_plan(args)['timing_semantics']['window_seconds'], 0.25)

    def test_invalid_arguments(self):
        invalid = ([['--devices', value] for value in ('', '0,', '0,0', '1,01', '-1', '4', '0,5', 'one', '0.1')]
                   + [['--duration', value] for value in ('0', '-1', 'nan', 'inf', '-inf')]
                   + [['--warmup', value] for value in ('-1', 'nan', 'inf')]
                   + [['--streams', value] for value in ('0', '33', '1.5', 'nan')]
                   + [['--input', ''], ['--input', 'rtsp://example.invalid/video'], ['--bmodel', ''],
                      ['--model', 'yolo26'], ['--duration', '1e308', '--warmup', '1e308']])
        for supplied in invalid:
            with self.subTest(arguments=supplied), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    runner.arguments('yolov8', supplied)
                self.assertEqual(error.exception.code, 2)

    def test_dry_run_has_no_file_checks_or_mutations(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'not-created'
            with patch.object(runner, 'preflight') as preflight, patch.object(Path, 'is_file') as is_file, \
                 patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'write_text') as write, \
                 patch.object(runner.subprocess, 'Popen') as popen, patch('sys.stdout', new_callable=io.StringIO) as stdout:
                self.assertEqual(runner.main('yolo26', ['--devices', '0,1,2,3', '--output', str(output), '--dry-run']), 0)
                plan = json.loads(stdout.getvalue())
                self.assertEqual(len(plan['cards']), 4)
                for operation in (preflight, is_file, mkdir, write, popen):
                    operation.assert_not_called()
            self.assertFalse(output.exists())

    def test_non_linux_actual_run_rejected_before_outputs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runner.sys, 'platform', 'win32'), \
             patch('sys.stderr', new_callable=io.StringIO):
            output = Path(temporary) / 'not-created'
            self.assertEqual(runner.main('yolov8', ['--output', str(output)]), 2)
            self.assertFalse(output.exists())


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        # Windows supports dry-run only; supply the Linux signal in mocked cleanup tests.
        kill_signal = patch.object(signal, 'SIGKILL', getattr(signal, 'SIGKILL', 9), create=True)
        kill_signal.start()
        self.addCleanup(kill_signal.stop)

    def plan(self, temporary, devices='0,1'):
        return runner.build_plan(runner.arguments('yolo26', ['--devices', devices, '--output', temporary]), 'run')

    def supervise(self, plan, children, event=None, received_signal=None, on_spawn=None):
        by_pid = {p.pid: p for p in children if isinstance(p, FakeProcess)}
        spawned, signals = [], []
        def popen(command, **kwargs):
            item = children[len(spawned)]
            spawned.append((command, kwargs))
            if isinstance(item, Exception):
                raise item
            if on_spawn:
                on_spawn(len(spawned))
            return item
        def send(pid, signum):
            signals.append((pid, signum))
            process = by_pid[pid]
            if signum == signal.SIGKILL or not process.ignore_term:
                process.returncode = -signum
        with patch.object(runner.subprocess, 'Popen', side_effect=popen), \
             patch.object(runner, 'group_alive', side_effect=lambda pid: by_pid[pid].returncode is None), \
             patch.object(runner, 'signal_group', side_effect=send), patch.object(runner, 'TERMINATE_GRACE', 0):
            code = runner.supervise(plan, event or threading.Event(), received_signal or [])
        summary = json.loads((Path(plan['run_directory']) / 'summary.json').read_text(encoding='utf-8'))
        return code, summary, spawned, signals

    def test_all_cards_complete_and_output_children_not_precreated(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.plan(temporary)
            code, summary, spawned, signals = self.supervise(plan, [FakeProcess(101, 0), FakeProcess(102, 0)])
            self.assertEqual(code, 0)
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(signals, [])
            self.assertNotIn('total_fps', summary)
            for card, (_, options) in zip(plan['cards'], spawned):
                self.assertFalse(Path(card['output_directory']).exists())
                self.assertTrue(Path(card['log_file']).is_file())
                self.assertTrue(options['start_new_session'])
                self.assertEqual(options['cwd'], runner.ROOT)
                self.assertNotIn('shell', options)
            self.assertTrue((Path(plan['run_directory']) / 'run.json').is_file())

    def test_failure_cancels_only_owned_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, summary, _, signals = self.supervise(self.plan(temporary), [FakeProcess(101, 7), FakeProcess(102)])
            self.assertEqual(code, 1)
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual([c['status'] for c in summary['cards']], ['failed', 'cancelled'])
            self.assertEqual([c['exit_code'] for c in summary['cards']], [7, -signal.SIGTERM])
            self.assertEqual(signals, [(102, signal.SIGTERM)])

    def test_spawn_failure_cancels_earlier_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, summary, _, signals = self.supervise(self.plan(temporary), [FakeProcess(101), OSError('test spawn failure')])
            self.assertEqual(code, 1)
            self.assertEqual([c['status'] for c in summary['cards']], ['cancelled', 'failed'])
            self.assertIsNone(summary['cards'][1]['pid'])
            self.assertEqual(signals, [(101, signal.SIGTERM)])

    def test_sigint_and_sigterm_stop_without_launching_more_cards(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as temporary:
                event = threading.Event()
                code, summary, spawned, signals = self.supervise(self.plan(temporary), [FakeProcess(101)],
                    event=event, received_signal=[signum], on_spawn=lambda _count: event.set())
                self.assertEqual(code, 128 + signum)
                self.assertEqual(summary['status'], 'interrupted')
                self.assertEqual(summary['signal'], signum)
                self.assertEqual(len(spawned), 1)
                self.assertTrue(all(c['status'] == 'interrupted' for c in summary['cards']))
                self.assertEqual(signals, [(101, signal.SIGTERM)])

    def test_timeout_is_failure_and_escalates_only_owned_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.plan(temporary, '3')
            plan['protection_timeout_seconds'] = 0
            code, summary, _, signals = self.supervise(plan, [FakeProcess(103, ignore_term=True)])
            self.assertEqual(code, 124)
            self.assertEqual(summary['status'], 'timed_out')
            self.assertEqual(summary['cards'][0]['status'], 'timed_out')
            self.assertEqual(summary['cards'][0]['exit_code'], -signal.SIGKILL)
            self.assertEqual(signals, [(103, signal.SIGTERM), (103, signal.SIGKILL)])

    def test_mixed_completed_and_failed_never_reports_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, summary, _, _ = self.supervise(self.plan(temporary), [FakeProcess(101, 0), FakeProcess(102, 3)])
            self.assertEqual(code, 1)
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual([c['status'] for c in summary['cards']], ['completed', 'failed'])

    def test_existing_run_directory_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.plan(temporary)
            directory = Path(plan['run_directory'])
            directory.mkdir()
            saved = directory / 'summary.json'
            saved.write_text('existing', encoding='utf-8')
            with patch.object(runner.subprocess, 'Popen') as popen, self.assertRaises(FileExistsError):
                runner.supervise(plan, threading.Event(), [])
            popen.assert_not_called()
            self.assertEqual(saved.read_text(encoding='utf-8'), 'existing')

    def test_released_group_is_never_rechecked_or_signalled_after_id_reuse(self):
        child = {'process': FakeProcess(101, 0), 'card': {'device': 0, 'status': 'completed'}}
        with patch.object(runner, 'group_alive', return_value=False) as alive:
            self.assertFalse(runner.owned_group_alive(child))
            self.assertTrue(child['group_closed'])
            alive.assert_called_once_with(101)
        # Another job could now have reused 101; this launcher no longer owns it.
        with patch.object(runner, 'group_alive', return_value=True) as alive, patch.object(runner, 'signal_group') as send:
            self.assertEqual(runner.stop_children([child]), [])
            alive.assert_not_called()
            send.assert_not_called()

    def test_remaining_descendants_are_reported_after_leader_reap(self):
        child = {'process': FakeProcess(101, 0), 'card': {'device': 0, 'status': 'completed'}}
        with patch.object(runner, 'group_alive', return_value=True), patch.object(runner, 'signal_group') as send, \
             patch.object(runner, 'TERMINATE_GRACE', 0), patch.object(runner, 'KILL_GRACE', 0):
            errors = runner.stop_children([child])
        self.assertTrue(any('process group remains after bounded cleanup' in error for error in errors))
        self.assertEqual([call.args for call in send.call_args_list], [(101, signal.SIGTERM), (101, signal.SIGKILL)])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Real process-group tests require Linux; no SOPHON device is used')
class LinuxProcessTests(unittest.TestCase):
    def run_scripts(self, temporary, scripts):
        plan = runner.build_plan(runner.arguments('yolo26', ['--devices', ','.join(str(i) for i in range(len(scripts))),
                                                           '--output', temporary]), 'run')
        plan['protection_timeout_seconds'] = 5
        for card, script in zip(plan['cards'], scripts):
            card['command'] = [sys.executable, '-u', '-c', script]
        with patch.object(runner, 'TERMINATE_GRACE', 0.5), patch.object(runner, 'KILL_GRACE', 2):
            code = runner.supervise(plan, threading.Event(), [])
        return code, plan

    def test_real_single_and_dual_process_success(self):
        for count in (1, 2):
            with self.subTest(cards=count), tempfile.TemporaryDirectory() as temporary:
                code, plan = self.run_scripts(temporary, ["print('virtual backend completed')"] * count)
                self.assertEqual(code, 0)
                self.assertEqual(plan['status'], 'completed')
                self.assertTrue(all(card['process_group_released'] for card in plan['cards']))

    def test_real_failure_cleans_descendants_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / 'descendant.pid'
            slow = '\n'.join([
                'import signal, subprocess, sys, time', 'from pathlib import Path',
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])",
                'def stop(*_):', '    child.wait(timeout=2)', '    raise SystemExit(0)',
                'signal.signal(signal.SIGTERM, stop)',
                'Path(%r).write_text(str(child.pid))' % str(ready), 'time.sleep(20)',
            ])
            fail = '\n'.join(['import time', 'from pathlib import Path', 'ready = Path(%r)' % str(ready),
                              'deadline = time.monotonic() + 3',
                              'while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)',
                              'raise SystemExit(7)'])
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], start_new_session=True)
            try:
                code, plan = self.run_scripts(temporary, [fail, slow])
                self.assertEqual(code, 1)
                self.assertEqual(plan['status'], 'failed')
                self.assertIsNone(unrelated.poll())
                self.assertFalse(runner.group_alive(plan['cards'][1]['pid']))
                self.assertTrue(ready.is_file())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(ready.read_text()), 0)
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=3)

    def test_real_term_resistant_child_is_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / 'ready'
            resist = '\n'.join(['import signal, time', 'from pathlib import Path',
                'signal.signal(signal.SIGTERM, signal.SIG_IGN)', 'Path(%r).touch()' % str(ready), 'time.sleep(20)'])
            fail = '\n'.join(['import time', 'from pathlib import Path', 'ready = Path(%r)' % str(ready),
                'deadline = time.monotonic() + 3',
                'while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)', 'raise SystemExit(7)'])
            code, plan = self.run_scripts(temporary, [fail, resist])
            self.assertEqual(code, 1)
            self.assertTrue(plan['cards'][1]['kill_requested'])
            self.assertEqual(plan['cards'][1]['exit_code'], -signal.SIGKILL)
            self.assertFalse(runner.group_alive(plan['cards'][1]['pid']))


if __name__ == '__main__':
    unittest.main()
