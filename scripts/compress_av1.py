#!/usr/bin/env python3
# 关键需求：保留 OneDrive 全片 AV1 兼容；Release 单次只压缩开头 1200 帧并记录完整 FFmpeg 日志。
# 待确认：视频帧时基、AAC 音轨尾部时长和主观画质；1200 帧输出不能用于计算整部影片压缩率。
# 后续研究：读取 encode.log/progress.log 后分析速度、码率、内存、警告；HDR/字幕/非 AAC 另行研究。
# 风险：有损重编码可能增大体积；latest 会变化，记录本次镜像 ID/digest；不覆盖原文件。
# 验证重点：输出恰好 1200 帧、时长、像素格式、显示比例、AAC 流、解码与详细日志；失败不发布。
"""Standard-library AV1 compression and verification; requires ffmpeg/ffprobe."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


MEDIA_PREFIX = []


def media_command(tool, *arguments):
    """Use the same pulled image for probing, encoding, and decoding."""
    if MEDIA_PREFIX:
        return [*MEDIA_PREFIX, "--entrypoint", tool, os.environ["FFMPEG_DOCKER_IMAGE"], *arguments]
    return [tool, *arguments]


def probe(path, *, count_frames=True):
    options = ("-count_frames",) if count_frames else ()
    return json.loads(subprocess.check_output(media_command(
        "ffprobe", "-v", "error", *options, "-show_format",
        "-show_streams", "-of", "json", str(path),
    ), text=True))


def video(info):
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def audio(info):
    return [s for s in info["streams"] if s["codec_type"] == "audio"]


def ratio(value):
    return Fraction(value.replace(":", "/"))


def main():
    global MEDIA_PREFIX
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--crf", type=int, choices=range(1, 41), default=30)
    parser.add_argument("--preset", type=int, choices=range(4, 10), default=6)
    parser.add_argument("--threads", type=int, choices=range(1, 33), default=4)
    parser.add_argument("--max-frames", type=int, default=None,
                        help="Limit output to first N decoded frames; omit for full input")
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames must be positive")
    # A new directory prevents accidentally mixing stale outputs with a failed run.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"status": "failed", "crf": args.crf, "preset": args.preset,
              "frame_limit": args.max_frames}
    partial = args.output_dir / "encoding.partial.mp4"
    target = args.output_dir / "output_av1.mp4"
    try:
        if os.environ.get("FFMPEG_DOCKER_IMAGE"):
            workspace = Path.cwd().resolve()
            output_dir = args.output_dir.resolve()
            if not output_dir.is_relative_to(workspace) or not args.input.resolve().is_relative_to(workspace):
                raise ValueError("Docker input/output must be inside the current workspace")
            MEDIA_PREFIX = [
                "docker", "run", "--rm", "--init", "--pull=never", "--network=none",
                "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,nosuid,nodev",
                "--mount", f"type=bind,source={workspace},target={workspace},readonly",
                "--mount", f"type=bind,source={output_dir},target={output_dir}",
                "--workdir", str(workspace),
            ]
            report["docker_image_id"] = os.environ["FFMPEG_DOCKER_IMAGE"]
            metadata = os.environ.get("FFMPEG_IMAGE_METADATA")
            if metadata:
                report["docker_image"] = json.loads(Path(metadata).read_text())
        if not args.input.is_file() or args.input.stat().st_size == 0:
            raise ValueError("Input is missing or empty")
        required_disk = (1_000_000_000 if args.max_frames else args.input.stat().st_size * 3)
        if shutil.disk_usage(args.output_dir).free < required_disk:
            raise ValueError("Insufficient free disk space for selected encoding scope")
        source = probe(args.input, count_frames=args.max_frames is None)
        streams = source["streams"]
        if sum(s["codec_type"] == "video" for s in streams) != 1:
            raise ValueError("Exactly one video stream is required")
        if any(s["codec_type"] not in ("video", "audio") for s in streams):
            raise ValueError("Subtitle/data/attachment streams require an explicit preservation policy")
        v = video(source)
        if v.get("disposition", {}).get("attached_pic"):
            raise ValueError("Attached picture is not a video input")
        if (v.get("color_transfer") in ("smpte2084", "arib-std-b67")
                or v.get("color_primaries") == "bt2020" or v.get("side_data_list")):
            raise ValueError("HDR/rotation/side-data input requires a dedicated preservation policy")
        if v.get("pix_fmt") not in ("yuv420p", "yuv420p10le"):
            raise ValueError("Only SDR yuv420p/yuv420p10le input is supported")
        if v.get("field_order") not in (None, "unknown", "progressive"):
            raise ValueError("Interlaced input is not supported")
        if any(s["codec_name"] != "aac" for s in audio(source)):
            raise ValueError("Only AAC audio copy is supported; refusing implicit audio conversion")
        encoders = subprocess.check_output(media_command("ffmpeg", "-hide_banner", "-encoders"), text=True)
        if "libsvtav1" not in encoders:
            raise ValueError("FFmpeg does not include libsvtav1")
        report["input_pixel_format"] = v["pix_fmt"]
        report["output_pixel_format"] = v["pix_fmt"]
        report["ffmpeg_version"] = subprocess.check_output(media_command("ffmpeg", "-version"), text=True)
        progress_path = args.output_dir / "progress.log"
        limited = args.max_frames is not None
        limit_options = (["-frames:v", str(args.max_frames), "-shortest"] if limited else [])
        command = media_command(
            "ffmpeg", "-hide_banner", "-nostdin", "-n",
            "-loglevel", "verbose", "-stats_period", "1",
            "-progress", str(progress_path), "-benchmark",
            "-i", str(args.input), "-map", "0:v:0", "-map", "0:a?",
            "-map_metadata", "0", "-map_chapters", "-1" if limited else "0",
            "-c:v", "libsvtav1", "-crf", str(args.crf), "-preset", str(args.preset),
            "-svtav1-params", f"lp={args.threads}", "-pix_fmt", v["pix_fmt"],
            "-fps_mode", "passthrough", *limit_options, "-c:a", "copy",
            "-tag:v", "av01", "-movflags", "+faststart", str(partial),
        )
        started = time.monotonic()
        with (args.output_dir / "encode.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        elapsed = time.monotonic() - started
        encoder_log = (args.output_dir / "encode.log").read_text(errors="replace")
        version_lines = [line for line in encoder_log.splitlines() if re.search(r"SVT.*(?:version|Encoder Lib)", line, re.I)]
        report["svtav1_version"] = "\n".join(version_lines) or "See encode.log (version banner not recognized)"
        result = probe(partial)
        out = video(result)
        if out["codec_name"] != "av1":
            raise ValueError("Output video is not AV1")
        for field in ("width", "height", "pix_fmt"):
            if not v.get(field) or v[field] != out.get(field):
                raise ValueError(f"Video verification failed: {field}")
        if args.max_frames is not None:
            if out.get("nb_read_frames") != str(args.max_frames):
                raise ValueError("Output frame count differs from requested prefix frame limit")
        elif not v.get("nb_read_frames") or v["nb_read_frames"] != out.get("nb_read_frames"):
            raise ValueError("Full video frame count mismatch")
        rate_fields = (("sample_aspect_ratio",) if args.max_frames is not None
                       else ("sample_aspect_ratio", "avg_frame_rate"))
        for field in rate_fields:
            if ratio(v.get(field, "1:1")) != ratio(out.get(field, "1:1")):
                raise ValueError(f"Video verification failed: {field}")
        source_duration = float(source["format"]["duration"])
        output_duration = float(result["format"]["duration"])
        if args.max_frames is not None:
            if output_duration <= 0 or output_duration > source_duration + 0.1:
                raise ValueError("Encoded prefix duration is out of source range")
        elif abs(source_duration - output_duration) > 0.1:
            raise ValueError("Output duration differs by more than 0.1 seconds")
        source_audio, output_audio = audio(source), audio(result)
        fields = (("codec_name", "channels", "sample_rate")
                  if args.max_frames is not None
                  else ("codec_name", "channels", "sample_rate", "nb_read_frames"))
        if [[s.get(k) for k in fields] for s in source_audio] != [
                [s.get(k) for k in fields] for s in output_audio]:
            raise ValueError("Audio stream verification failed")
        with (args.output_dir / "decode.log").open("w") as log:
            subprocess.run(media_command("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror",
                            "-i", str(partial), "-map", "0:v", "-map", "0:a?", "-f", "null", "-"),
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        size_in, size_out = args.input.stat().st_size, partial.stat().st_size
        report.update(status="success", source_file_bytes=size_in, output_bytes=size_out,
                      output_duration_seconds=output_duration,
                      encoded_video_frames=int(out["nb_read_frames"]),
                      encode_seconds=elapsed, encode_fps=int(out["nb_read_frames"]) / elapsed,
                      log_files=["encode.log", "progress.log", "decode.log"],
                      input_probe=source, output_probe=result,
                      quality="Lossy; subjective quality not reviewed",
                      validation="AV1, frame count, pixel format, SAR, audio, duration, full decode")
        if args.max_frames is None:
            report.update(input_bytes=size_in, output_percent=100 * size_out / size_in,
                          saved_percent=100 * (1 - size_out / size_in))
        partial.rename(target)
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        lines = ["# AV1 compression report", "", f"- Status: {report['status']}",
                 f"- CRF: {args.crf}; preset: {args.preset}",
                 f"- Frame limit: {args.max_frames if args.max_frames else 'full video'}",
                 f"- Input pixel format: {report.get('input_pixel_format', 'unknown')}",
                 f"- Output pixel format: {report.get('output_pixel_format', 'unknown')}"]
        if "docker_image_id" in report:
            lines += [f"- Docker image ID: {report['docker_image_id']}"]
        if "ffmpeg_version" in report:
            lines += [f"- FFmpeg: {report['ffmpeg_version'].splitlines()[0]}"]
        if "svtav1_version" in report:
            lines += [f"- SVT-AV1: {report['svtav1_version']}"]
        if report["status"] == "success":
            lines += [f"- Original source file: {report['source_file_bytes']} bytes",
                      f"- Output: {report['output_bytes']} bytes",
                      f"- Frames: {report['encoded_video_frames']}",
                      f"- Duration: {report['output_duration_seconds']:.3f} s",
                      f"- Encode: {report['encode_seconds']:.2f} s; {report['encode_fps']:.2f} fps",
                      "- Logs: encode.log, progress.log, decode.log",
                      f"- Validation: {report['validation']}", f"- Quality: {report['quality']}"]
            if args.max_frames is None:
                lines += [f"- Output/input: {report['output_percent']:.2f}%",
                          f"- Saved: {report['saved_percent']:.2f}%"]
            else:
                lines += ["- Prefix output cannot be compared to full source file size"]
        else:
            lines += [f"- Error: {report.get('error', 'Unknown failure')}"]
        markdown = "\n".join(lines) + "\n"
        (args.output_dir / "report.md").write_text(markdown)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(markdown)
        print(markdown)


if __name__ == "__main__":
    main()
