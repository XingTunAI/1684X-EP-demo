#!/usr/bin/env python3
"""Prepare a longer local HDMI demo video by repeating packets without encoding.

The output repeats a local clip to avoid frequent decoder teardown at short-file
EOF. It is not independent camera footage or a real live-stream acceptance test.
Success prints only the resulting absolute video path to stdout.
"""

import argparse
import datetime
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import uuid


KIND = "bm1684x-hdmi-loop-material-v1"
SOURCE = "third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4"
SYSTEM_LIBS = "/usr/lib/aarch64-linux-gnu"
SPACE_RESERVE_BYTES = 1024 ** 3


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]) if sys.platform.startswith("linux") else None,
                        help="Absolute Linux repository directory; required for dry-run on other hosts.")
    parser.add_argument("--input", default=SOURCE, help="Local input video, relative to repository or absolute.")
    parser.add_argument("--seconds", type=positive, default=600)
    parser.add_argument("--output", help="Local .mp4 output; default: data/inputs/hdmi_wall_demo_loop_<seconds>s.mp4")
    parser.add_argument("--dry-run", action="store_true", help="Print exact probe/copy commands and paths without reading or writing files.")
    args = parser.parse_args(argv)
    if not args.root or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if "://" in args.input or (args.output and "://" in args.output):
        parser.error("--input and --output must be local filesystem paths")
    if args.output and PurePosixPath(args.output).suffix.lower() != ".mp4":
        parser.error("--output must have a .mp4 extension")
    return args


def system_environment():
    env = dict(os.environ, LD_LIBRARY_PATH=SYSTEM_LIBS)
    env.pop("LD_PRELOAD", None)
    env.pop("LD_AUDIT", None)
    return env


def probe_command(path):
    return ["/usr/bin/ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name,codec_type,width,height,pix_fmt,r_frame_rate,avg_frame_rate,color_space,color_range,duration:format=duration",
            "-of", "json", str(path)]


def copy_command(source, seconds, output):
    return ["/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
            "-stream_loop", "-1", "-i", str(source), "-map", "0:v:0", "-t", str(seconds),
            "-c", "copy", "-an", "-movflags", "+faststart", "-f", "mp4", str(output)]


