#!/usr/bin/env python3
"""Display one live HDMI wall per device while consuming every device's preview."""

from __future__ import annotations

import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time


STALE_SECONDS = 2.0
READ_BUDGET = 1024 * 1024  # Per device and event-loop turn; no device monopolizes reads.
CLOSE_TIMEOUT = 45.0


class FrameBuffer:
    """Two fixed-size buffers: one partial frame and one replaceable complete frame."""

    def __init__(self, frame_bytes: int):
        self.frame_bytes = frame_bytes
        self.partial = bytearray(frame_bytes)
        self.latest = bytearray(frame_bytes)
        self.used = 0
        self.frames = 0
        self.latest_monotonic = None
        self.latest_unix_ms = None

    def commit_bytes(self, count: int, now: float, unix_ms: int):
        if count < 0 or self.used + count > self.frame_bytes:
            raise ValueError("Invalid frame byte count")
        self.used += count
        if self.used == self.frame_bytes:
            self.partial, self.latest = self.latest, self.partial
            self.used = 0
            self.frames += 1
            self.latest_monotonic = now
            self.latest_unix_ms = unix_ms

    def feed(self, data: bytes, now: float, unix_ms: int):
        """Byte-stream adapter used by tests; production reads directly into partial."""
        offset = 0
        while offset < len(data):
            count = min(len(data) - offset, self.frame_bytes - self.used)
            self.partial[self.used:self.used + count] = data[offset:offset + count]
            self.commit_bytes(count, now, unix_ms)
            offset += count

    def end_writer(self):
        # A new writer starts at a frame boundary. Never splice across writers.
        self.used = 0

    def age(self, now: float):
        return None if self.latest_monotonic is None else max(0.0, now - self.latest_monotonic)


