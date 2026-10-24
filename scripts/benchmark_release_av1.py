#!/usr/bin/env python3
# 关键需求：main 下载并校验 Release，比较短片段 SVT-AV1 CRF/preset，报告回传原 Release。
# 待确认：READONLY/WRITEONLY 实际权限；原片的像素格式、HDR/旋转与时长。
# 后续研究：取得客观指标与主观审片后，再选择全片参数；避免仅以文件体积优化。
# 风险：公开仓库中的 Actions 日志可被读取；不得上传原片、样本、令牌或私有文件哈希。
# 验证重点：sha256、完整解码、画质度量、FFmpeg/SVT-AV1 版本、执行时间、剩余磁盘。
"""Benchmark AV1 clips and return only measurement reports to their source Release."""
import argparse
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
from urllib.parse import quote

import release_av1 as release

PRESETS = (6, 8)
CRFS = (24, 30, 36)
POSITIONS = (0.12, 0.50, 0.82)
SECONDS = 6.0


def command(args, timeout=1200, env=None):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode:
        raise RuntimeError(f"{args[0]} returned {proc.returncode}")
    return proc.stdout + "\n" + proc.stderr


def obtain_original(repo, tag, folder):
    metadata = release.api(f'repos/{repo}/releases/tags/{quote(tag, safe="")}')
    assets = []
    page = 1
    while True:
        batch = release.api(f'repos/{repo}/releases/{metadata["id"]}/assets?per_page=100&page={page}')
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    selected, split = release.source_assets(assets)
    total = sum(asset['size'] for asset in selected)
    if shutil.disk_usage(folder).free < total + 1_000_000_000:
        raise RuntimeError('Not enough disk space for original and short samples')
    expected = release.original_digest(repo, assets) if split else release.digest(selected[0])
    original = folder / 'source.mp4'
    checksum = hashlib.sha256()
    with original.open('x+b') as output:
        for index, asset in enumerate(selected, 1):
            print(f'Downloading and verifying input {index}/{len(selected)}', flush=True)
            release.download(repo, asset, output, checksum)
    if checksum.hexdigest() != expected:
        raise RuntimeError('Original SHA-256 mismatch')
    original.chmod(0o400)
    print(f'SHA-256 verified: {len(selected)} attachments, {total} original bytes', flush=True)
    return original, metadata['id']


def ff(tool, folder, *args, timeout=1200):
    environment = release.encoder_env()
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-url', required=True)
    args = parser.parse_args()
    repo, tag = release.release_target(args.release_url)
    folder = Path(os.environ["RUNNER_TEMP"]) / "av1-gh-cache-benchmark"
    folder.mkdir(parents=True, exist_ok=False)
    original, release_id = obtain_original(repo, tag, folder)
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
    if stream.get('field_order') not in (None, 'unknown', 'progressive'):
        raise RuntimeError('Interlaced benchmark input is not supported')
    duration = float(probe["format"]["duration"])
    if duration < 30:
        raise RuntimeError("Source too short for three distributed clips")
    records = []
    for scene, ratio in enumerate(POSITIONS, 1):
        start = min(max(0.0, ratio * duration), duration - SECONDS)
        # A single lossless reference fixes frame alignment and avoids repeated source seeks.
        reference = "/work/results/reference.mkv"
        ff("ffmpeg", folder, "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
           "-i", "/work/source.mp4", "-t", str(SECONDS), "-map", "0:v:0", "-an",
           "-c:v", "ffv1", "-pix_fmt", pix_fmt, "-fps_mode", "passthrough",
           "-map_metadata", "-1", "-y", reference)
        reference_probe = json.loads(ff("ffprobe", folder, "-v", "error", "-count_frames",
                                       "-show_streams", "-of", "json", reference))["streams"][0]
        for preset in PRESETS:
            for crf in CRFS:
                stem = f"s{scene}p{preset}c{crf}"
                output = f"/work/results/{stem}.mkv"
                begin = time.monotonic()
                ff("ffmpeg", folder, "-nostdin", "-nostats", "-v", "error",
                   "-i", reference, "-map", "0:v:0", "-an",
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
                actual = json.loads(ff("ffprobe", folder, "-v", "error", "-count_frames",
                                       "-show_streams", "-of", "json", output))["streams"][0]
                if actual.get('codec_name') != 'av1' or any(
                        actual.get(key) != reference_probe.get(key)
                        for key in ('width', 'height', 'pix_fmt', 'nb_read_frames')):
                    raise RuntimeError('Benchmark sample video/frame-count verification failed')
                inputs = ("-nostdin", "-nostats", "-v", "info", "-i", output,
                          "-i", reference)
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
        (folder / 'results' / 'reference.mkv').unlink()
    lines = [
        "## Private video AV1 sample benchmark", "",
        "- Input Release parts and merged original: SHA-256 verified",
        f"- Input bytes: {original.stat().st_size}",
        f"- FFmpeg: {version}",
        f"- Encoder: libsvtav1; pixel format: {pix_fmt}",
        f"- VMAF filter available: {vmaf_available}",
        f"- Sampling: {len(POSITIONS)} positions, {SECONDS:g} seconds each",
        "- Video-only benchmark; measurement reports returned to source Release; no samples uploaded", "",
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
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    if not run.isdecimal() or not attempt.isdecimal():
        raise RuntimeError('Invalid workflow identity')
    prefix = f'av1-benchmark-{run}-{attempt}'
    markdown = folder / 'results' / f'{prefix}.md'
    scores = folder / 'results' / f'{prefix}.json'
    markdown.write_text(report, encoding='utf-8')
    scores.write_text(json.dumps({'ffmpeg': version, 'image_id': os.environ['FF_IMAGE_ID'],
                                 'pixel_format': pix_fmt, 'records': records}, indent=2) + '\n',
                      encoding='utf-8')
    uploaded = [release.publish(repo, tag, path) for path in (markdown, scores)]
    release.verify_uploaded(repo, release_id, uploaded)
    print('Benchmark reports returned to source Release; sizes and GitHub SHA-256 verified', flush=True)
    shutil.rmtree(folder)  # This run created the directory exclusively; all reports are verified remotely.


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"::error::Benchmark failed ({type(error).__name__}); private error details suppressed", file=sys.stderr)
        sys.exit(1)

