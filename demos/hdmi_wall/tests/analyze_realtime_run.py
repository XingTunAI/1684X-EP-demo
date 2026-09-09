#!/usr/bin/env python3
"""Check a pulled HDMI-wall run and print JSON; no board/SDK dependencies.

    python demos/hdmi_wall/tests/analyze_realtime_run.py RUN_DIR --output report.json

All JSONL records, including warmup and drain, participate in integrity checks.
Measured metrics from summary.json remain authoritative: JSONL has detection
completion time, whereas the worker assigns measured counts at record-write time.
"""

import argparse
import json
import math
from pathlib import Path
import statistics
import sys


AGE_FIELDS = ("frame_age_ms", "source_age_ms")
COUNT_FIELDS = ("decoded", "completed", "policy_drops", "dropped_overwrite",
                "dropped_stale", "dropped_shutdown", "unprocessed_decoded_frames")


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def metric(values):
    values = sorted(v for v in values if number(v))
    if not values:
        return {"count": 0, "mean": None, "p95": None, "max": None}
    return {"count": len(values), "mean": statistics.fmean(values),
            "p95": values[math.ceil(.95 * len(values)) - 1], "max": values[-1]}


def ages(records):
    return {field: metric(r.get(field) for r in records) for field in AGE_FIELDS}