class DevicePage:
    def __init__(self, device: int, fifo: Path, output: Path, frame_bytes: int,
                 streams: int | None = None, pcie_link_label: str | None = None):
        self.device, self.fifo, self.output = device, fifo, output
        self.streams, self.pcie_link_label = streams, pcie_link_label
        self.buffer = FrameBuffer(frame_bytes)
        self.fd = None
        self.writer_seen = False
        self.disconnected = False
        self.next_open = 0.0
        self.error = ""
        self.summary = None
        self.launcher_status = "starting"
        self.returncode = None
        self.telemetry = None
        self.telemetry_max_age = 10.0

    def tpu_label(self, now: float) -> str:
        row = self.telemetry
        if not isinstance(row, dict) or row.get("error") is not None:
            return "TPU --"
        value, sampled = row.get("tpu_util_percent"), row.get("monotonic_s")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (value, sampled)):
            return "TPU --"
        if not 0 <= value <= 100 or not 0 <= now - sampled <= self.telemetry_max_age:
            return "TPU --"
        return f"TPU {value:.0f}%"

    def hardware_label(self) -> str:
        if self.pcie_link_label is None:
            return ""  # Older or ordinary manifests keep the existing two lines.
        return self.pcie_link_label + (f" | {self.streams} CH" if self.streams is not None else "")

    def open_fifo(self, now: float):
        if self.fd is not None or self.error or now < self.next_open:
            return
        self.next_open = now + .2
        descriptor = None
        try:
            before = self.fifo.lstat()
            if not stat.S_ISFIFO(before.st_mode):
                raise ValueError("Preview path is not a FIFO")
            descriptor = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK |
                                 getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
            after = os.fstat(descriptor)
            if not stat.S_ISFIFO(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("Preview FIFO changed while opening")
            self.fd, descriptor = descriptor, None
            self.writer_seen = False
        except FileNotFoundError:
            pass  # Viewer normally starts before the worker creates its FIFO.
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def drain(self, now: float, unix_ms: int, budget: int = READ_BUDGET) -> int:
        self.open_fifo(now)
        if self.fd is None:
            return 0
        total = 0
        try:
            while total < budget:
                count = min(self.buffer.frame_bytes - self.buffer.used, budget - total)
                target = memoryview(self.buffer.partial)[self.buffer.used:self.buffer.used + count]
                try:
                    received = os.readv(self.fd, [target])
                finally:
                    target.release()
                if received == 0:
                    # Before a writer connects, retain this reader so open(O_WRONLY)
                    # can succeed. Once a writer closes, reset its partial frame.
                    if self.writer_seen:
                        self.disconnected = True
                        self.buffer.end_writer()
                        self.close()
                        self.next_open = now + .2
                    break
                self.writer_seen = True
                self.disconnected = False
                self.buffer.commit_bytes(received, now, unix_ms)
                total += received
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            self.error = str(exc)
            self.close()
        return total

    def poll_summary(self):
        try:
            live = json.loads((self.output.parent / "telemetry-live.json").read_text(encoding="utf-8"))
            interval = live["interval_seconds"]
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 0:
                raise ValueError("Invalid telemetry interval")
            self.telemetry = live["devices"].get(str(self.device))
            self.telemetry_max_age = max(10.0, interval * 2 + 2)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.telemetry = None
        if self.summary is not None:
            return
        try:
            # Written only when the worker finishes. Retry a partial JSON write.
            with (self.output / "summary.json").open(encoding="utf-8") as stream:
                summary = json.load(stream)
            if isinstance(summary, dict) and isinstance(summary.get("status"), str):
                self.summary = summary
        except (OSError, ValueError):
            pass

    def state(self, now: float) -> str:
        if self.error or self.launcher_status in ("failed", "not_started"):
            return "error"
        if self.summary:
            return "error" if self.summary.get("error") or self.summary["status"] in (
                "failed", "incomplete_records", "incomplete_accounting", "incomplete_duration") else "ended"
        if self.launcher_status in ("completed", "stopped"):
            return "ended"
        if self.disconnected:
            return "disconnected"
        age = self.buffer.age(now)
        if age is None:
            return "waiting"
        return "stale" if age > STALE_SECONDS else "live"

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class ViewerState:
    def __init__(self, pages: list[DevicePage], supervised_close: bool = False):
        self.pages = pages
        self.active_index = 0
        self.switches = 0
        self.supervised_close = supervised_close
        self.close_requested = False
        self.close_requested_at = None
        self.failure = ""

    @property
    def active(self) -> DevicePage:
        return self.pages[self.active_index]

    def select(self, index: int) -> bool:
        if not self.close_requested and 0 <= index < len(self.pages) and index != self.active_index:
            self.active_index = index
            self.switches += 1
            return True
        return False

    def step(self, delta: int) -> bool:
        return self.select((self.active_index + delta) % len(self.pages))

    def request_close(self, now: float) -> bool:
        """Return whether to exit now; supervised workers must drain before us."""
        if not self.supervised_close:
            return True
        if not self.close_requested:
            self.close_requested = True
            self.close_requested_at = now
        return False

    def check_close_timeout(self, now: float):
        if self.close_requested_at is not None and now - self.close_requested_at >= CLOSE_TIMEOUT:
            self.failure = "Supervisor did not finish device shutdown within 45 seconds"
            raise RuntimeError(self.failure)

    def snapshot(self, now: float, unix_ms: int, running: bool = True) -> dict:
        return {
            "running": running, "pid": os.getpid(), "active_device": self.active.device,
            "switches": self.switches, "updated_unix_ms": unix_ms,
            "supervised_close": self.supervised_close, "close_requested": self.close_requested,
            "failure": self.failure,
            "devices": [{
                "device": page.device, "streams": page.streams, "pcie_link_label": page.pcie_link_label,
                "frames_received": page.buffer.frames,
                "latest_received_unix_ms": page.buffer.latest_unix_ms,
                "view_age_ms": None if page.buffer.age(now) is None else round(page.buffer.age(now) * 1000, 3),
                "partial_bytes": page.buffer.used, "state": page.state(now),
                "worker_status": None if page.summary is None else page.summary["status"],
                "launcher_status": page.launcher_status, "returncode": page.returncode,
                "error": page.error or (page.summary or {}).get("error", ""),
            } for page in self.pages],
            "note": "View age measures received wall updates, not camera or inference latency.",
        }

    def poll_manifest(self, path: Path):
        try:
            with path.open(encoding="utf-8") as stream:
                manifest = json.load(stream)
            updates = {item["device"]: item for item in manifest["devices"]}
            for page in self.pages:
                update = updates.get(page.device, {})
                status = update.get("status")
                if status in ("starting", "running", "completed", "failed", "stopped", "not_started"):
                    page.launcher_status = status
                    page.returncode = update.get("returncode")
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A launcher may still be replacing a manifest during shutdown.


def read_manifest(path: Path):
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    width, height, fps = manifest.get("width", 1920), manifest.get("height", 1080), manifest.get("fps", 10)
    if (type(width) is not int or type(height) is not int or
            not 320 <= width <= 4096 or not 240 <= height <= 2160):
        raise ValueError("Invalid preview dimensions")
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 60:
        raise ValueError("Invalid preview FPS")
    devices = manifest.get("devices")
    if not isinstance(devices, list) or not 1 <= len(devices) <= 4:
        raise ValueError("Viewer requires one to four devices")
    pages, ids, fifos = [], set(), set()
    for item in devices:
        device, fifo, output = item["device"], Path(item["fifo"]), Path(item["output"])
        if type(device) is not int or device < 0 or device in ids:
            raise ValueError("Device identifiers must be unique nonnegative integers")
        if not fifo.is_absolute() or not output.is_absolute() or str(fifo) in fifos:
            raise ValueError("Preview paths must be absolute and FIFOs must be unique")
        ids.add(device)
        fifos.add(str(fifo))
        streams, label = item.get("streams"), item.get("pcie_link_label")
        if type(streams) is not int or not 1 <= streams <= 32:
            streams = None
        if (not isinstance(label, str) or len(label) > 30
                or not re.fullmatch(r"PCIe [0-9]+(?:\.[0-9]+)?(?: GT/s)? x[1-9][0-9]*", label)):
            label = None
        pages.append(DevicePage(device, fifo, output, width * height * 3, streams, label))
    return ViewerState(pages, supervised_close=manifest.get("supervised_close") is True), width, height, float(fps)


def write_status(path: Path, value: dict):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


# SDL2 is already installed with the system ffplay. Avoid importing vendor
# OpenCV/FFmpeg libraries into the viewer process, or installing Python GUI deps.
class Rect(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "w", "h")]


