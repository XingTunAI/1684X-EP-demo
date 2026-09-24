#!/usr/bin/env python3
"""Summarize timer-read ioctls and their bounded function-graph captures.

MONOTONIC_RAW probe timestamps and mono function-graph timestamps are deliberately
not compared. Per-mode root grouping requires a complete capture, one probe TID
and exact call counts. Nested decomposition additionally requires one observed
bm_read32 child per bm_get_reg root; an inlined child is unavailable, not zero.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from analyze_dispatch_graph import parse_trace


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    if not all(math.isfinite(v) for v in ordered):
        raise ValueError("Nonfinite measurement")
    def percentile(percent: int) -> float:
        return ordered[math.ceil(len(ordered) * percent / 100) - 1]
    return {"count": len(values), "sum_us": sum(values),
            "mean_us": statistics.fmean(values), "p50_us": percentile(50),
            "p95_us": percentile(95), "p99_us": percentile(99),
            "min_us": ordered[0], "max_us": ordered[-1],
            "percentile_method": "nearest rank (same as mmio_read_probe)"}


def cpu_members(text: str) -> set[int]:
    result = set()
    for part in re.split(r"[\s,]+", text.strip()):
        if not part:
            continue
        pair = part.split("-")
        if len(pair) == 1:
            result.add(int(pair[0]))
        elif len(pair) == 2:
            result.update(range(int(pair[0]), int(pair[1]) + 1))
        else:
            raise ValueError("Invalid CPU list: " + text)
    return result


def frequency_context(context: dict, cpu: int) -> dict:
    result = {"note": "Before/after snapshots, not an in-window frequency time series"}
    for phase in ("before", "after"):
        result[phase] = {name: policy for name, policy in
                         context.get(phase, {}).get("cpufreq", {}).items()
                         if cpu in cpu_members(policy.get("affected_cpus", ""))}
    return result


def trace_distribution(calls: list[dict]) -> dict:
    roots, children, remainders = [], [], []
    invalid_children = 0
    for call in calls:
        roots.append(call["duration_us"])
        child = call["inclusive_function_stats"].get("bm_read32")
        if not child or child["count"] != 1:
            invalid_children += 1
            continue
        children.append(child["sum_us"])
        remainders.append(call["duration_us"] - child["sum_us"])
    negative = sum(v < -0.001 for v in remainders)
    return {"bm_get_reg_us": stats(roots), "bm_read32_us": stats(children),
            "roots_without_exactly_one_read32": invalid_children,
            "nested_negative_remainders": negative,
            "nested_breakdown_available": bool(calls) and not negative and not invalid_children,
            "same_root_get_reg_minus_read32_us":
                stats(remainders) if not negative and not invalid_children else None,
            "cpus": sorted({cpu for c in calls for cpu in c["cpus"]}),
            "note": "Nested remainder uses the SAME traced root; includes wrapper/tracer effects, not pure software or wire cost"}


def trace_modes(probe: dict, context: dict, capture: dict, parsed: dict) -> dict:
    """Fail closed on uncertain correspondence; never align unlike clocks."""
    pid = probe["pid"]
    calls = [c for c in parsed["calls"] if c["tid"] == pid]
    reasons = []
    relevant_issues = [i for i in parsed["diagnostics"]["issues"]
                       if i.get("tid") in (None, pid)]
    if relevant_issues:
        reasons.append("Trace parser reports incomplete/ambiguous data for the probe")
    expected = sum(len(run["samples"]) for run in probe["runs"])
    if len(calls) != expected:
        reasons.append(f"Trace/probe call count mismatch: {len(calls)} vs {expected}")
    if not probe["success"] or not all(r["complete"] and r["timer_value_changed"]
                                       and len(r["samples"]) == r["expected_samples"]
                                       for r in probe["runs"]):
        reasons.append("Probe not complete or timer values not verified")
    nested_reasons = []
    if any(c["inclusive_function_stats"].get("bm_read32", {}).get("count") != 1
           for c in calls):
        nested_reasons.append("Not every root contains exactly one observed bm_read32; inlining or filtering can hide child calls")
    begin = capture.get("after_enable_monotonic")
    end = capture.get("before_disable_monotonic")
    before = context.get("before", {}).get("monotonic_s")
    after = context.get("after", {}).get("monotonic_s")
    if None in (begin, end, before, after) or not (begin <= before <= after <= end):
        reasons.append("Whole subprocess is not proven enclosed by the trace window")
    elif any(float(c["start_timestamp"]) < before - 1e-6 or
             (c["return_timestamp"] is not None and
              float(c["return_timestamp"]) > after + 1e-6) for c in calls):
        reasons.append("Traced calls lie outside the subprocess mono-clock envelope")
    counter_names = {"overrun", "commit overrun", "dropped events"}
    for cpu, text in capture.get("cpu_stats", {}).items():
        for line in text.splitlines():
            key, sep, val = line.partition(":")
            if sep and key.strip() in counter_names and int(val.strip()) != 0:
                reasons.append(f"{cpu}: {line.strip()}")
    if not capture.get("cpu_stats"):
        reasons.append("Missing trace loss counters")
    distribution = trace_distribution(calls)
    if distribution["nested_negative_remainders"]:
        nested_reasons.append("Some nested durations exceed their own root")
    result = {"mode_assignment_valid": not reasons, "root_trace_valid": not reasons,
              "rejection_reasons": reasons,
              "nested_breakdown_available": not reasons and distribution["nested_breakdown_available"],
              "nested_breakdown_limitations": nested_reasons,
              "probe_pid": pid, "probe_root_count": len(calls),
              "other_pid_root_count": len(parsed["calls"]) - len(calls),
              "parser_diagnostics": {key: value for key, value in parsed["diagnostics"].items()
                                     if key != "issues"},
              "relevant_parser_issues": relevant_issues,
              "all_probe_roots": distribution, "by_mode": {},
              "assignment_method": "PID + exact complete-call count + sequential run order; raw and mono clocks are NOT directly compared"}
    if not reasons:
        offset = 0
        for run in probe["runs"]:
            count = len(run["samples"])
            result["by_mode"][run["mode"]] = trace_distribution(calls[offset:offset + count])
            offset += count
    return result


def stage_summary(folder: Path) -> dict:
    probe = json.loads((folder / "probe.json").read_text(encoding="utf-8"))
    context = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    errors = []
    result = {"stage": folder.name, "source": str(folder.resolve()),
              "probe_sha256": hashlib.sha256((folder / "probe.json").read_bytes()).hexdigest(),
              "probe_success": probe["success"], "probe_error": probe.get("error"),
              "returncode": context["returncode"], "requested_cpu": probe["requested_cpu"],
              "kernel": probe["kernel"], "machine": probe["machine"],
              "device_path": probe["device_path"], "link_before": {
                  k: context["before"].get(k) for k in ("link_speed", "link_width")},
              "link_after": {k: context["after"].get(k) for k in ("link_speed", "link_width")},
              "cpu_frequency": frequency_context(context, probe["requested_cpu"]),
              "clock_pair_baseline_us": stats([n / 1000 for n in probe["clock_pair_baseline_samples_ns"]]),
              "clock_resolution_ns": probe["clock_resolution_ns"], "baseline_subtracted": False,
              "runs": [], "errors": errors}
    if not probe["success"] or context["returncode"] != 0:
        errors.append("Probe process failed")
    if result["link_before"] != result["link_after"]:
        errors.append("Link changed within probe subprocess")
    for run in probe["runs"]:
        records = [dict(zip(run["sample_columns"], sample)) for sample in run["samples"]]
        if not run["complete"] or len(records) != run["expected_samples"]:
            errors.append(run["mode"] + ": incomplete sample count")
        valid = [s for s in records if s["ioctl_result"] == 0 and s["errno"] == 0]
        if len(valid) != len(records):
            errors.append(run["mode"] + ": unsuccessful ioctls")
        if any(s["duration_ns"] != s["end_raw_ns"] - s["start_raw_ns"]
               or s["duration_ns"] < 0 for s in valid):
            raise ValueError("Inconsistent raw sample duration: " + folder.name)
        frequencies = Counter(s["cpu_before"] for s in valid)
        migrations = sum(s["cpu_before"] != s["cpu_after"] for s in valid)
        unexpected_cpu = any(s["cpu_before"] != probe["requested_cpu"] or
                             s["cpu_after"] != probe["requested_cpu"] for s in valid)
        if migrations or (probe["requested_cpu"] >= 0 and unexpected_cpu):
            errors.append(run["mode"] + ": CPU affinity mismatch/migration")
        changed = len({s["timer_low_u32"] for s in valid}) > 1
        if not changed:
            errors.append(run["mode"] + ": timer value did not change")
        recomputed = stats([s["duration_ns"] / 1000 for s in valid])
        if recomputed is not None:
            for name in ("mean", "p50", "p95", "p99", "max"):
                stored = run["ioctl_duration"][name + "_ns"] / 1000
                if not math.isclose(recomputed[name + "_us"], stored, rel_tol=1e-9, abs_tol=1e-6):
                    errors.append(run["mode"] + ": stored/recomputed " + name + " mismatch")
        result["runs"].append({"mode": run["mode"], "requested_idle_gap_ns": run["requested_idle_gap_ns"],
                               "ioctl_us": recomputed, "complete": run["complete"],
                               "timer_value_changed": changed, "cpu_before_counts": dict(frequencies),
                               "cpu_migrations": migrations,
                               "elapsed_raw_s": ((valid[-1]["end_raw_ns"] - valid[0]["start_raw_ns"]) / 1e9
                                                 if valid else None)})
    trace_file = folder / "timer.trace"
    result["ioctl_errors"] = list(errors)
    result["ioctl_valid"] = not errors
    if folder.name.endswith("_traced"):
        parsed = parse_trace(trace_file, roots=["bm_get_reg"])
        capture = json.loads((folder / "timer.capture.json").read_text(encoding="utf-8"))
        result["trace"] = trace_modes(probe, context, capture, parsed)
        result["trace"]["trace_sha256"] = hashlib.sha256(trace_file.read_bytes()).hexdigest()
        if not result["trace"]["mode_assignment_valid"]:
            errors.extend(result["trace"]["rejection_reasons"])
    return result


def analyze(root: Path) -> dict:
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    stages, errors = [], []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not re.fullmatch(r"b\d+_(plain|traced)", folder.name):
            continue
        try:
            stages.append(stage_summary(folder))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"stage": folder.name, "error": repr(exc)})
    if state.get("status") != "completed":
        errors.append({"state": "Experiment is not marked completed"})
    restored = state.get("restored", {})
    if not restored.get("link_speed", "").startswith("8.0") or restored.get("link_width") != "1":
        errors.append({"restoration": "Expected restored 8.0 GT/s x1 is not recorded"})
    if state.get("recovery_errors"):
        errors.append({"recovery_errors": state["recovery_errors"]})
    for key in ("current_tracer", "tracing_on"):
        if state.get("trace_restored", {}).get(key) != config.get("trace_original", {}).get(key):
            errors.append({"trace_restoration_mismatch": key})
    expected_stages = {f"b{batch}_{kind}" for batch in range(4) for kind in ("plain", "traced")}
    if {stage["stage"] for stage in stages} != expected_stages:
        errors.append({"observed_stages": [stage["stage"] for stage in stages],
                       "expected_stages": sorted(expected_stages)})
    traced = [stage["trace"] for stage in stages if "trace" in stage]
    return {"schema": 2, "source": str(root.resolve()), "state": state, "config": config,
            "valid": not errors and all(not s["errors"] for s in stages),
            "ioctl_valid": len(stages) == 8 and all(s["ioctl_valid"] for s in stages),
            "root_trace_valid": len(traced) == 4 and all(t["root_trace_valid"] for t in traced),
            "nested_breakdown_available": len(traced) == 4 and all(t["nested_breakdown_available"] for t in traced),
            "errors": errors, "stages": stages,
            "notes": [
                "All reported time statistics are microseconds; raw samples retain nanoseconds.",
                "Plain ioctl duration includes syscall, copies, address lookup, MMIO, barriers and scheduling; it is not wire latency.",
                "Traced wrapper duration contains instrumentation and possible scheduling effects; keep it separate from untraced measurements.",
                "Do not subtract means from different trace/plain windows or different modes to infer pure software overhead.",
                "Only bm_get_reg minus its own observed nested bm_read32 is calculated, and only within the same traced call.",
                "Missing/inlined bm_read32 children make nested breakdown unavailable, but do not invalidate a complete root or ioctl measurement.",
                "Clock-pair baseline is reported independently and is not subtracted.",
                "CPU frequencies are before/after snapshots; no frequency locking or full temporal coverage is implied.",
                "Timer register response does not stand for all device registers, posted writes or DMA operations.",
                "The timer's 40 ns tick period does not mean a PCIe transaction takes 40 ns.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.directory)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(encoded)
        print(json.dumps({"output": str(args.output), "valid": result["valid"],
                          "stages": len(result["stages"])}))
    else:
        print(encoded, end="")
    if not result["valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