class Issues:
    def __init__(self):
        self.items = {}

    def add(self, code, detail):
        entry = self.items.setdefault(code, {"count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < 3:
            entry["examples"].append(detail)


def load_json(path):
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def trend(records, stream, config, root_summary, issues):
    window = config.get("window_s", 10)
    if not number(window) or window <= 0:
        issues.add("invalid_window", window)
        return None
    source_fps = stream.get("source_fps")
    anchors = []
    if number(source_fps) and source_fps > 0:
        for record in records:
            if (number(record.get("source_age_ms")) and count(record.get("frame"))
                    and number(record.get("analysis_completed_monotonic_s"))):
                anchors.append(record["analysis_completed_monotonic_s"]
                               - record["source_age_ms"] / 1000 - record["frame"] / source_fps)
    span = root_summary.get("measured_seconds")
    warmup = config.get("warmup_s")
    if anchors and number(span) and span > 0 and number(warmup):
        anchor = statistics.median(anchors)
        start, end = anchor + warmup, anchor + warmup + span
        timestamp = "analysis_completed_monotonic_s"
        basis = {"scope": "estimated_measurement_interval", "timestamp": timestamp,
                 "anchor": "local due = analysis_completed - source_age; start = due - frame/source_fps",
                 "run_start_monotonic_s": anchor, "anchor_spread_ms": (max(anchors) - min(anchors)) * 1000,
                 "note": "Detection completion approximates record-write membership; summary measured metrics are authoritative."}
    else:
        times = [r["received_monotonic_s"] for r in records if number(r.get("received_monotonic_s"))]
        if not times:
            return {"scope": "unavailable", "note": "No usable monotonic timestamps."}
        start, end = min(times), max(times)
        timestamp = "received_monotonic_s"
        basis = {"scope": "all_records_relative_to_first_received_including_warmup_and_drain",
                 "timestamp": timestamp, "first_received_monotonic_s": start,
                 "note": "No reliable run-start anchor; these are observed reception windows, not formal measurement windows."}
    width = min(window, end - start)
    first = [r for r in records if number(r.get(timestamp)) and start <= r[timestamp] < start + width]
    last = [r for r in records if number(r.get(timestamp)) and end - width <= r[timestamp] < end]
    if basis["scope"].startswith("all_records"):
        # The fallback end is an observed record time, so include the final record.
        last.extend(r for r in records if r.get(timestamp) == end)
        if width == 0:
            first = list(last)
    first_stats, last_stats = ages(first), ages(last)
    delta = {field: (last_stats[field]["mean"] - first_stats[field]["mean"]
                     if first_stats[field]["count"] and last_stats[field]["count"] else None)
             for field in AGE_FIELDS}
    return {**basis, "window_s": width, "windows_overlap": 2 * width > end - start,
            "first": {"start_relative_s": 0, "records": len(first), **first_stats},
            "last": {"start_relative_s": max(0, end - width - start), "records": len(last), **last_stats},
            "last_minus_first_mean_ms": delta}


def analyze_stream(run_dir, stream, config, root_summary, all_issues):
    stream_id = stream.get("stream_id")
    source_id = stream.get("source_id", f"stream_{stream_id:02d}" if count(stream_id) else "unknown")
    issues = Issues()
    records = []
    stream_dir = run_dir / source_id
    if stream_dir.parent.resolve() != run_dir.resolve():
        raise ValueError(f"Invalid source_id path: {source_id!r}")
    try:
        if load_json(stream_dir / "summary.json") != stream:
            issues.add("stream_summary_mismatch", "Root and per-stream summary differ")
    except (OSError, ValueError) as exc:
        issues.add("stream_summary_read", str(exc))
    try:
        with (stream_dir / "detections.jsonl").open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("Record is not an object")
                    records.append(record)
                except ValueError as exc:
                    issues.add("invalid_jsonl", {"line": line_number, "error": str(exc)})
    except OSError as exc:
        issues.add("records_read", str(exc))
    previous = None
    skipped = 0
    observed_loops = set()
    for index, record in enumerate(records):
        def bad(code, detail):
            issues.add(code, {"record": index, "detail": detail})
        for field in ("processed_index", "frame", "source_frame_id", "source_loop"):
            if not count(record.get(field)):
                bad("invalid_index", field)
        if record.get("processed_index") != index:
            bad("processed_index_not_contiguous", record.get("processed_index"))
        if record.get("stream_id") != stream_id or record.get("source_id") != source_id:
            bad("record_stream_mismatch", [record.get("stream_id"), record.get("source_id")])
        if record.get("policy") != stream.get("policy", config.get("policy")):
            bad("record_policy_mismatch", record.get("policy"))
        if all(count(record.get(f)) for f in ("frame", "source_frame_id", "source_loop")):
            frame, source_frame, loop = (record[f] for f in ("frame", "source_frame_id", "source_loop"))
            observed_loops.add(loop)
            if source_frame > frame or (loop == 0 and frame != source_frame):
                bad("source_frame_invalid_offset", [frame, source_frame, loop])
            if previous is not None:
                old_frame, old_source, old_loop = previous
                if frame <= old_frame:
                    bad("frame_not_strictly_increasing", [old_frame, frame])
                else:
                    skipped += frame - old_frame - 1
                if loop < old_loop:
                    bad("source_loop_decreased", [old_loop, loop])
                elif loop == old_loop:
                    if frame - source_frame != old_frame - old_source:
                        bad("source_frame_delta_mismatch", [old_frame, old_source, frame, source_frame])
                elif frame - source_frame <= old_frame - old_source:
                    bad("source_loop_offset_not_increasing", [old_frame, old_source, frame, source_frame])
                # A latest policy can skip whole loops; the first observed ID in
                # a new loop need not be zero or lower than the previous ID.
            else:
                skipped += frame
            previous = frame, source_frame, loop
            if stream.get("policy") == "all" and frame != index:
                bad("all_policy_frame_not_contiguous", frame)
        for field in AGE_FIELDS:
            if not (field == "source_age_ms" and record.get(field) is None) and not number(record.get(field)):
                bad("invalid_age", field)
        received, completed = record.get("received_monotonic_s"), record.get("analysis_completed_monotonic_s")
        if not number(received) or not number(completed) or completed < received:
            bad("invalid_timestamps", [received, completed])
        elif number(record.get("frame_age_ms")) and abs((completed - received) * 1000 - record["frame_age_ms"]) > .1:
            bad("frame_age_timestamp_mismatch", record["frame_age_ms"])
    counts = {field: stream.get(field) for field in COUNT_FIELDS}
    if not all(count(value) for value in counts.values()):
        issues.add("invalid_summary_counts", counts)
    else:
        if counts["decoded"] != counts["completed"] + counts["policy_drops"] + counts["unprocessed_decoded_frames"]:
            issues.add("decoded_accounting_mismatch", counts)
        if counts["policy_drops"] != sum(counts[f] for f in ("dropped_overwrite", "dropped_stale", "dropped_shutdown")):
            issues.add("drop_accounting_mismatch", counts)
        if len(records) != counts["completed"]:
            issues.add("record_count_mismatch", {"records": len(records), "completed": counts["completed"]})
        if previous is not None and previous[0] >= counts["decoded"]:
            issues.add("frame_exceeds_decoded", previous[0])
        if root_summary.get("records_complete") and counts["unprocessed_decoded_frames"]:
            issues.add("complete_with_unprocessed_frames", counts["unprocessed_decoded_frames"])
    high_watermark = stream.get("queue_high_watermark")
    if stream.get("policy") == "latest" and (not count(high_watermark) or high_watermark > 1):
        issues.add("latest_queue_capacity_exceeded", high_watermark)
    for phase in ("completed", "decoded"):
        windows = stream.get("windows", [])
        values = [window.get(phase) for window in windows]
        measured = stream.get(f"{phase}_measured")
        if not count(measured) or not all(count(v) for v in values) or sum(values) != measured:
            issues.add("measured_windows_mismatch", {"phase": phase, "sum": sum(v for v in values if count(v)), "measured": measured})
    if stream.get("error"):
        issues.add("worker_error", stream["error"])
    trend_result = trend(records, stream, config, root_summary, issues)
    if issues.items:
        all_issues.add("stream_failed", source_id)
    return {"source_id": source_id, "checks_passed": not issues.items, "issues": issues.items,
            "counts": {**counts, "records": len(records), "skipped_before_or_between_records": skipped,
                       "observed_loops": sorted(observed_loops)},
            "queue_high_watermark": high_watermark,
            "completed_fps": stream.get("completed_fps"), "decoded_fps": stream.get("decoded_fps"),
            "measured_summary": {field: stream.get(field) for field in AGE_FIELDS},
            "all_records_age": ages(records), "trend": trend_result}


def analyze(run_dir):
    run_dir = Path(run_dir).resolve()
    summary, config = load_json(run_dir / "summary.json"), load_json(run_dir / "config.json")
    issues = Issues()
    streams = [analyze_stream(run_dir, stream, config, summary, issues) for stream in summary["streams"]]
    if len(streams) != config.get("streams"):
        issues.add("stream_count_mismatch", {"config": config.get("streams"), "summary": len(streams)})
    ids = [stream["source_id"] for stream in streams]
    if len(set(ids)) != len(ids):
        issues.add("duplicate_source_id", ids)
    if all(count(s["counts"]["policy_drops"]) for s in streams):
        drops = sum(s["counts"]["policy_drops"] for s in streams)
        if drops != summary.get("application_drops"):
            issues.add("total_drop_mismatch", {"streams": drops, "summary": summary.get("application_drops")})
    if summary.get("error"):
        issues.add("run_error", summary["error"])
    return {"run_dir": str(run_dir), "policy": summary.get("policy"), "status": summary.get("status"),
            "checks_passed": not issues.items, "issues": issues.items,
            "measurement_finished": summary.get("status") == "measured", "records_complete": summary.get("records_complete"),
            "measured_seconds": summary.get("measured_seconds"), "drain_seconds": summary.get("drain_seconds"),
            "total_completed_fps": summary.get("total_completed_fps"), "application_drops": summary.get("application_drops"),
            "note": "Integrity checks include warmup/drain. Age is software latency, not camera-to-HDMI latency. Null source age is expected for RTSP.",
            "streams": streams}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this local path")
    args = parser.parse_args()
    try:
        report = analyze(args.run_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"run_dir": str(args.run_dir), "checks_passed": False, "fatal_error": str(exc)}
    result = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    return 0 if report["checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
