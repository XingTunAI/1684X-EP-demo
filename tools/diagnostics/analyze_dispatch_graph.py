#!/usr/bin/env python3
"""Decompose complete function_graph roots without adding nested durations twice.

Input must include absolute timestamps and TASK/PID (funcgraph-abstime and
funcgraph-proc). Stacks are keyed by TID, not CPU. A truncated/corrupt root is
discarded; a depth-limited trace can still have a complete root, but omitted
children remain unobserved, not evidence of zero cost. All durations are traced
wall-clock call durations, affected by tracer overhead and sleep-time settings.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import statistics
from typing import Iterable


DEFAULT_ROOTS = (
    "bm1686_trigger_vpp", "bmdev_memcpy_s2d", "bmdev_memcpy_d2s",
    "bmdrv_send_api", "bmdrv_thread_sync_api",
)
CDMA_NAMES = {"bm1684_cdma_transfer", "bm1682_cdma_transfer"}
MUTEX_NAMES = {"mutex_lock", "mutex_lock_interruptible", "mutex_lock_killable"}
COMPLETION_NAMES = {
    "schedule_timeout", "schedule_timeout_interruptible",
    "schedule_timeout_uninterruptible", "wait_for_completion_timeout",
    "wait_for_completion_interruptible_timeout", "wait_for_completion",
    "wait_for_completion_interruptible",
}
DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|us|ms|s)\b")
IDENTITY = re.compile(r"\s*(\d+)\)\s*(.*?)\s*-(\d+)\s*$")
CALL = re.compile(r"^([\w.$]+)(?:\s+\[[^]]+\])?\(\)\s*([;{])")
RETURN = re.compile(r"^}\s*(?:/\*\s*([\w.$]+)(?:\s+\[[^]]+\])?\s*\*/)?")


@dataclass
class Node:
    name: str
    tid: int
    indent: int
    start_line: int
    start_timestamp: str
    start_raw: str
    cpus: set[int] = field(default_factory=set)
    children: list["Node"] = field(default_factory=list)
    return_line: int | None = None
    return_timestamp: str | None = None
    return_raw: str | None = None
    duration_us: float | None = None
    leaf: bool = False


def _stage(nodes: list[Node], kind: str) -> dict:
    """Select outermost matching children; descend only through unselected nodes."""
    groups: dict[str, list[Node]] = {}
    child_kinds: dict[str, str] = {}

    def visit(node: Node, direct: bool = False) -> None:
        name, category, child_kind = node.name, None, "generic"
        if kind == "vpp":
            if name == "down_interruptible":
                category = "vpp_admission"
            elif name == "bmdev_memcpy_s2d_internal":
                category, child_kind = "descriptor_upload", "dma"
            elif name in COMPLETION_NAMES:
                category = "vpp_completion_wait"
        elif kind == "dma":
            if name == "bmdrv_get_stagemem":
                category, child_kind = "stage_buffer_acquire", "stage"
            elif name in CDMA_NAMES:
                category, child_kind = "cdma_call", "cdma"
            elif direct and name in MUTEX_NAMES:
                # The IOMMU branch locks at the memcpy root, not inside CDMA.
                category = "direct_mutex_calls"
        elif kind == "cdma":
            if direct and name in MUTEX_NAMES:
                # Includes every direct mutex, not a guessed mutex address.
                category = "direct_mutex_calls"
            elif name in COMPLETION_NAMES:
                category = "completion_wait"
        elif kind == "stage":
            if name in MUTEX_NAMES:
                category = "mutex_calls"
            elif name in COMPLETION_NAMES:
                category = "buffer_completion_wait"
        elif kind == "submit":
            if direct and name in MUTEX_NAMES:
                category = "direct_submission_mutex_calls"
            elif name == "bmdev_wait_msgfifo":
                category = "fifo_space_check_wait"
            elif name == "bmdev_copy_to_msgfifo":
                category = "message_copy"
        elif kind == "sync":
            if direct and name in MUTEX_NAMES:
                category = "direct_mutex_calls"
            elif name in COMPLETION_NAMES:
                category = "completion_wait"
        if category:
            groups.setdefault(category, []).append(node)
            child_kinds[category] = child_kind
        else:
            for child in node.children:
                visit(child)

    for parent in nodes:
        for child in parent.children:
            visit(child, direct=True)
    total = sum(node.duration_us or 0.0 for node in nodes)
    children = {name: _stage(matches, child_kinds[name])
                for name, matches in groups.items()}
    # Do not clamp: a negative remainder is useful evidence of bad/incompatible
    # graph timing, rather than silently inventing an exclusive duration.
    remainder = total - sum(child["inclusive_us"] for child in children.values())
    return {
        "inclusive_us": total,
        "matched_calls": len(nodes),
        "function_names": sorted({node.name for node in nodes}),
        "children": children,
        "unclassified_remainder_us": remainder,
    }


def _root_record(node: Node) -> dict:
    kinds = {"bm1686_trigger_vpp": "vpp", "bmdev_memcpy_s2d": "dma",
             "bmdev_memcpy_d2s": "dma", "bmdrv_send_api": "submit",
             "bmdrv_thread_sync_api": "sync"}
    stage = _stage([node], kinds.get(node.name, "generic"))
    function_values: dict[str, list[float]] = {}
    def collect(current: Node) -> None:
        function_values.setdefault(current.name, []).append(current.duration_us or 0.0)
        for child in current.children:
            collect(child)
    collect(node)
    return {
        "root": node.name, "tid": node.tid, "cpus": sorted(node.cpus),
        "start_line": node.start_line, "return_line": node.return_line,
        "start_timestamp": node.start_timestamp,
        "return_timestamp": node.return_timestamp,
        "start_raw": node.start_raw, "return_raw": node.return_raw,
        "leaf": node.leaf,
        "duration_us": node.duration_us, "stages": stage,
        "inclusive_function_stats": {
            name: {"count": len(values), "sum_us": sum(values),
                   "mean_us": statistics.fmean(values), "max_us": max(values)}
            for name, values in sorted(function_values.items())},
    }


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    def percentile(q: float) -> float:
        index = (len(ordered) - 1) * q
        low = int(index)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (index - low)
    return {
        "count": len(values), "sum_us": sum(values),
        "mean_us": statistics.fmean(values), "p50_us": percentile(.50),
        "p95_us": percentile(.95), "max_us": max(values),
    }


def _summarize_stages(stages: list[dict]) -> dict:
    """Children absent from some roots are reported as zero observed time only."""
    names = sorted({name for stage in stages for name in stage["children"]})
    empty = {"inclusive_us": 0.0, "matched_calls": 0, "function_names": [],
             "children": {}, "unclassified_remainder_us": 0.0}
    return {
        "inclusive_us": _stats([s["inclusive_us"] for s in stages]),
        "matched_calls": sum(s["matched_calls"] for s in stages),
        "roots_with_observed_calls": sum(s["matched_calls"] > 0 for s in stages),
        "function_names": sorted({n for s in stages for n in s["function_names"]}),
        "children": {name: _summarize_stages(
            [stage["children"].get(name, empty) for stage in stages]) for name in names},
        "unclassified_remainder_us": _stats(
            [s["unclassified_remainder_us"] for s in stages]),
    }


def _summarize_functions(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        for name, values in row["inclusive_function_stats"].items():
            groups.setdefault(name, []).append(values)
    result = {}
    for name, values in sorted(groups.items()):
        count = sum(value["count"] for value in values)
        total = sum(value["sum_us"] for value in values)
        result[name] = {"count": count, "sum_us": total, "mean_us": total / count,
                        "max_us": max(value["max_us"] for value in values),
                        "roots_with_observed_calls": len(values)}
    return result


def parse_trace(trace: str | Iterable[str] | Path,
                roots: Iterable[str] | None = None) -> dict:
    """Parse trace text, a lines iterable, or a Path into JSON-compatible data."""
    if isinstance(trace, Path):
        lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()
    elif isinstance(trace, str):
        lines = trace.splitlines()
    else:
        lines = trace
    selected = set(DEFAULT_ROOTS if roots is None else roots)
    stacks: dict[int, list[Node]] = {}
    calls, issues = [], []
    counters: Counter = Counter()

    def issue(kind: str, line: int, tid: int | None = None, detail: str = "") -> None:
        counters[kind] += 1
        issues.append({"kind": kind, "line": line, "tid": tid, "detail": detail})

    def discard(tid: int, line: int, reason: str) -> None:
        stack = stacks.pop(tid, [])
        for node in stack:
            if node.name in selected:
                issue("discarded_incomplete_roots", line, tid,
                      f"{node.name} started at line {node.start_line}: {reason}")

    def finish(node: Node, stack: list[Node]) -> None:
        if stack:
            stack[-1].children.append(node)
        # Only the outermost selected root is emitted: nested selected roots
        # belong to its decomposition, not a second independent wall-time sum.
        if node.name in selected and not any(n.name in selected for n in stack):
            calls.append(_root_record(node))

    line_no = 0
    for line_no, raw in enumerate(lines, 1):
        if re.search(r"\bLOST\s+\d+\s+EVENTS\b", raw, re.I):
            issue("lost_event_markers", line_no, detail=raw.strip())
            for tid in list(stacks):
                discard(tid, line_no, "lost events")
            continue
        parts = raw.split("|", 3)
        if len(parts) != 4:
            continue
        stamp, identity, duration, function = parts
        stamp = stamp.strip()
        match = IDENTITY.fullmatch(identity)
        if not match or not re.fullmatch(r"\d+(?:\.\d+)?", stamp):
            continue
        cpu, _, tid = int(match[1]), match[2], int(match[3])
        body = function.strip()
        indent = len(function) - len(function.lstrip())
        entry, closing = CALL.match(body), RETURN.match(body)
        if not entry and not closing:
            continue
        counters["function_records"] += 1
        dmatch = DURATION.search(duration)
        elapsed = (float(dmatch[1]) * {"ns": .001, "us": 1, "ms": 1000,
                                     "s": 1_000_000}[dmatch[2]]) if dmatch else None
        stack = stacks.setdefault(tid, [])
        for node in stack:
            node.cpus.add(cpu)
        if entry:
            if stack and indent <= stack[-1].indent:
                issue("stack_discontinuities", line_no, tid, body)
                discard(tid, line_no, "entry indentation lost parent return")
                stack = stacks.setdefault(tid, [])
            node = Node(entry[1], tid, indent, line_no, stamp, raw, {cpu})
            if entry[2] == "{":
                stack.append(node)
            elif elapsed is None:
                issue("missing_durations", line_no, tid, body)
                discard(tid, line_no, "leaf has no duration")
            else:
                node.leaf, node.duration_us = True, elapsed
                # A leaf is a single entry/duration record: there is no separate
                # observed return timestamp. Do not fabricate one.
                finish(node, stack)
            continue
        if not stack:
            issue("orphan_returns", line_no, tid, body)
            continue
        if indent != stack[-1].indent or (closing[1] and closing[1] != stack[-1].name):
            issue("mismatched_returns", line_no, tid, body)
            discard(tid, line_no, "return does not match open function")
            continue
        node = stack.pop()
        if elapsed is None:
            issue("missing_durations", line_no, tid, body)
            # node was popped, so count it explicitly if it is a selected root.
            if node.name in selected:
                issue("discarded_incomplete_roots", line_no, tid, node.name)
            discard(tid, line_no, "return has no duration")
            continue
        node.duration_us = elapsed
        node.return_line, node.return_timestamp, node.return_raw = line_no, stamp, raw
        finish(node, stack)

    counters["unclosed_functions"] = sum(len(stack) for stack in stacks.values())
    for tid in list(stacks):
        discard(tid, line_no, "end of trace")
    calls.sort(key=lambda call: call["start_line"])
    by_root = {name: [c for c in calls if c["root"] == name] for name in sorted(selected)}
    for name, rows in by_root.items():
        if not rows:
            continue
        for row in rows:
            def check(stage: dict) -> bool:
                return (stage["unclassified_remainder_us"] < -1.0 or
                        any(check(c) for c in stage["children"].values()))
            if check(row["stages"]):
                issue("negative_remainders", row["start_line"], row["tid"], name)
    return {
        "schema_version": 1,
        "units": "microseconds",
        "roots": sorted(selected),
        "notes": [
            "Stage durations are inclusive. Children are subsets; never add a parent to its children.",
            "inclusive_function_stats includes all observed nested calls and the root itself; it is an overlapping inventory, NOT an additive time decomposition.",
            "At each level, sibling stages plus unclassified remainder equal the parent duration.",
            "Durations sum overlapping thread time, not elapsed trace time or resource utilization.",
            "Only complete outermost selected roots are counted; trace-edge calls are discarded.",
            "Missing children mean unobserved at the configured graph depth, not proven zero cost.",
            "Mutex calls include acquisition overhead and possible sleep; no lock address is inferred.",
            "Completion waits include device execution, interrupt/wakeup and scheduling, not only contention.",
            "CDMA remainder includes register I/O, polling and software; it is not pure link transfer time.",
            "Tracer overhead and the configured sleep-time option affect all reported durations.",
        ],
        "diagnostics": {**dict(counters), "complete_roots": len(calls), "issues": issues},
        "summary": {name: {"count": len(rows),
                           "stages": _summarize_stages([c["stages"] for c in rows]) if rows else None,
                           "inclusive_function_stats": _summarize_functions(rows)}
                    for name, rows in by_root.items()},
        "calls": calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS),
                        help="Root symbols, separated by spaces or commas")
    parser.add_argument("--output", type=Path, help="JSON output; defaults to stdout")
    args = parser.parse_args()
    roots = [part for arg in args.roots for part in arg.split(",") if part]
    result = parse_trace(args.trace, roots)
    result["source"] = str(args.trace.resolve())
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output),
                          "counts": {k: v["count"] for k, v in result["summary"].items()},
                          "discarded_incomplete_roots": result["diagnostics"].get(
                              "discarded_incomplete_roots", 0)}))
    else:
        print(encoded)


if __name__ == "__main__":
    main()
