#!/usr/bin/env python3
"""Validate and summarize an existing multi-device HDMI run without changing it."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def finite(value, label, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}: missing, non-finite or out of range")
    return value


def count(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}: expected nonnegative integer")
    return value


def read(path, hashes):
    data = path.read_bytes()
    hashes[path.parent.name + "/" + path.name if path.parent.name.startswith("device_") else path.name] = hashlib.sha256(data).hexdigest()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected object")
    return value


def summarize_worker(summary, config, expected_streams):
    if summary.get("status") != "measured" or summary.get("accounting_complete") is not True or summary.get("error"):
        raise ValueError("measurement unfinished, accounting incomplete or worker error")
    if config.get("inference_enabled") is not True:
        raise ValueError("this report requires an inference-enabled HDMI run")
    seconds = finite(summary.get("measured_seconds"), "measured_seconds", 1e-9)
    start = finite(summary.get("measurement_start_monotonic_s"), "measurement start")
    end = finite(summary.get("observed_measurement_end_monotonic_s"), "measurement end")
    requested = finite(config.get("duration_s"), "configured duration", 1e-9)
    if not math.isclose(end - start, seconds, abs_tol=.01) or not math.isclose(seconds, requested, abs_tol=.01):
        raise ValueError("formal duration does not match completed window / requested duration")
    streams = summary.get("streams")
    if not isinstance(streams, list) or any(not isinstance(s, dict) for s in streams) or len(streams) != expected_streams or config.get("streams") != expected_streams:
        raise ValueError("configured / observed channel count differs")
    ids = [s.get("stream_id") for s in streams]
    if any(type(i) is not int for i in ids) or set(ids) != set(range(expected_streams)):
        raise ValueError("duplicate or missing stream IDs")
    counts, previews, gaps, stale, overwrites, no_results = [], [], [], [], [], []
    for stream in streams:
        if stream.get("accounting_complete") is not True or stream.get("error"):
            raise ValueError(f"stream {stream.get('stream_id')}: incomplete accounting or error")
        completed = count(stream.get("completed_measured"), "completed_measured")
        preview = count(stream.get("previews_submitted_measured"), "previews_submitted_measured")
        if preview > completed:
            raise ValueError("preview count exceeds completed inferences; unsupported counting scope")
        counts.append(completed)
        previews.append(preview)
        if not math.isclose(finite(stream.get("completed_fps"), "stream FPS"), completed / seconds, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("stream FPS does not match formal count / seconds")
        continuity = stream.get("analysis_completion_continuity", {})
        if not isinstance(continuity, dict) or type(continuity.get("no_results")) is not bool:
            raise ValueError("missing continuity evidence")
        gap = finite(continuity.get("max_gap_including_edges_s"), "no-result gap")
        if gap > seconds + .01:
            raise ValueError("no-result gap exceeds measurement duration")
        gaps.append(gap)
        if not math.isclose(finite(continuity.get("seconds"), "continuity seconds"), seconds, abs_tol=.01):
            raise ValueError("continuity uses a different measurement window")
        if completed == 0 or continuity.get("no_results") is True:
            no_results.append(stream["stream_id"])
        stale.append(count(stream.get("dropped_stale"), "whole-run stale drops"))
        overwrites.append(count(stream.get("dropped_overwrite"), "whole-run overwrites"))
    fps = sum(counts) / seconds
    if not math.isclose(finite(summary.get("total_completed_fps"), "total FPS"), fps, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("total FPS does not match the sum of formal stream counts")
    return {"measurement_complete": True, "channels": expected_streams, "formal_seconds": seconds,
            "formal_start": start, "formal_end": end, "completed_inferences": sum(counts),
            "total_inference_fps": fps, "minimum_stream_inference_fps": min(counts) / seconds,
            "mean_preview_submission_fps_per_channel": sum(previews) / seconds / expected_streams,
            "max_no_result_gap_seconds": max(gaps), "channels_without_results": no_results,
            "stale_drops_whole_run": sum(stale), "overwritten_frames_whole_run": sum(overwrites),
            "configuration": {k: config.get(k) for k in ("device", "model", "decoder", "decoder_extra_buffers",
                "policy", "per_stream_preview_fps", "preview_max_fps", "preview_mode", "preview_thumbnail")}}


def summarize(directory):
    hashes, issues, warnings, devices = {}, [], [], {}
    run = read(directory / "run.json", hashes)
    selected = run.get("selected_devices")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 4 or any(type(d) is not int or d < 0 for d in selected) or len(set(selected)) != len(selected):
        raise ValueError("run.json must declare 1..4 unique selected devices")
    if run.get("status") != "stopped" or run.get("exit_status") != 0 or run.get("viewer_returncode") != 0:
        issues.append("supervisor / viewer has not completed successfully")
    if not isinstance(run.get("streams_by_device"), dict):
        raise ValueError("invalid streams_by_device mapping")
    if not isinstance(run.get("viewer_manifest", {}), dict):
        raise ValueError("invalid viewer manifest")
    workers = run.get("workers", [])
    if not isinstance(workers, list) or any(not isinstance(w, dict) for w in workers):
        raise ValueError("invalid worker list")
    if len(workers) != len(selected) or {w.get("device") for w in workers} != set(selected):
        issues.append("worker list differs from selected devices")
    for worker in workers:
        if worker.get("returncode") != 0 or worker.get("status") != "completed":
            issues.append(f"device {worker.get('device')}: worker did not complete successfully")
    try:
        telemetry = read(directory / "telemetry-summary.json", hashes).get("formal_measurement", {})
        if not isinstance(telemetry, dict) or not isinstance(telemetry.get("devices", {}), dict):
            raise ValueError("invalid formal TPU mapping")
    except (OSError, ValueError) as exc:
        telemetry = {}
        warnings.append(f"formal TPU unavailable: {exc}")
    for device in selected:
        folder = directory / f"device_{device}"
        try:
            expected = count(run.get("streams_by_device", {}).get(str(device)), "configured streams")
            if not 1 <= expected <= 32:
                raise ValueError("configured streams must be 1..32")
            config = read(folder / "config.json", hashes)
            if config.get("device") != device:
                raise ValueError("config device differs from selected device")
            row = summarize_worker(read(folder / "summary.json", hashes), config, expected)
            row["formal_tpu"] = None
            tpu = telemetry.get("devices", {}).get(str(device))
            if tpu:
                try:
                    if not isinstance(tpu, dict):
                        raise ValueError("invalid TPU sample object")
                    valid = count(tpu.get("valid_samples"), "TPU samples")
                    if valid == 0 or tpu.get("read_failures") != 0 or tpu.get("samples") != valid or tpu.get("worker_summary_status") != "measured" or tpu.get("accounting_complete") is not True:
                        raise ValueError("missing / failed samples or incomplete window")
                    if any(not math.isclose(finite(tpu.get(k), k), row[v], abs_tol=.01) for k, v in (("start_monotonic_s", "formal_start"), ("end_monotonic_s", "formal_end"))):
                        raise ValueError("TPU window differs from worker window")
                    mean, low, high = [finite(tpu.get(k), k) for k in ("mean_percent", "min_percent", "max_percent")]
                    if not 0 <= low <= mean <= high <= 100 or not math.isclose(finite(tpu.get("sum_percent"), "TPU sum") / valid, mean, abs_tol=1e-6):
                        raise ValueError("invalid TPU percentage range")
                    row["formal_tpu"] = {"samples": valid, "mean_percent": mean, "min_percent": low, "max_percent": high}
                except ValueError as exc:
                    warnings.append(f"device {device}: TPU unavailable ({exc})")
            else:
                warnings.append(f"device {device}: no formal TPU samples; not treated as 0%")
            if row["channels_without_results"]:
                issues.append(f"device {device}: channels without results {row['channels_without_results']}")
            if row["stale_drops_whole_run"]:
                warnings.append(f"device {device}: age-limit drops occurred (whole-run count)")
            devices[str(device)] = row
        except (OSError, ValueError, TypeError, KeyError) as exc:
            devices[str(device)] = {"measurement_complete": False, "error": str(exc)}
            issues.append(f"device {device}: {exc}")
    valid = not issues and len(devices) == len(selected)
    return {"run_id": directory.name, "display_fps": run.get("viewer_manifest", {}).get("fps"), "valid_measurement": valid, "issues": issues, "warnings": warnings,
            "devices": devices,
            "formal_tpu_complete": all(d.get("formal_tpu") is not None for d in devices.values()),
            "sum_of_device_mean_fps": sum(d["total_inference_fps"] for d in devices.values()) if valid else None,
            "scope": "Each device uses its own completed formal window. Sum of means is not a common-window count. Preview submissions are not screen refresh. Whole-run drops include warmup. No long-term or accuracy acceptance is inferred.",
            "source_sha256": hashes}


def markdown(result):
    lines = ["# HDMI 运行结果", "", f"运行：`{result['run_id']}`", "",
             "检测计数核验通过（不代表业务或长稳验收通过）。" if result["valid_measurement"] else "测量无效或不完整，不提供合计吞吐。", "",
             "正式窗口 TPU 遥测完整。" if result["formal_tpu_complete"] else "正式窗口 TPU 遥测不完整；缺失值不按 0% 处理。", "",
             "| 卡 | 总检测 FPS | 最慢单路 FPS | 平均每路预览提交 FPS | 最长无结果间隔(s) | TPU 正式均值 | 全运行年龄淘汰 | 全运行覆盖等待帧 |",
             "|---|---:|---:|---:|---:|---|---:|---:|"]
    for device, row in result["devices"].items():
        if not row["measurement_complete"]:
            lines.append(f"| {device} | 未测 | 未测 | 未测 | 未测 | 未测 | 未测 | 未测 |")
            continue
        tpu = row["formal_tpu"]
        tpu_text = f"{tpu['mean_percent']:.2f}% ({tpu['samples']} 次)" if tpu else "缺失"
        lines.append(f"| {device} | {row['total_inference_fps']:.2f} | {row['minimum_stream_inference_fps']:.2f} | {row['mean_preview_submission_fps_per_channel']:.2f} | {row['max_no_result_gap_seconds']:.3f} | {tpu_text} | {row['stale_drops_whole_run']} | {row['overwritten_frames_whole_run']} |")
    lines += ["", "各卡使用自身正式窗口；预览提交不等于屏幕显示帧率；年龄淘汰不包含所有丢帧类型。完整配置、来源校验和覆盖等待帧计数见 JSON。", ""]
    lines += ["- " + message for message in result["issues"] + result["warnings"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path, help="Directory containing run.json and device_N/config.json + summary.json")
    parser.add_argument("--output", type=Path, help="New report directory; never overwrite existing results")
    args = parser.parse_args(argv)
    try:
        result = summarize(args.run_directory)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=False)
            (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            (args.output / "report.md").write_text(markdown(result), encoding="utf-8")
        print(markdown(result))
        return 0 if result["valid_measurement"] else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Report unavailable: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
