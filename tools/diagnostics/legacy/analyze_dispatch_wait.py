#!/usr/bin/env python3
"""Analyze a retrieved dispatch-wait matrix locally, preserving each round.

Writes a new output directory; never changes the source experiment. Traced call
durations and status-counter throughput have different timing boundaries.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from analyze_dispatch_graph import (
    CALL, IDENTITY, parse_trace, _summarize_functions, _summarize_stages,
)


NOTES = [
    "Each batch and mode is reported separately; repeated calls are not independent experimental replicates.",
    "Graph stages are inclusive and nested: never add parents to children or treat thread-time sums as utilization.",
    "Roots with any stage remainder below -1 us are excluded in full; original parser diagnostics and calls are retained.",
    "Completion rate divides complete roots by observed selected-root entry records; it is not capture coverage of all real calls.",
    "Mutex function time includes acquisition overhead; completion waits include execution, notification and scheduling.",
    "Function filters can hide helper frames; absent stages are unobserved, not proven zero-cost.",
    "The watcher pauses status sampling during capture. Session throughput brackets capture setup, recording, dump and restore; it is NOT throughput exactly while tracing_on=1.",
    "Use snapshot_monotonic_s and cumulative counter differences, not the rolling total_infer_fps field.",
    "Untraced baseline excludes each observed capture session plus the configured guard on both sides.",
    "Differences from neighboring baselines are descriptive and may contain scene variation or lasting trace effects, not a causal overhead correction.",
    "A mixed 60-second summary includes intrusive captures and is not an untraced performance benchmark.",
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def negative_paths(stage: dict, path: str = "root") -> list[dict]:
    found = []
    if stage["unclassified_remainder_us"] < -1.0:
        found.append({"stage": path, "remainder_us": stage["unclassified_remainder_us"]})
    for name, child in stage["children"].items():
        found.extend(negative_paths(child, path + "/" + name))
    return found


def root_entries(text: str, roots: list[str]) -> dict[str, int]:
    counts = Counter()
    for line in text.splitlines():
        fields = line.split("|", 3)
        if len(fields) != 4 or not IDENTITY.fullmatch(fields[1]):
            continue
        call = CALL.match(fields[3].strip())
        if call and call[1] in roots:
            counts[call[1]] += 1
    return {name: counts[name] for name in roots}


def analyze_graph(path: Path, capture: dict) -> tuple[dict, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    roots = capture["roots"]
    parsed = parse_trace(text, roots)
    starts = root_entries(text, roots)
    accepted, excluded = [], []
    for call in parsed["calls"]:
        reasons = negative_paths(call["stages"])
        if reasons:
            excluded.append({"root": call["root"], "tid": call["tid"],
                             "start_line": call["start_line"],
                             "return_line": call["return_line"], "reasons": reasons})
        else:
            accepted.append(call)
    summaries = {}
    for root in roots:
        original = [c for c in parsed["calls"] if c["root"] == root]
        rows = [c for c in accepted if c["root"] == root]
        entry_count = starts[root]
        summaries[root] = {
            "observed_entry_records": entry_count,
            "parsed_complete_roots": len(original), "accepted_roots": len(rows),
            "excluded_negative_roots": len(original) - len(rows),
            "structural_completion_rate": len(original) / entry_count if entry_count else None,
            "accepted_entry_rate": len(rows) / entry_count if entry_count else None,
            "stages": _summarize_stages([row["stages"] for row in rows]) if rows else None,
            "inclusive_function_stats": _summarize_functions(rows),
        }
    diagnostics = {
        "source": str(path.resolve()),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "capture": capture, "parser_diagnostics": parsed["diagnostics"],
        "excluded_negative_roots": excluded,
        "accepted_start_lines": [row["start_line"] for row in accepted],
        "summary_after_filter": summaries,
    }
    # Preserve original per-call rows, nested values, timings and parser summary.
    detailed = {"analysis": diagnostics, "original_parse": parsed}
    return diagnostics, detailed


def read_samples(path: Path, thumbnail_bytes: int) -> tuple[list[dict], dict]:
    if not path.exists():
        return [], {"missing_file": str(path)}
    samples, issues = [], []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
            status = record.get("status", record)
            streams = {str(row["id"]): row for row in status["streams"]}
            if len(streams) != len(status["streams"]):
                raise ValueError("duplicate stream ids")
            counters = {
                "inference_frames": sum(row["inferred_count"] for row in streams.values()),
                "decoded_frames": sum(row["decoded_count"] for row in streams.values()),
            }
            for field, name, divisor in [
                ("preview_readback_bytes", "preview_frames", thumbnail_bytes),
                ("preview_readback_bytes", "preview_bytes", 1),
                ("output_readback_bytes", "output_bytes", 1),
                ("published_frames", "wall_published_frames", 1),
            ]:
                if field in status:
                    counters[name] = status[field] / divisor
            samples.append({
                "time": float(status["snapshot_monotonic_s"]),
                "sample_time": record.get("monotonic"), "line": line_no,
                "counters": counters,
                "stream_inference_counts": {sid: row["inferred_count"] for sid, row in streams.items()},
            })
        except (ValueError, KeyError, TypeError) as exc:
            issues.append({"line": line_no, "error": str(exc)})
    samples.sort(key=lambda sample: (sample["time"], sample["line"]))
    unique = []
    duplicates = 0
    for sample in samples:
        if unique and sample["time"] == unique[-1]["time"]:
            duplicates += 1
            continue
        unique.append(sample)
    return unique, {"rows_read": len(samples), "unique_snapshots": len(unique),
                    "duplicate_snapshot_timestamps": duplicates, "issues": issues}


def interval(first: dict, last: dict) -> dict | None:
    seconds = last["time"] - first["time"]
    if seconds <= 0 or first["stream_inference_counts"].keys() != last["stream_inference_counts"].keys():
        return None
    deltas = {key: last["counters"][key] - value
              for key, value in first["counters"].items() if key in last["counters"]}
    per_stream = {key: last["stream_inference_counts"][key] - value
                  for key, value in first["stream_inference_counts"].items()}
    if any(value < 0 for value in (*deltas.values(), *per_stream.values())):
        return None
    return {"start": first["time"], "end": last["time"], "seconds": seconds,
            "first_sample_line": first["line"], "last_sample_line": last["line"],
            "counter_deltas": deltas, "rates_per_second": {k: v / seconds for k, v in deltas.items()},
            "stream_inference_deltas": per_stream,
            "minimum_stream_inference_fps": min(per_stream.values()) / seconds if per_stream else None}


def aggregate_intervals(intervals: list[dict]) -> dict | None:
    if not intervals:
        return None
    seconds = sum(row["seconds"] for row in intervals)
    common = set.intersection(*(set(row["counter_deltas"]) for row in intervals))
    deltas = {key: sum(row["counter_deltas"][key] for row in intervals) for key in sorted(common)}
    return {"covered_seconds": seconds, "interval_count": len(intervals),
            "first_snapshot": intervals[0]["start"], "last_snapshot": intervals[-1]["end"],
            "counter_deltas": deltas,
            "rates_per_second": {key: value / seconds for key, value in deltas.items()}}


def overlaps(start: float, end: float, masks: list[tuple[float, float]]) -> bool:
    return any(start < right and end > left for left, right in masks)


def analyze_sessions(samples: list[dict], captures: dict[str, dict], summary: dict,
                     guard_s: float = 2, baseline_span_s: float = 6,
                     minimum_baseline_s: float = 2) -> dict:
    if not summary or not samples:
        return {"available": False, "reason": "summary or status snapshots missing",
                "sessions": {}, "untraced_baseline": None}
    formal_start = summary["measurement_start_monotonic_s"]
    formal_end = min(summary["measurement_end_monotonic_s"],
                     summary.get("observed_measurement_end_monotonic_s", float("inf")))
    sessions, masks = {}, []
    for group, capture in captures.items():
        start, end = capture["before_enable_monotonic"], capture["after_disable_monotonic"]
        previous = [sample for sample in samples if formal_start <= sample["time"] <= start]
        following = [sample for sample in samples if end <= sample["time"] < formal_end]
        measured = interval(previous[-1], following[0]) if previous and following else None
        lower = capture["before_disable_monotonic"] - capture["after_enable_monotonic"]
        upper = end - start
        session = {"capture_recording_duration_bounds_s": [lower, upper],
                   "capture_within_formal_window": formal_start <= start <= end <= formal_end,
                   "counter_session": measured,
                   "boundary_note": "Nearest snapshots bracketing enable/disable also bracket setup/dump/restore; no in-capture status sampling."}
        if measured:
            masks.append((measured["start"] - guard_s, measured["end"] + guard_s))
        else:
            # Exclude an expanded capture even when a valid counter pair is absent.
            masks.append((start - guard_s, end + guard_s))
        sessions[group] = session
    raw_intervals, eligible, invalid_intervals = [], [], []
    for first, last in zip(samples, samples[1:]):
        if first["time"] < formal_start or last["time"] >= formal_end:
            continue
        item = interval(first, last)
        if item is None:
            invalid_intervals.append([first["line"], last["line"]])
            continue
        raw_intervals.append(item)
        if not overlaps(item["start"], item["end"], masks):
            eligible.append(item)
    for group, session in sessions.items():
        item = session["counter_session"]
        if not item:
            session["comparison_available"] = False
            continue
        pre_end, post_start = item["start"] - guard_s, item["end"] + guard_s
        before = [row for row in eligible if pre_end - baseline_span_s <= row["start"] and row["end"] <= pre_end]
        after = [row for row in eligible if post_start <= row["start"] and row["end"] <= post_start + baseline_span_s]
        pre, post, both = aggregate_intervals(before), aggregate_intervals(after), aggregate_intervals(before + after)
        session.update(pre_baseline=pre, post_baseline=post, combined_baseline=both,
                       comparison_available=bool(both))
        enough = bool(pre and post and pre["covered_seconds"] >= minimum_baseline_s
                      and post["covered_seconds"] >= minimum_baseline_s)
        session["both_baselines_long_enough"] = enough
        other = [name for name, cap in captures.items() if name != group and
                 item["start"] < cap["after_disable_monotonic"] and item["end"] > cap["before_enable_monotonic"]]
        session["other_captures_in_same_session"] = other
        session["relative_session_rate_change_pct"] = {}
        if both:
            for metric, reference in both["rates_per_second"].items():
                actual = item["rates_per_second"].get(metric)
                if reference > 0 and actual is not None:
                    session["relative_session_rate_change_pct"][metric] = 100 * (actual / reference - 1)
        drift = None
        if pre and post:
            a, b = pre["rates_per_second"]["inference_frames"], post["rates_per_second"]["inference_frames"]
            if a + b > 0:
                drift = 200 * abs(a - b) / (a + b)
        session["neighbor_inference_baseline_difference_pct"] = drift
        session["descriptive_comparison_quality"] = (
            "adequate_neighbors" if enough and not other and drift is not None and drift <= 10
            else "limited_neighbors_or_drift")
    return {
        "available": True, "formal_start": formal_start, "formal_end": formal_end,
        "guard_seconds": guard_s, "neighbor_baseline_span_seconds": baseline_span_s,
        "minimum_each_neighbor_baseline_seconds": minimum_baseline_s,
        "quality_flag_drift_threshold_pct": 10,
        "quality_note": "10% neighbor drift is a descriptive flag, not a statistical significance test.",
        "exclusion_intervals_including_guard": masks,
        "sessions": sessions, "untraced_baseline": aggregate_intervals(eligible),
        "eligible_untraced_intervals": eligible,
        "whole_sampled_formal_period": aggregate_intervals(raw_intervals),
        "invalid_counter_intervals_sample_lines": invalid_intervals,
    }


def flatten_stages(stage: dict, prefix: str = "root") -> list[dict]:
    rows = [{"path": prefix, "count": stage["inclusive_us"]["count"],
             "matched_calls": stage["matched_calls"], **stage["inclusive_us"],
             "unclassified_mean_us": stage["unclassified_remainder_us"]["mean_us"]}]
    for name, child in stage["children"].items():
        rows.extend(flatten_stages(child, prefix + "/" + name))
    return rows


def numeric(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def build_markdown(report: dict) -> str:
    lines = ["# Dispatch wait analysis", "", "> Traced attribution windows; not an untraced benchmark.", "",
             "Root durations are inclusive microseconds. Retained/entries excludes malformed and negative-accounting roots.", "",
             "| Stage | Group | Root | Retained/entries | Mean µs | P50 µs | P95 µs | Max µs |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for name, stage in report["stages"].items():
        for group, graph in stage["graphs"].items():
            for root, row in graph["summary_after_filter"].items():
                stats = row["stages"]["inclusive_us"] if row["stages"] else {}
                values = [numeric(stats.get(key)) for key in ("mean_us", "p50_us", "p95_us", "max_us")]
                lines.append(f"| {name} | {group} | {root} | {row['accepted_roots']}/{row['observed_entry_records']} | " + " | ".join(values) + " |")
    lines += ["", "## Counter throughput around capture sessions", "",
              "Status sampling stops during capture. These intervals include capture setup/dump/restoration; they are longer than tracing_on.", "",
              "| Stage | Group | Session s | Session INF/s | Neighbor baseline INF/s | Change % | Quality |",
              "|---|---|---:|---:|---:|---:|---|"]
    for name, stage in report["stages"].items():
        for group, session in stage["throughput"].get("sessions", {}).items():
            interval_data = session["counter_session"]
            if not interval_data:
                lines.append(f"| {name} | {group} | — | — | — | — | no valid bracket |")
                continue
            baseline = session.get("combined_baseline")
            baseline_fps = baseline["rates_per_second"].get("inference_frames") if baseline else None
            values = [interval_data["seconds"], interval_data["rates_per_second"].get("inference_frames"),
                      baseline_fps, session["relative_session_rate_change_pct"].get("inference_frames")]
            lines.append(f"| {name} | {group} | " + " | ".join(numeric(value) for value in values) +
                         f" | {session['descriptive_comparison_quality']} |")
    lines += ["", "## Interpretation limits", ""] + ["- " + note for note in report["notes"]]
    return "\n".join(lines) + "\n"


def analyze_directory(source: Path, output: Path, guard_s: float = 2,
                      baseline_span_s: float = 6, thumbnail_bytes: int = 110592) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}; choose a fresh directory")
    stage_paths = [source] if list(source.glob("*.capture.json")) else sorted(
        path for path in source.iterdir() if path.is_dir() and list(path.glob("*.capture.json")))
    if not stage_paths:
        raise ValueError("No stage with *.capture.json found")
    output.mkdir(parents=True)
    config = load_json(source / "config.json") if (source / "config.json").exists() else {}
    state = load_json(source / "state.json") if (source / "state.json").exists() else {}
    report = {"schema_version": 1, "source": str(source), "experiment_config": config,
              "experiment_state": state, "notes": NOTES,
              "analysis_parameters": {"guard_s": guard_s, "baseline_span_s": baseline_span_s,
                                      "thumbnail_bytes": thumbnail_bytes}, "stages": {}}
    csv_rows = []
    for path in stage_paths:
        name = path.name
        stage_output = output / name
        stage_output.mkdir()
        captures, graphs, warnings = {}, {}, []
        summary_path = path / "worker/summary.json"
        summary = load_json(summary_path) if summary_path.exists() else {}
        for capture_path in sorted(path.glob("*.capture.json")):
            group = capture_path.name.removesuffix(".capture.json")
            cap = load_json(capture_path)
            captures[group] = cap
            trace_path = path / (group + ".trace")
            if not trace_path.exists():
                warnings.append(f"Missing trace: {trace_path.name}")
                continue
            graph, detailed = analyze_graph(trace_path, cap)
            detailed_path = stage_output / (group + ".graph.json")
            save_json(detailed_path, detailed)
            graph["details_file"] = str(detailed_path.relative_to(output))
            graphs[group] = graph
            for root, row in graph["summary_after_filter"].items():
                if row["stages"]:
                    for values in flatten_stages(row["stages"]):
                        csv_rows.append({"stage": name, "group": group, "root": root, **values})
        samples, sample_diagnostics = read_samples(path / "status-samples.jsonl", thumbnail_bytes)
        throughput = analyze_sessions(samples, captures, summary, guard_s, baseline_span_s)
        command_path = path / "command.json"
        report["stages"][name] = {
            "path": str(path), "command": load_json(command_path) if command_path.exists() else None,
            "original_worker_summary": summary,
            "warnings": warnings, "graphs": graphs,
            "sample_diagnostics": sample_diagnostics, "throughput": throughput,
        }
    save_json(output / "report.json", report)
    (output / "report.md").write_text(build_markdown(report), encoding="utf-8")
    if csv_rows:
        with (output / "stages.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Fresh local output directory")
    parser.add_argument("--guard-s", type=float, default=2)
    parser.add_argument("--baseline-span-s", type=float, default=6)
    parser.add_argument("--thumbnail-bytes", type=int, default=110592)
    args = parser.parse_args()
    if args.guard_s < 0 or args.baseline_span_s <= 0 or args.thumbnail_bytes <= 0:
        parser.error("guard must be nonnegative; baseline span and thumbnail bytes must be positive")
    report = analyze_directory(args.source, args.output, args.guard_s,
                               args.baseline_span_s, args.thumbnail_bytes)
    print(json.dumps({"output": str(args.output.resolve()), "stages": list(report["stages"])}))


if __name__ == "__main__":
    main()
