#!/usr/bin/env python3
# 关键需求：只读下载私有 Release，校验分片及原文件，比较短片段的 SVT-AV1 CRF/preset。
# 待确认：readonly 是否为 Actions Secret；原片的实际像素格式、HDR/旋转与时长。
# 后续研究：取得客观指标与主观审片后，再选择全片参数；避免仅以文件体积优化。
# 风险：公开仓库中的 Actions 日志可被读取；不得上传原片、样本、令牌或私有文件哈希。
# 验证重点：sha256、完整解码、画质度量、FFmpeg/SVT-AV1 版本、执行时间、剩余磁盘。
"""Ephemeral, nonpublishing AV1 benchmark for a private GitHub Release."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = "Grinch27/gh-cache"
TAG = "test-media-20261024-liuying"
PRESETS = (6, 8)
CRFS = (24, 30, 36)
POSITIONS = (0.12, 0.50, 0.82)
SECONDS = 6.0


def command(args, timeout=1200, env=None):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode:
        raise RuntimeError(f"{args[0]} returned {proc.returncode}")
    return proc.stdout + "\n" + proc.stderr


def get_asset(filename, folder):
    command(["gh", "release", "download", TAG, "--repo", REPO,
             "--dir", str(folder), "--pattern", filename], timeout=1800)
    path = folder / filename
    if not path.is_file():
        raise RuntimeError("Expected Release attachment was not downloaded")
    return path


def obtain_original(folder):
    if not os.environ.get("GH_TOKEN"):
        raise RuntimeError("Missing Actions Secret readonly / GH_CACHE_TOKEN")
    try:
        release = json.loads(command(["gh", "api", f"repos/{REPO}/releases/tags/{TAG}"]))
    except (RuntimeError, ValueError) as error:
        raise RuntimeError("Private Release permission check failed; Contents: read is required") from error
    parts = []
    for asset in release["assets"]:
        matched = re.search(r"\.part(\d{3,})$", asset["name"])
        if matched:
            parts.append((int(matched.group(1)), asset))
    parts.sort(key=lambda value: value[0])
    if len(parts) < 2 or [number for number, _ in parts] != list(range(1, len(parts) + 1)):
        raise RuntimeError("Release multipart attachment sequence is invalid")
    total = sum(asset["size"] for _, asset in parts)
    biggest = max(asset["size"] for _, asset in parts)
    if shutil.disk_usage(folder).free < total + biggest + 1_000_000_000:
        raise RuntimeError("Not enough disk for original MP4 and one download part")
    checksums = get_asset("ORIGINAL_SHA256SUMS", folder).read_text(encoding="utf-8")
    match = re.match(r"\s*([0-9a-fA-F]{64})\s+", checksums)
    if not match:
        raise RuntimeError("Missing original SHA-256 record")
    original = folder / "source.mp4"
    merged_hash = hashlib.sha256()
    with original.open("xb") as output:
        for number, asset in parts:
            part = get_asset(asset["name"], folder)
            digest = hashlib.sha256()
            size = 0
            with part.open("rb") as source:
                while chunk := source.read(4 * 1024 * 1024):
                    digest.update(chunk)
                    merged_hash.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
            if (size != asset["size"] or
                    asset.get("digest") != "sha256:" + digest.hexdigest()):
                raise RuntimeError(f"Release attachment part {number} checksum mismatch")
            part.unlink()
    if original.stat().st_size != total or merged_hash.hexdigest() != match.group(1).lower():
        raise RuntimeError("Merged original SHA-256 mismatch")
    original.chmod(0o400)
    print(f"SHA-256 verified: {len(parts)} attachments, {total} original bytes")
    return original


def ff(tool, folder, *args, timeout=1200):
    environment = dict(os.environ)
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    docker = [
        "docker", "run", "--rm", "--init", "--pull=never", "--network=none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--mount", f"type=bind,source={folder},target=/work",
        "--workdir", "/work", "--entrypoint", tool, os.environ["FF_IMAGE_ID"], *args
    ]
    return command(docker, timeout=timeout, env=environment)


def main():
    folder = Path(os.environ["RUNNER_TEMP"]) / "av1-gh-cache-benchmark"
    folder.mkdir(parents=True, exist_ok=False)
    original = obtain_original(folder)
    os.environ.pop("GH_TOKEN", None)
    (folder / "results").mkdir()
    version = ff("ffmpeg", folder, "-version").splitlines()[0]
    if "libsvtav1" not in ff("ffmpeg", folder, "-encoders"):
        raise RuntimeError("Pinned FFmpeg image is missing libsvtav1")
    vmaf_available = "libvmaf" in ff("ffmpeg", folder, "-filters")
    probe = json.loads(ff("ffprobe", folder, "-v", "error", "-show_format",
                          "-show_streams", "-of", "json", "/work/source.mp4"))
    videos = [item for item in probe["streams"] if item["codec_type"] == "video"
              and not item.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise RuntimeError("Exactly one primary video stream is supported")
    stream = videos[0]
    pix_fmt = stream.get("pix_fmt")
    if pix_fmt not in ("yuv420p", "yuv420p10le"):
        raise RuntimeError("Benchmark currently requires SDR 4:2:0 8/10-bit video")
    if (stream.get("color_transfer") in ("smpte2084", "arib-std-b67") or
            stream.get("color_primaries") == "bt2020" or stream.get("side_data_list")):
        raise RuntimeError("HDR/rotation/side-data requires a separate preservation policy")
    duration = float(probe["format"]["duration"])
    if duration < 30:
        raise RuntimeError("Source too short for three distributed clips")
    records = []
    for scene, ratio in enumerate(POSITIONS, 1):
        start = min(max(0.0, ratio * duration), duration - SECONDS)
        for preset in PRESETS:
            for crf in CRFS:
                stem = f"s{scene}p{preset}c{crf}"
                output = f"/work/results/{stem}.mkv"
                begin = time.monotonic()
                ff("ffmpeg", folder, "-nostdin", "-nostats", "-v", "error",
                   "-ss", f"{start:.3f}", "-i", "/work/source.mp4",
                   "-t", str(SECONDS), "-map", "0:v:0", "-an",
                   "-c:v", "libsvtav1", "-crf", str(crf), "-preset", str(preset),
                   "-svtav1-params", "lp=4", "-pix_fmt", pix_fmt,
                   "-fps_mode", "passthrough", "-map_metadata", "-1",
                   "-y", output, timeout=1200)
                elapsed = time.monotonic() - begin
                encoded = folder / "results" / (stem + ".mkv")
                if not encoded.exists() or not encoded.stat().st_size:
                    raise RuntimeError("AV1 sample missing or empty")
                size = encoded.stat().st_size
                ff("ffmpeg", folder, "-v", "error", "-xerror", "-i", output,
                   "-map", "0:v:0", "-f", "null", "-", timeout=300)
                inputs = ("-nostdin", "-nostats", "-v", "info", "-i", output,
                          "-ss", f"{start:.3f}", "-t", str(SECONDS),
                          "-i", "/work/source.mp4")
                pair = ("[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];"
                        "[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]")
                log = ff("ffmpeg", folder, *inputs, "-filter_complex", pair + "ssim",
                         "-an", "-f", "null", "-", timeout=600)
                match = re.search(r"SSIM\s+.*?All:([0-9.]+)", log)
                if not match:
                    raise RuntimeError("SSIM unavailable for encoded sample")
                vmaf = None
                if vmaf_available:
                    score_file = folder / "results" / (stem + ".json")
                    try:
                        ff("ffmpeg", folder, *inputs, "-filter_complex",
                           pair + f"libvmaf=log_fmt=json:log_path=/work/results/{stem}.json",
                           "-an", "-f", "null", "-", timeout=600)
                        score_json = json.loads(score_file.read_text(encoding="utf-8"))
                        vmaf = float(score_json["pooled_metrics"]["vmaf"]["mean"])
                    except (RuntimeError, KeyError, ValueError, OSError, TypeError):
                        print(f"VMAF unavailable in scene {scene}, preset {preset}, CRF {crf}")
                    score_file.unlink(missing_ok=True)
                records.append(dict(scene=scene, preset=preset, crf=crf,
                                    bytes=size, seconds=elapsed,
                                    ssim=float(match.group(1)), vmaf=vmaf))
                encoded.unlink()
                print(f"Benchmarked scene={scene} preset={preset} CRF={crf}")
    lines = [
        "## Private video AV1 sample benchmark", "",
        "- Input Release parts and merged original: SHA-256 verified",
        f"- Input bytes: {original.stat().st_size}",
        f"- FFmpeg: {version}",
        f"- Encoder: libsvtav1; pixel format: {pix_fmt}",
        f"- VMAF filter available: {vmaf_available}",
        f"- Sampling: {len(POSITIONS)} positions, {SECONDS:g} seconds each",
        "- Video-only benchmark; no media, secrets or source hashes uploaded", "",
        "| CRF | Preset | Mean clip bytes | Mean SSIM | Mean VMAF | Mean encode seconds |",
        "|---:|---:|---:|---:|---:|---:|"
    ]
    for preset in PRESETS:
        for crf in CRFS:
            group = [r for r in records if r["preset"] == preset and r["crf"] == crf]
            scores = [r["vmaf"] for r in group if r["vmaf"] is not None]
            vmaf = f"{statistics.mean(scores):.2f}" if len(scores) == len(group) else "N/A"
            lines.append(f"| {crf} | {preset} | {statistics.mean(r['bytes'] for r in group):.0f} | "
                         f"{statistics.mean(r['ssim'] for r in group):.6f} | {vmaf} | "
                         f"{statistics.mean(r['seconds'] for r in group):.1f} |")
    lines += ["", "These are sample measurements, not full-film bitrate predictions. "
              "A visual quality decision and full encode verification are still required.", ""]
    report = "\n".join(lines)
    print(report)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(report)
    (folder / "results" / "scores.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"::error::{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