def build_plan(args):
    root = PurePosixPath(args.root)
    source = PurePosixPath(args.input)
    source = source if source.is_absolute() else root / source
    output = PurePosixPath(args.output or f"data/inputs/hdmi_wall_demo_loop_{args.seconds}s.mp4")
    output = output if output.is_absolute() else root / output
    temporary = output.parent / ("." + output.name + ".prepare-" + uuid.uuid4().hex) / "video.mp4"
    return {"input": str(source), "seconds": args.seconds, "output": str(output),
            "manifest": str(output) + ".manifest.json", "temporary_output": str(temporary),
            "environment": {"LD_LIBRARY_PATH": SYSTEM_LIBS, "unset": ["LD_PRELOAD", "LD_AUDIT"]},
            "probe_input_command": probe_command(source), "copy_command": copy_command(source, args.seconds, temporary),
            "probe_output_command": probe_command(temporary),
            "note": "Repeated local video packets; first video stream only, no re-encoding. Avoids short-clip EOF reopen, not a live-camera acceptance test."}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path, env):
    completed = subprocess.run(probe_command(path), env=env, capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError("ffprobe did not return a video description")
    streams = result.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise ValueError("Input must contain a readable first video stream")
    video = dict(streams[0])
    if not video.get("codec_name") or video.get("width", 0) <= 0 or video.get("height", 0) <= 0:
        raise ValueError("Video has no valid codec or dimensions")
    rate = Fraction(video.get("r_frame_rate", "0/1"))
    duration = float(video.get("duration", result.get("format", {}).get("duration", "nan")))
    if rate <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video must have a finite positive duration and nominal frame rate")
    video["duration_seconds"] = duration
    return video


def verify_output(source, output, seconds):
    for key in ("codec_name", "width", "height", "pix_fmt"):
        if source.get(key) != output.get(key):
            raise ValueError(f"Stream copy unexpectedly changed {key}: {source.get(key)} -> {output.get(key)}")
    if Fraction(source["r_frame_rate"]) != Fraction(output["r_frame_rate"]):
        raise ValueError("Stream copy unexpectedly changed the nominal frame rate")
    for key in ("color_space", "color_range"):
        if source.get(key) not in (None, "unknown") and source[key] != output.get(key):
            raise ValueError(f"Stream copy unexpectedly changed {key}")
    # Packet copying ends on packet boundaries; a small frame-level discrepancy
    # is possible with timestamp reordering, without dropping/re-encoding frames.
    tolerance = max(.5, 2 / float(Fraction(source["r_frame_rate"])))
    if abs(output["duration_seconds"] - seconds) > tolerance:
        raise ValueError(f"Output duration {output['duration_seconds']}s differs from requested {seconds}s")


def check_output_space(source, output, source_info, seconds):
    # probe() already rejects missing, nonfinite and nonpositive durations.
    # Integer/Fraction arithmetic avoids rounding a large requested duration
    # down or overflowing an intermediate float.
    estimated = math.ceil(Fraction(source.stat().st_size * seconds) /
                          Fraction(str(source_info["duration_seconds"])))
    required = estimated + (estimated + 19) // 20 + SPACE_RESERVE_BYTES
    filesystem_path = output.parent
    while not filesystem_path.exists():
        if filesystem_path.parent == filesystem_path:
            raise ValueError(f"Cannot determine the output filesystem for {output}")
        filesystem_path = filesystem_path.parent
    available = shutil.disk_usage(filesystem_path).free
    details = (f"estimated output {estimated:,} bytes ({estimated / 1024 ** 3:.2f} GiB), "
               f"required {required:,} bytes including 5% + 1 GiB reserve, "
               f"available {available:,} bytes on {filesystem_path}")
    print("Stream-copy space estimate from source size and duration: " + details, file=sys.stderr)
    if available < required:
        raise ValueError("Insufficient free space: " + details)


def prepare(plan):
    source, output, manifest = (Path(plan[key]) for key in ("input", "output", "manifest"))
    if not source.is_file():
        raise ValueError(f"Local input is missing: {source}")
    if source.resolve() == output.resolve():
        raise ValueError("Input and output must be different files")
    for executable in ("/usr/bin/ffprobe", "/usr/bin/ffmpeg"):
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"Required system tool is missing or not executable: {executable}")
    env = system_environment()
    source_info = probe(source, env)
    source_hash = sha256(source)
    if output.is_symlink() or manifest.is_symlink():
        raise ValueError("Refusing to replace or reuse an output/manifest symlink")
    if output.exists() or manifest.exists():
        if not output.is_file() or not manifest.is_file():
            raise ValueError(f"Output or manifest already exists without its matching pair; refusing overwrite: {output}")
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or saved.get("kind") != KIND or saved.get("source_sha256") != source_hash
                or saved.get("seconds") != plan["seconds"] or saved.get("output_sha256") != sha256(output)):
            raise ValueError(f"Existing file is not a matching verified generated asset; refusing overwrite: {output}")
        print("Reusing verified repeated local clip: " + str(output), file=sys.stderr)
        return output
    # A verified cache needs no new video allocation, so check only after that
    # reuse path. Reject before creating a temporary directory or running ffmpeg.
    check_output_space(source, output, source_info, plan["seconds"])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = Path(plan["temporary_output"])
    temporary_dir = temporary_output.parent
    # A private directory prevents another writer from replacing our temporary
    # files. Final hard links publish complete files atomically and never replace
    # an unknown destination created after the checks above.
    temporary_dir.mkdir(mode=0o700, exist_ok=False)
    temporary_manifest = temporary_dir / "manifest.json"
    try:
        print(f"Repeating local clip to approximately {plan['seconds']}s using video stream copy (no encoding).", file=sys.stderr)
        subprocess.run(plan["copy_command"], env=env, check=True, stdout=sys.stderr)
        output_info = probe(temporary_output, env)
        verify_output(source_info, output_info, plan["seconds"])
        if sha256(source) != source_hash:
            raise ValueError("Source changed while preparing the loop; generated output was not published")
        output_hash = sha256(temporary_output)
        metadata = {"kind": KIND, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "source": str(source.resolve()), "source_sha256": source_hash, "source_video": source_info,
                    "seconds": plan["seconds"], "output": str(output.absolute()), "output_sha256": output_hash,
                    "output_video": output_info, "command": plan["copy_command"], "note": plan["note"]}
        with temporary_manifest.open("x", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with temporary_output.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.link(temporary_manifest, manifest)
        os.link(temporary_output, output)
        print("Prepared repeated local footage; this avoids short-file EOF reopen, not a real live-camera acceptance test.", file=sys.stderr)
        return output
    finally:
        # Handle interruption between publishing the two links as well as an
        # unknown output racing publication. Never delete a different inode.
        if (temporary_manifest.exists() and manifest.exists()
                and os.path.samefile(temporary_manifest, manifest)
                and not (temporary_output.exists() and output.exists() and os.path.samefile(temporary_output, output))):
            manifest.unlink()
        for owned in (temporary_output, temporary_manifest):
            if owned.exists():
                owned.unlink()
        temporary_dir.rmdir()


def main(argv=None):
    args = arguments(argv)
    plan = build_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not sys.platform.startswith("linux"):
        print("Generation requires the Linux board; use --dry-run on this host.", file=sys.stderr)
        return 2

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        print(str(prepare(plan).absolute()))
        return 0
    except KeyboardInterrupt:
        print("Interrupted; this invocation's temporary files were cleaned up.", file=sys.stderr)
        return 130
    except (OSError, ValueError, TypeError, ZeroDivisionError, subprocess.CalledProcessError) as exc:
        print("Cannot prepare HDMI loop: " + str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
