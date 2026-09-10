import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock


VIEWER_PATH = Path(__file__).resolve().parents[1] / "viewer.py"
spec = importlib.util.spec_from_file_location("_hdmi_wall_viewer", VIEWER_PATH)
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


def fifo_stat(inode=10):
    return SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_dev=1, st_ino=inode)


class FakeReader:
    """Nonblocking byte stream: queued chunks/EOF/exceptions, then EAGAIN."""

    def __init__(self, *chunks):
        self.chunks = list(chunks)

    def __call__(self, _fd, buffers):
        if not self.chunks:
            raise BlockingIOError()
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        count = min(len(chunk), len(buffers[0]))
        buffers[0][:count] = chunk[:count]
        if count < len(chunk):
            self.chunks.insert(0, chunk[count:])
        return count


class ViewerTests(unittest.TestCase):
    def setUp(self):
        # Pure logic tests also run on Windows; actual FIFO integration stays Linux-only.
        nonblocking = patch.object(os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 2048), create=True)
        nonblocking.start()
        self.addCleanup(nonblocking.stop)

    def page(self, device=0):
        return viewer.DevicePage(device, Path("/preview.bgr"), Path("/worker"), 6)

    def test_readback_toggle_is_global_atomic_and_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / "readback-control.json"
            state = viewer.ViewerState([self.page(0), self.page(1)], control_path=control)
            for expected in (False, True, False):
                self.assertTrue(state.toggle_readback())
                self.assertIs(json.loads(control.read_text())["readback_enabled"], expected)
                self.assertIs(state.snapshot(0, 0)["readback_enabled_requested"], expected)
                self.assertFalse(control.with_name(control.name + ".tmp").exists())
            events = [json.loads(line) for line in control.with_name("readback-events.jsonl").read_text().splitlines()]
            self.assertEqual([e["readback_enabled"] for e in events], [False, True, False])
            self.assertTrue(all(e["scope"] == "all_selected_devices" for e in events))
            self.assertEqual(state.active.device, 0)
            state.close_requested = True
            self.assertFalse(state.toggle_readback())
            self.assertFalse(viewer.ViewerState([self.page()]).toggle_readback())

    def test_readback_control_requires_supported_manifest_and_boolean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "viewer.json"
            control = root / "readback-control.json"
            manifest = {"devices": [{"device": 0, "fifo": str(root / "preview.bgr"), "output": str(root)}]}
            path.write_text(json.dumps(manifest))
            self.assertIsNone(viewer.read_manifest(path)[0].control_path)
            manifest["readback_control"] = str(control)
            path.write_text(json.dumps(manifest))
            state = viewer.read_manifest(path)[0]
            self.assertEqual(state.control_path, control)
            control.write_text(json.dumps({"readback_enabled": False}))
            state.poll_manifest(path)
            self.assertFalse(state.readback_enabled)
            for invalid in (1, "true", None):
                control.write_text(json.dumps({"readback_enabled": invalid}))
                state.poll_manifest(path)
                self.assertFalse(state.readback_enabled)
            manifest["readback_control"] = str(root / "unexpected.json")
            path.write_text(json.dumps(manifest))
            self.assertIsNone(viewer.read_manifest(path)[0].control_path)

    def test_model_only_dashboard_hides_cached_video_and_shows_both_cards(self):
        pages = [self.page(0), self.page(1)]
        state = viewer.ViewerState(pages, control_path=Path("/control"))
        state.readback_enabled = False
        for page in pages:
            page.buffer.feed(b"abcdef", 10, 10000)
            page.live_status = {"timestamp_unix_ms": 10000, "readback_enabled": False,
                                "readback_inflight": 0, "total_decode_fps": 768,
                                "total_infer_fps": 360, "output_readback_bytes": 12,
                                "preview_readback_bytes": 34}
        display = viewer.SDLViewer.__new__(viewer.SDLViewer)
        display.lib, display.renderer = Mock(), None
        display.text, display.color, display.fill = Mock(), Mock(), Mock()
        display.lib.SDL_RenderClear.return_value = 0
        def output_size(_renderer, width, height):
            width._obj.value, height._obj.value = 1920, 1080
            return 0
        display.lib.SDL_GetRendererOutputSize.side_effect = output_size
        with patch.object(viewer.time, "time", return_value=10):
            display.render(state, 10)
        display.lib.SDL_UpdateTexture.assert_not_called()
        texts = [c.args[0] for c in display.text.call_args_list]
        self.assertTrue(any("ENCODE: N/A" in text for text in texts))
        self.assertEqual(sum("OUTPUT BYTES 12" in text for text in texts), 2)

    def test_arbitrary_chunk_boundaries_keep_only_latest_complete_frame(self):
        buffer = viewer.FrameBuffer(6)
        buffer.feed(b"abc", 0, 0)
        self.assertEqual(buffer.frames, 0)
        buffer.feed(b"defghijklmn", 1, 1000)
        self.assertEqual(buffer.frames, 2)
        self.assertEqual(buffer.latest, b"ghijkl")
        self.assertEqual(buffer.used, 2)
        self.assertEqual(buffer.age(3.25), 2.25)
        self.assertEqual(len(buffer.partial) + len(buffer.latest), 12)

    def test_replacing_many_frames_retains_two_fixed_buffers(self):
        buffer = viewer.FrameBuffer(6)
        identities = {id(buffer.partial), id(buffer.latest)}
        for i in range(10000):
            buffer.feed(bytes([i % 256]) * 6, i, i * 1000)
        self.assertEqual(buffer.frames, 10000)
        self.assertEqual(buffer.latest, bytes([9999 % 256]) * 6)
        self.assertEqual({id(buffer.partial), id(buffer.latest)}, identities)

    def test_eof_discards_partial_without_erasing_previous_frame(self):
        buffer = viewer.FrameBuffer(6)
        buffer.feed(b"ABCDEFbad", 1, 1000)
        buffer.end_writer()
        buffer.feed(b"GHIJKL", 3, 3000)
        self.assertEqual(buffer.frames, 2)
        self.assertEqual(buffer.latest, b"GHIJKL")
        self.assertEqual(buffer.used, 0)

    def test_nonselected_devices_continue_and_switch_uses_newest_frame(self):
        first, second = self.page(1), self.page(7)
        state = viewer.ViewerState([first, second])
        first.buffer.feed(b"111111", 1, 1000)
        for i in range(20):
            second.buffer.feed(bytes([i]) * 6, i + 1, (i + 1) * 1000)
        self.assertEqual(state.active.device, 1)
        self.assertTrue(state.select(1))
        self.assertEqual(state.active.device, 7)
        self.assertEqual(state.active.buffer.latest, bytes([19]) * 6)
        self.assertEqual(state.active.buffer.frames, 20)
        self.assertTrue(state.step(1))
        self.assertEqual([p.buffer.frames for p in state.pages], [1, 20])
        self.assertFalse(state.select(9))
        self.assertEqual(state.snapshot(21, 21000)["switches"], 2)

    def test_missing_first_frame_and_stale_retained_frame_are_distinct(self):
        page = self.page()
        self.assertEqual(page.state(100), "waiting")
        page.buffer.feed(b"abcdef", 0, 0)
        self.assertEqual(page.state(.5), "live")
        self.assertEqual(page.state(2.01), "stale")
        self.assertEqual(page.buffer.latest, b"abcdef")
        page.disconnected = True
        self.assertEqual(page.state(.5), "disconnected")
        page.summary = {"status": "measured", "error": ""}
        self.assertEqual(page.state(3), "ended")
        page.summary = {"status": "failed", "error": "decode failed"}
        self.assertEqual(page.state(3), "error")

    def test_missing_fifo_is_retried_after_creation(self):
        page = self.page()
        with patch.object(Path, "lstat", side_effect=[FileNotFoundError(), fifo_stat()]), \
                patch.object(os, "open", return_value=42) as opened, \
                patch.object(os, "fstat", return_value=fifo_stat()):
            page.open_fifo(0)
            self.assertIsNone(page.fd)
            self.assertFalse(page.error)
            page.open_fifo(.1)
            opened.assert_not_called()
            page.open_fifo(.21)
        self.assertEqual(page.fd, 42)

    def test_symlink_or_regular_file_is_never_opened(self):
        for mode in (stat.S_IFLNK, stat.S_IFREG):
            page = self.page()
            with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode)), \
                    patch.object(os, "open") as opened:
                page.open_fifo(0)
            opened.assert_not_called()
            self.assertIsNone(page.fd)
            self.assertEqual(page.state(0), "error")

    def test_changed_fifo_inode_closes_descriptor(self):
        page = self.page()
        with patch.object(Path, "lstat", return_value=fifo_stat(10)), \
                patch.object(os, "open", return_value=42), \
                patch.object(os, "fstat", return_value=fifo_stat(11)), \
                patch.object(os, "close") as closed:
            page.open_fifo(0)
        closed.assert_called_once_with(42)
        self.assertIsNone(page.fd)
        self.assertEqual(page.state(0), "error")

    def test_no_writer_yet_keeps_fifo_reader_open(self):
        page = self.page()
        page.fd = 42
        with patch.object(os, "readv", FakeReader(b""), create=True), patch.object(os, "close") as closed:
            page.drain(1, 1000)
        closed.assert_not_called()
        self.assertEqual(page.fd, 42)
        self.assertFalse(page.disconnected)

    def test_partial_read_eagain_and_fair_byte_budget(self):
        page = self.page()
        page.fd = 42
        reader = FakeReader(b"abcdefghi")
        with patch.object(os, "readv", reader, create=True):
            self.assertEqual(page.drain(1, 1000, budget=4), 4)
            self.assertEqual(page.buffer.used, 4)
            self.assertEqual(page.buffer.frames, 0)
            self.assertEqual(page.drain(2, 2000, budget=4), 4)
            self.assertEqual(page.buffer.latest, b"abcdef")
            self.assertEqual(page.buffer.used, 2)
            self.assertEqual(page.drain(3, 3000, budget=4), 1)
            self.assertEqual(page.buffer.used, 3)

    def test_writer_eof_reopen_preserves_count_and_resets_partial(self):
        page = self.page()
        page.fd = 42
        with patch.object(os, "readv", FakeReader(b"ABCDEFbad", b""), create=True), \
                patch.object(os, "close") as closed:
            self.assertEqual(page.drain(1, 1000), 9)
        closed.assert_called_once_with(42)
        self.assertIsNone(page.fd)
        self.assertTrue(page.disconnected)
        self.assertEqual(page.buffer.used, 0)
        with patch.object(Path, "lstat", return_value=fifo_stat()), \
                patch.object(os, "open", return_value=43), \
                patch.object(os, "fstat", return_value=fifo_stat()), \
                patch.object(os, "readv", FakeReader(b"GHIJKL"), create=True):
            self.assertEqual(page.drain(1.5, 1500), 6)
        self.assertFalse(page.disconnected)
        self.assertEqual(page.buffer.frames, 2)
        self.assertEqual(page.buffer.latest, b"GHIJKL")

    def test_launcher_reports_failure_even_when_no_fifo_or_summary_exists(self):
        page = self.page(7)
        state = viewer.ViewerState([page])
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "viewer.json"
            manifest.write_text(json.dumps({"devices": [{"device": 7, "status": "failed", "returncode": 2}]}))
            state.poll_manifest(manifest)
            self.assertEqual(page.state(5), "error")
            self.assertEqual(state.snapshot(5, 5000)["devices"][0]["returncode"], 2)
            # Switching cannot reset a failed page; only a launcher update can.
            state.step(1)
            self.assertEqual(page.state(10), "error")
            manifest.write_text(json.dumps({"devices": [{"device": 7, "status": "stopped", "returncode": 2}]}))
            state.poll_manifest(manifest)
            self.assertEqual(page.state(11), "ended")

    def test_partial_summary_retries_then_marks_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            page = viewer.DevicePage(0, Path(temp) / "preview.bgr", Path(temp), 6)
            summary = Path(temp) / "summary.json"
            summary.write_text('{"status":')
            page.poll_summary()
            self.assertIsNone(page.summary)
            summary.write_text('{"status":"measured","error":""}')
            page.poll_summary()
            self.assertEqual(page.state(100), "ended")

    def test_click_coordinates_follow_resize_and_letterbox(self):
        for width, height in ((1920, 1080), (1280, 720), (1024, 768), (2560, 1080)):
            scale = min(width / 1920, height / 1080)
            for index, (x, y, _, _) in enumerate(viewer.button_rects(4)):
                real_x = (width - 1920 * scale) / 2 + (x + 10) * scale
                real_y = (height - 1080 * scale) / 2 + (y + 10) * scale
                logical = viewer.to_logical(real_x, real_y, width, height)
                self.assertEqual(viewer.hit_button(4, *logical), index)
        self.assertIsNone(viewer.hit_button(4, 280, 100))
        self.assertIsNone(viewer.hit_button(1, 800, 30))

    def test_manifest_unique_ids_paths_and_four_device_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "viewer.json"
            devices = [{"device": i, "fifo": str(Path(temp) / str(i) / "preview.bgr"), "output": str(Path(temp) / str(i))}
                       for i in (0, 2, 5, 7)]
            path.write_text(json.dumps({"devices": devices}))
            state, width, height, fps = viewer.read_manifest(path)
            self.assertEqual([p.device for p in state.pages], [0, 2, 5, 7])
            self.assertEqual((width, height, fps), (1920, 1080, 10))
            for invalid in (devices + [devices[0]], [devices[0], devices[0]], []):
                path.write_text(json.dumps({"devices": invalid}))
                with self.assertRaises(ValueError):
                    viewer.read_manifest(path)

    def test_atomic_status_preserves_all_page_counters(self):
        state = viewer.ViewerState([self.page(0), self.page(9)])
        state.pages[1].buffer.feed(b"abcdefghijklm", 12, 12000)
        state.select(1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "viewer-status.json"
            viewer.write_status(path, state.snapshot(14, 14000, running=False))
            status = json.loads(path.read_text())
            self.assertFalse(status["running"])
            self.assertEqual(status["active_device"], 9)
            self.assertEqual(status["devices"][1]["frames_received"], 2)
            self.assertEqual(status["devices"][1]["partial_bytes"], 1)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_manifest_hardware_metadata_survives_switch_without_id_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "viewer.json"
            devices = [{"device": device, "fifo": str(Path(temp) / str(device) / "preview.bgr"),
                        "output": str(Path(temp) / str(device)), "streams": streams, "pcie_link_label": label}
                       for device, streams, label in ((7, 20, "PCIe 2.0 x1"), (0, 30, "PCIe 3.0 x2"),
                                                       (1, 32, None))]
            path.write_text(json.dumps({"devices": devices}))
            state, *_ = viewer.read_manifest(path)
            self.assertEqual([page.hardware_label() for page in state.pages],
                             ["PCIe 2.0 x1 | 20 CH", "PCIe 3.0 x2 | 30 CH", ""])
            state.select(1)
            snapshot = state.snapshot(0, 0)
            self.assertEqual(snapshot["active_device"], 0)
            self.assertEqual([(page["device"], page["streams"], page["pcie_link_label"]) for page in snapshot["devices"]],
                             [(7, 20, "PCIe 2.0 x1"), (0, 30, "PCIe 3.0 x2"), (1, 32, None)])
            devices[0].update(streams=True, pcie_link_label="PCIe guessed\n3.0")
            path.write_text(json.dumps({"devices": devices}))
            state, *_ = viewer.read_manifest(path)
            self.assertIsNone(state.pages[0].streams)
            self.assertEqual(state.pages[0].hardware_label(), "")

    def test_four_button_hardware_and_status_text_stays_inside_buttons(self):
        self.check_four_button_labels()

    def test_tpu_indicator_expires_and_never_turns_missing_data_into_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = viewer.DevicePage(1, root/'fifo', root/'device_1', 6)
            path = root/'telemetry-live.json'
            path.write_text(json.dumps({'interval_seconds': 5, 'devices': {'1': {
                'tpu_util_percent': 98, 'monotonic_s': 100, 'error': None}}}))
            page.poll_summary()
            self.assertEqual(page.tpu_label(102), 'TPU 98%')
            self.assertTrue(all(char in viewer.FONT for char in page.tpu_label(102)))
            self.assertEqual(page.tpu_label(113), 'TPU --')
            for invalid in [None, True, -1, 101, float('nan')]:
                page.telemetry = {'tpu_util_percent': invalid, 'monotonic_s': 100, 'error': None}
                self.assertEqual(page.tpu_label(102), 'TPU --')
            path.write_text('{')
            page.poll_summary()
            self.assertEqual(page.tpu_label(102), 'TPU --')

    def check_four_button_labels(self):
        pages = [viewer.DevicePage(device, Path("/preview"), Path("/worker"), 6, 32, label)
                 for device, label in ((7, "PCIe 2.0 x1"), (0, "PCIe 3.0 x2"),
                                       (4, "PCIe 128 GT/s x32"), (1, None))]
        state = viewer.ViewerState(pages)
        display = viewer.SDLViewer.__new__(viewer.SDLViewer)
        display.lib = Mock()
        display.renderer = None
        display.text, display.color, display.fill = Mock(), Mock(), Mock()
        display.lib.SDL_RenderClear.return_value = 0

        def output_size(_renderer, width, height):
            width._obj.value, height._obj.value = 1920, 1080
            return 0

        display.lib.SDL_GetRendererOutputSize.side_effect = output_size
        display.render(state, 0)
        for index, (page, box) in enumerate(zip(pages, viewer.button_rects(4))):
            expected = ["DEVICE " + str(page.device), str(index + 1) + " | waiting | TPU --"]
            if page.hardware_label():
                expected.append(page.hardware_label())
            for label in expected:
                matching = [call.args for call in display.text.call_args_list
                            if call.args[0] == label and call.args[1] == box[0] + 18]
                self.assertEqual(len(matching), 1, label)
                _, x, y, size, *_ = matching[0]
                self.assertGreaterEqual(y, box[1])
                self.assertLessEqual(y + 7 * size, box[1] + box[3])
                self.assertLessEqual(x + len(label) * 6 * size, box[0] + box[2])
            if not page.hardware_label():
                display.text.assert_any_call("DEVICE 1", box[0] + 18, 24, 3)
                label = "4 | waiting | TPU --"
                display.text.assert_any_call(label, box[0] + 18, 57,
                                             min(2, (box[2] - 36) / (6 * len(label))), (249, 189, 104))
        # Page-reception freshness still has its explicit VIEW LIVE wording.
        pages[0].buffer.latest_monotonic = 0
        display.text.reset_mock()
        display.render(state, .1)
        self.assertTrue(any(call.args[0] == "1 | VIEW LIVE | TPU --" for call in display.text.call_args_list))

    def test_supervised_close_is_idempotent_locks_switch_and_has_deadline(self):
        state = viewer.ViewerState([self.page(0), self.page(7)], supervised_close=True)
        self.assertFalse(state.request_close(0))
        self.assertTrue(state.close_requested)
        self.assertFalse(state.select(1))
        self.assertFalse(state.step(1))
        self.assertFalse(state.request_close(44))
        self.assertEqual(state.close_requested_at, 0)
        state.check_close_timeout(44.99)
        with self.assertRaisesRegex(RuntimeError, "45 seconds"):
            state.check_close_timeout(45)
        self.assertTrue(state.snapshot(45, 45000)["failure"])

    def run_close_loop(self, supervised, timeout=False):
        pages = [self.page(0), self.page(7)]
        state = viewer.ViewerState(pages, supervised_close=supervised)
        for page in pages:
            page.fd = 42 + page.device
            page.drain = Mock(side_effect=lambda now, unix_ms, page=page: (
                page.buffer.feed(b"abcdef", now, unix_ms) or 6))
            page.poll_summary = Mock()
        display, handlers, snapshots = Mock(), {}, []
        event_calls = []

        def events(current):
            event_calls.append(1)
            # The initial Esc is the only close action. Every subsequent loop
            # must still consume both device FIFOs until the parent sends TERM.
            if len(event_calls) == 1:
                return True, False
            self.assertTrue(current.close_requested)
            self.assertTrue(all(page.fd is not None for page in pages))
            self.assertFalse(current.select(1))
            if not timeout and len(event_calls) == 3:
                handlers[viewer.signal.SIGTERM](viewer.signal.SIGTERM, None)
            return False, False

        display.events.side_effect = events
        clock = iter([index * (46 if timeout else .1) for index in range(30)])
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(viewer, "read_manifest", return_value=(state, 320, 240, 10)), \
                patch.object(viewer, "SDLViewer", return_value=display), \
                patch.object(viewer.signal, "signal", side_effect=lambda number, handler: handlers.update({number: handler})), \
                patch.object(viewer.time, "monotonic", side_effect=lambda: next(clock)), \
                patch.object(viewer, "write_status", side_effect=lambda path, value: snapshots.append(value)), \
                patch.object(os, "close") as closed:
            result = viewer.main(["--manifest", str(Path(temp) / "viewer.json")])
        display.close.assert_called_once()
        self.assertEqual(closed.call_count, 2)
        self.assertTrue(all(page.fd is None for page in pages))
        return result, state, display, snapshots

    def test_supervised_escape_drains_and_renders_until_parent_termination(self):
        result, state, display, snapshots = self.run_close_loop(supervised=True)
        self.assertEqual(result, 0)
        self.assertEqual([page.buffer.frames for page in state.pages], [3, 3])
        self.assertGreaterEqual(display.render.call_count, 2)
        self.assertTrue(snapshots[0]["running"])
        self.assertTrue(snapshots[0]["close_requested"])
        self.assertEqual(snapshots[0]["pid"], os.getpid())
        self.assertFalse(snapshots[-1]["running"])
        self.assertEqual(snapshots[-1]["exit_code"], 0)
        self.assertFalse(snapshots[-1]["failure"])

    def test_standalone_escape_exits_immediately(self):
        result, state, display, snapshots = self.run_close_loop(supervised=False)
        self.assertEqual(result, 0)
        self.assertEqual([page.buffer.frames for page in state.pages], [1, 1])
        display.render.assert_not_called()
        self.assertFalse(snapshots[-1]["running"])
        self.assertFalse(snapshots[-1]["close_requested"])

    def test_unresponsive_supervisor_exits_with_recorded_failure(self):
        result, state, _display, snapshots = self.run_close_loop(supervised=True, timeout=True)
        self.assertEqual(result, 1)
        self.assertEqual([page.buffer.frames for page in state.pages], [2, 2])
        self.assertFalse(snapshots[-1]["running"])
        self.assertTrue(snapshots[-1]["close_requested"])
        self.assertIn("45 seconds", snapshots[-1]["failure"])

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "readv"), "Linux FIFO integration")
    def test_real_fifo_writer_restart_does_not_splice_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / "preview.bgr"
            os.mkfifo(fifo, 0o600)
            page = viewer.DevicePage(3, fifo, Path(temp), 6)
            try:
                page.drain(0, 0)
                writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                try:
                    os.write(writer, b"abcdefbad")
                    page.drain(.1, 100)
                finally:
                    os.close(writer)
                page.drain(.2, 200)
                self.assertEqual(page.buffer.used, 0)
                self.assertEqual(page.buffer.latest, b"abcdef")
                page.drain(.5, 500)
                writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                try:
                    os.write(writer, b"ghijkl")
                    page.drain(.6, 600)
                    self.assertEqual(page.buffer.latest, b"ghijkl")
                    self.assertEqual(page.buffer.frames, 2)
                finally:
                    os.close(writer)
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