class KeySym(C.Structure):
    _fields_ = [("scancode", C.c_int32), ("sym", C.c_int32), ("mod", C.c_uint16), ("unused", C.c_uint32)]


class KeyEvent(C.Structure):
    _fields_ = [("type", C.c_uint32), ("timestamp", C.c_uint32), ("windowID", C.c_uint32),
                ("state", C.c_uint8), ("repeat", C.c_uint8), ("padding2", C.c_uint8),
                ("padding3", C.c_uint8), ("keysym", KeySym)]


class MouseEvent(C.Structure):
    _fields_ = [("type", C.c_uint32), ("timestamp", C.c_uint32), ("windowID", C.c_uint32),
                ("which", C.c_uint32), ("button", C.c_uint8), ("state", C.c_uint8),
                ("clicks", C.c_uint8), ("padding", C.c_uint8), ("x", C.c_int32), ("y", C.c_int32)]


class TouchEvent(C.Structure):
    _fields_ = [("type", C.c_uint32), ("timestamp", C.c_uint32), ("touchId", C.c_int64),
                ("fingerId", C.c_int64), ("x", C.c_float), ("y", C.c_float),
                ("dx", C.c_float), ("dy", C.c_float), ("pressure", C.c_float), ("windowID", C.c_uint32)]


class Event(C.Union):
    _fields_ = [("type", C.c_uint32), ("key", KeyEvent), ("button", MouseEvent),
                ("touch", TouchEvent), ("padding", C.c_uint8 * 56)]


# Five-by-seven uppercase glyphs, drawn as batched rectangles without SDL_ttf.
FONT = {
    "A": [14,17,17,31,17,17,17], "B": [30,17,17,30,17,17,30], "C": [14,17,16,16,16,17,14],
    "D": [30,17,17,17,17,17,30], "E": [31,16,16,30,16,16,31], "F": [31,16,16,30,16,16,16],
    "G": [14,17,16,23,17,17,15], "H": [17,17,17,31,17,17,17], "I": [31,4,4,4,4,4,31],
    "J": [7,2,2,2,18,18,12], "K": [17,18,20,24,20,18,17], "L": [16,16,16,16,16,16,31],
    "M": [17,27,21,21,17,17,17], "N": [17,25,21,19,17,17,17], "O": [14,17,17,17,17,17,14],
    "P": [30,17,17,30,16,16,16], "Q": [14,17,17,17,21,18,13], "R": [30,17,17,30,20,18,17],
    "S": [15,16,16,14,1,1,30], "T": [31,4,4,4,4,4,4], "U": [17,17,17,17,17,17,14],
    "V": [17,17,17,17,17,10,4], "W": [17,17,17,21,21,21,10], "X": [17,17,10,4,10,17,17],
    "Y": [17,17,10,4,4,4,4], "Z": [31,1,2,4,8,16,31],
    "0": [14,17,19,21,25,17,14], "1": [4,12,4,4,4,4,14], "2": [14,17,1,2,4,8,31],
    "3": [30,1,1,14,1,1,30], "4": [2,6,10,18,31,2,2], "5": [31,16,16,30,1,1,30],
    "6": [14,16,16,30,17,17,14], "7": [31,1,2,4,8,8,8], "8": [14,17,17,14,17,17,14],
    "9": [14,17,17,15,1,1,14], "-": [0,0,0,31,0,0,0], ":": [0,4,4,0,4,4,0],
    ".": [0,0,0,0,0,4,4], "/": [1,1,2,4,8,16,16], "?": [14,17,1,2,4,0,4],
    " ": [0,0,0,0,0,0,0], "|": [4,4,4,4,4,4,4],
    "%": [25,26,2,4,8,11,19],
}


def button_rects(count: int):
    width = min(390, 1620 / count)
    return [(280 + index * width, 12, width - 12, 72) for index in range(count)]


def hit_button(count: int, x: float, y: float):
    for index, (left, top, width, height) in enumerate(button_rects(count)):
        if left <= x < left + width and top <= y < top + height:
            return index
    return None


def to_logical(x: float, y: float, width: int, height: int):
    scale = min(width / 1920, height / 1080)
    return ((x - (width - 1920 * scale) / 2) / scale,
            (y - (height - 1080 * scale) / 2) / scale)


class SDLViewer:
    def __init__(self, width: int, height: int):
        self.lib = C.CDLL("libSDL2-2.0.so.0")
        self.window = self.renderer = self.texture = None
        self.uploaded = None
        self.width, self.height = width, height
        pointer, integer, rect = C.c_void_p, C.c_int, C.POINTER(Rect)
        signatures = {
            "SDL_Init": (integer, [C.c_uint32]), "SDL_Quit": (None, []),
            "SDL_GetError": (C.c_char_p, []), "SDL_CreateWindow": (pointer, [C.c_char_p, integer, integer, integer, integer, C.c_uint32]),
            "SDL_DestroyWindow": (None, [pointer]), "SDL_CreateRenderer": (pointer, [pointer, integer, C.c_uint32]),
            "SDL_DestroyRenderer": (None, [pointer]), "SDL_CreateTexture": (pointer, [pointer, C.c_uint32, integer, integer, integer]),
            "SDL_DestroyTexture": (None, [pointer]), "SDL_UpdateTexture": (integer, [pointer, rect, pointer, integer]),
            "SDL_SetRenderDrawColor": (integer, [pointer, C.c_uint8, C.c_uint8, C.c_uint8, C.c_uint8]),
            "SDL_RenderClear": (integer, [pointer]), "SDL_RenderFillRect": (integer, [pointer, rect]),
            "SDL_RenderFillRects": (integer, [pointer, rect, integer]), "SDL_RenderCopy": (integer, [pointer, pointer, rect, rect]),
            "SDL_RenderPresent": (None, [pointer]), "SDL_PollEvent": (integer, [C.POINTER(Event)]),
            "SDL_GetRendererOutputSize": (integer, [pointer, C.POINTER(integer), C.POINTER(integer)]),
            "SDL_GetWindowSize": (None, [pointer, C.POINTER(integer), C.POINTER(integer)]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = restype, argtypes
        try:
            self.check(self.lib.SDL_Init(0x20 | 0x4000))
            self.window = self.lib.SDL_CreateWindow(b"BM1684X - Device Pages", 0x2FFF0000, 0x2FFF0000,
                                                     1920, 1080, 0x1001 | 4 | 32)
            if not self.window:
                self.fail()
            self.renderer = self.lib.SDL_CreateRenderer(self.window, -1, 1)
            if not self.renderer:
                self.fail()
            self.texture = self.lib.SDL_CreateTexture(self.renderer, 0x17401803, 1, width, height)  # BGR24, streaming
            if not self.texture:
                self.fail()
        except BaseException:
            self.close()
            raise

    def fail(self):
        raise RuntimeError(self.lib.SDL_GetError().decode("utf-8", "replace"))

    def check(self, result):
        if result < 0:
            self.fail()

    def events(self, state: ViewerState):
        event, quit_requested, changed = Event(), False, False
        while self.lib.SDL_PollEvent(C.byref(event)):
            if event.type == 0x100:
                return True, changed
            elif event.type == 0x300 and not event.key.repeat:
                key = event.key.keysym.sym
                if key == 27:
                    return True, changed
                elif ord("1") <= key <= ord("4"):
                    changed = state.select(key - ord("1")) or changed
                elif key in (1073741903, 1073741904):
                    changed = state.step(1 if key == 1073741903 else -1) or changed
            elif event.type in (0x402, 0x701):
                width, height = C.c_int(), C.c_int()
                self.lib.SDL_GetWindowSize(self.window, C.byref(width), C.byref(height))
                if width.value <= 0 or height.value <= 0:
                    continue
                if event.type == 0x402:
                    if event.button.button != 1:
                        continue
                    x, y = event.button.x, event.button.y
                else:
                    x, y = event.touch.x * width.value, event.touch.y * height.value
                x, y = to_logical(x, y, width.value, height.value)
                index = hit_button(len(state.pages), x, y)
                if index is not None:
                    changed = state.select(index) or changed
        return quit_requested, changed

    def color(self, rgb):
        self.lib.SDL_SetRenderDrawColor(self.renderer, *rgb, 255)

    def rectangle(self, box):
        x, y, width, height = box
        return Rect(round(self.offset_x + x * self.scale), round(self.offset_y + y * self.scale),
                    max(1, round(width * self.scale)), max(1, round(height * self.scale)))

    def fill(self, box, rgb):
        self.color(rgb)
        self.lib.SDL_RenderFillRect(self.renderer, C.byref(self.rectangle(box)))

    def text(self, value, x, y, size=2, rgb=(222, 230, 240)):
        rectangles = []
        for char in str(value).upper():
            for row, bits in enumerate(FONT.get(char, FONT["?"])):
                for column in range(5):
                    if bits & (1 << (4 - column)):
                        rectangles.append(self.rectangle((x + column * size, y + row * size, size, size)))
            x += 6 * size
        if rectangles:
            self.color(rgb)
            batch = (Rect * len(rectangles))(*rectangles)
            self.lib.SDL_RenderFillRects(self.renderer, batch, len(batch))

    def render(self, state: ViewerState, now: float):
        width, height = C.c_int(), C.c_int()
        self.check(self.lib.SDL_GetRendererOutputSize(self.renderer, C.byref(width), C.byref(height)))
        if width.value <= 0 or height.value <= 0:
            return
        self.scale = min(width.value / 1920, height.value / 1080)
        self.offset_x, self.offset_y = (width.value - 1920 * self.scale) / 2, (height.value - 1080 * self.scale) / 2
        self.color((7, 11, 18))
        self.check(self.lib.SDL_RenderClear(self.renderer))
        self.fill((0, 0, 1920, 96), (18, 27, 40))
        self.text("HDMI WALL", 24, 24, 3)
        self.text("DEVICE PAGES", 24, 57, 2, (129, 151, 178))
        for index, (page, box) in enumerate(zip(state.pages, button_rects(len(state.pages)))):
            self.fill(box, (29, 83, 125) if index == state.active_index else (34, 45, 60))
            hardware = page.hardware_label()
            self.text("DEVICE " + str(page.device), box[0] + 18, 19 if hardware else 24, 3)
            if hardware:
                # Fit all four buttons without clipping long but valid observed
                # link labels; the status row remains visible during failures.
                size = min(2, (box[2] - 36) / (6 * len(hardware)))
                self.text(hardware, box[0] + 18, 45, size, (190, 210, 231))
            page_state = page.state(now)
            state_label = "VIEW LIVE" if page_state == "live" else page_state
            state_text = str(index + 1) + " | " + state_label + " | " + page.tpu_label(now)
            state_size = min(2, (box[2] - 36) / (6 * len(state_text)))
            self.text(state_text, box[0] + 18, 66 if hardware else 57, state_size,
                      (121, 225, 178) if page_state == "live" else (249, 189, 104))
        page = state.active
        if page.buffer.frames:
            version = (page.device, page.buffer.frames)
            if version != self.uploaded:
                pixels = (C.c_uint8 * len(page.buffer.latest)).from_buffer(page.buffer.latest)
                self.check(self.lib.SDL_UpdateTexture(self.texture, None, pixels, self.width * 3))
                self.uploaded = version
            ratio = min(1920 / self.width, 942 / self.height)
            destination = self.rectangle(((1920 - self.width * ratio) / 2, 100 + (942 - self.height * ratio) / 2,
                                          self.width * ratio, self.height * ratio))
            self.check(self.lib.SDL_RenderCopy(self.renderer, self.texture, None, C.byref(destination)))
        status = page.state(now)
        if status != "live" or state.close_requested:
            messages = {"waiting": "WAITING FOR DEVICE", "stale": "STALE VIEW", "disconnected": "DEVICE DISCONNECTED",
                        "ended": "DEVICE FINISHED", "error": "DEVICE ERROR - CHECK LOGS"}
            message = "STOPPING DEVICES" if state.close_requested else messages[status]
            self.fill((450, 484, 1020, 105), (24, 32, 46))
            self.text(message, (1920 - len(message) * 24) / 2, 504, 4, (255, 194, 110))
            self.text("DEVICE " + str(page.device), 848, 553, 2)
        self.fill((0, 1044, 1920, 36), (18, 27, 40))
        self.text("1-4 / LEFT-RIGHT / CLICK: SWITCH DEVICE    ESC: CLOSE", 24, 1055, 2)
        age = page.buffer.age(now)
        self.text("VIEW UPDATE: " + ("--" if age is None else f"{age:.1f}S"), 1510, 1055, 2)
        self.lib.SDL_RenderPresent(self.renderer)

    def close(self):
        for name, function in (("texture", "SDL_DestroyTexture"), ("renderer", "SDL_DestroyRenderer"), ("window", "SDL_DestroyWindow")):
            value = getattr(self, name, None)
            if value:
                getattr(self.lib, function)(value)
                setattr(self, name, None)
        self.lib.SDL_Quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    state = display = None
    stopped = False
    exit_code = 0
    status_path = args.manifest.parent / "viewer-status.json"

    def stop(_number, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        state, width, height, fps = read_manifest(args.manifest)
        display = SDLViewer(width, height)
        next_render = next_status = 0.0
        turn = 0
        while not stopped:
            now, unix_ms = time.monotonic(), time.time_ns() // 1_000_000
            # Start each turn at a different device for fair consumption under load.
            received = 0
            for offset in range(len(state.pages)):
                received += state.pages[(turn + offset) % len(state.pages)].drain(now, unix_ms)
            turn = (turn + 1) % len(state.pages)
            quit_requested, changed = display.events(state)
            if quit_requested:
                if state.request_close(now):
                    break
                # Keep FIFO readers alive while the launcher stops and joins
                # workers, then wait for its SIGTERM. Esc must not cause EPIPE.
                changed = True
            state.check_close_timeout(now)
            if now >= next_status or changed:
                state.poll_manifest(args.manifest)
                for page in state.pages:
                    page.poll_summary()
                write_status(status_path, state.snapshot(now, unix_ms))
                next_status = now + 1.0
            if now >= next_render or changed:
                display.render(state, now)
                next_render = time.monotonic() + 1.0 / fps
            if not received:
                time.sleep(.002)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print("HDMI_VIEWER_ERROR: " + str(exc), file=sys.stderr, flush=True)
        exit_code = 1
        if state is not None:
            state.failure = str(exc)
    finally:
        if state is not None:
            for page in state.pages:
                page.close()
            try:
                result = state.snapshot(time.monotonic(), time.time_ns() // 1_000_000, running=False)
                result["exit_code"] = exit_code
                write_status(status_path, result)
            except OSError as exc:
                print("HDMI_VIEWER_STATUS_ERROR: " + str(exc), file=sys.stderr, flush=True)
        if display is not None:
            display.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
