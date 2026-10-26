#!/usr/bin/env python3
# 需求：原片前 1200 帧的单组 AV1 压缩；回传私有 Release MP4、完整编码日志、进度及诊断报告。
# 待确认：真实媒体时基/AAC 时间戳、令牌权限；CRF 为单组参数而非画质最佳结论。
# 后续研究：根据 encode.log、progress.log 分析编码速度、资源开销、码率、警告并选择后续参数。
# 风险：上传失败可留下本次部分附件；唯一运行前缀、不覆盖、不删原附件，不公开私有日志。
# 验证：分片还原、只压缩前 1200 帧、日志完整、成品可解码且输出帧数精确，不覆盖原附件。
"""Download one video, encode AV1, and append verified results to its Release."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit

from onedrive_batch import EXTENSIONS, clean_env

CHUNK = 8 * 1024**2
ASSET_LIMIT = 1_900_000_000
RESULT_PREFIX = 'av1-result-'


def release_target(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r'/([\w.-]+)/([\w.-]+)/releases/tag/(.+)', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
            or parsed.query or parsed.fragment or not match):
        raise ValueError('Use https://github.com/OWNER/REPO/releases/tag/TAG')
    owner, repo, tag = match.groups()
    return f'{owner}/{repo}', unquote(tag)


def encoder_env():
    return {k: v for k, v in clean_env().items() if k != 'GH_RELEASE_WRITE_TOKEN'}


def gh(*arguments, stdout=subprocess.PIPE, timeout=1800, write=False):
    environment = encoder_env()
    environment['GH_TOKEN'] = os.environ['GH_RELEASE_WRITE_TOKEN' if write else 'GH_TOKEN']
    result = subprocess.run(['gh', *arguments], stdout=stdout, stderr=subprocess.PIPE,
                            timeout=timeout, check=False, env=environment)
    if result.returncode:
        # Upstream errors can contain private URLs; never copy them into public logs.
        raise RuntimeError('GitHub request failed; check token permissions, Release state and network')
    return result.stdout


def api(endpoint):
    return json.loads(gh('api', '--hostname', 'github.com', endpoint, timeout=120))


def digest(asset):
    value = asset.get('digest') or ''
    if (asset.get('state') != 'uploaded' or not isinstance(asset.get('size'), int)
            or asset['size'] <= 0 or not re.fullmatch(r'sha256:[0-9a-f]{64}', value)):
        raise ValueError('Source asset must be uploaded, nonempty and have a GitHub SHA-256 digest')
    return value.removeprefix('sha256:')


def source_assets(assets):
    groups = {}
    for asset in assets:
        name = asset['name']
        if name.startswith(RESULT_PREFIX):
            continue
        part = re.fullmatch(r'(.+)\.part(\d+)', name)
        base, number = (part[1], int(part[2])) if part else (name, 0)
        if Path(base).suffix.lower() in EXTENSIONS:
            groups.setdefault(base, []).append((number, asset))
    if len(groups) != 1:
        raise ValueError('Release must contain exactly one source video or one set of byte-split video parts')
    numbered = sorted(next(iter(groups.values())), key=lambda item: item[0])
    numbers = [n for n, _ in numbered]
    if numbers != [0] and numbers != list(range(1, len(numbers) + 1)):
        raise ValueError('Video parts must be consecutive from part001; do not mix full video and parts')
    selected = [asset for _, asset in numbered]
    for asset in selected:
        digest(asset)
    return selected, numbers != [0]


def download(repo, asset, sink, combined=None):
    expected = digest(asset)
    start = sink.tell()
    gh('api', '--hostname', 'github.com', f'repos/{repo}/releases/assets/{asset["id"]}',
       '-H', 'Accept: application/octet-stream', stdout=sink)
    end = sink.tell()
    if end - start != asset['size']:
        raise ValueError('Downloaded asset size mismatch')
    sink.seek(start)
    checksum = hashlib.sha256()
    while block := sink.read(min(CHUNK, end - sink.tell())):
        checksum.update(block)
        if combined is not None:
            combined.update(block)
    if checksum.hexdigest() != expected:
        raise ValueError('Downloaded asset SHA-256 mismatch')
    sink.seek(end)


def original_digest(repo, assets):
    checksum = next((a for a in assets if a['name'] == 'ORIGINAL_SHA256SUMS'), None)
    if not checksum or checksum['size'] > 65536:
        raise ValueError('Split input requires a small ORIGINAL_SHA256SUMS containing one original hash')
    with tempfile.TemporaryFile('w+b') as sink:
        download(repo, checksum, sink)
        sink.seek(0)
        lines = sink.read().decode('utf-8').strip().splitlines()
    if len(lines) != 1 or not re.fullmatch(r'[0-9a-fA-F]{64} [ *].+', lines[0]):
        raise ValueError('ORIGINAL_SHA256SUMS must contain one sha256sum-format line')
    return lines[0][:64].lower()


def local_asset(path):
    with path.open('rb') as source:
        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'name': path.name, 'size': path.stat().st_size, 'digest': f'sha256:{checksum}'}


def publish(repo, tag, path):
    expected = local_asset(path)
    # No --clobber: a collision fails rather than replacing any existing attachment.
    gh('release', 'upload', tag, str(path), '--repo', repo, write=True)
    return expected


def verify_uploaded(repo, release_id, expected):
    assets = []
    page = 1
    while True:
        batch = api(f'repos/{repo}/releases/{release_id}/assets?per_page=100&page={page}')
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    uploaded = {a['name']: a for a in assets}
    for item in expected:
        asset = uploaded.get(item['name'], {})
        if (asset.get('state') != 'uploaded'
                or any(asset.get(key) != item[key] for key in ('size', 'digest'))):
            raise ValueError('Uploaded attachment size/SHA-256 verification failed')


def upload_video(repo, tag, target, prefix):
    expected = []
    if target.stat().st_size <= ASSET_LIMIT:
        named = target.with_name(prefix + '.mp4')
        target.rename(named)
        return [publish(repo, tag, named)]
    with target.open('rb') as source:
        for number in range(1, (target.stat().st_size + ASSET_LIMIT - 1) // ASSET_LIMIT + 1):
            part = target.with_name(f'{prefix}.mp4.part{number:03d}')
            with part.open('xb') as sink:
                remaining = ASSET_LIMIT
                while remaining and (block := source.read(min(CHUNK, remaining))):
                    sink.write(block)
                    remaining -= len(block)
            expected.append(publish(repo, tag, part))
            part.unlink()  # Only this run's temporary upload part; original assets stay untouched.
    return expected


def upload_reports(repo, tag, encoded, prefix):
    expected = []
    for name in ('report.json', 'report.md', 'encode.log', 'progress.log', 'decode.log'):
        path = encoded / name
        if path.is_file():
            named = encoded / f'{prefix}.{name}'
            path.rename(named)
            expected.append(publish(repo, tag, named))
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-url', required=True)
    parser.add_argument('--crf', type=int, choices=range(1, 41), default=30)
    parser.add_argument('--preset', type=int, choices=range(4, 10), default=6)
    parser.add_argument('--max-frames', type=int, default=1200)
    args = parser.parse_args()
    if args.max_frames <= 0:
        parser.error('--max-frames must be positive')
    repo, tag = release_target(args.release_url)
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    if not run.isdecimal() or not attempt.isdecimal():
        raise ValueError('Invalid workflow run identity')
    prefix = f'{RESULT_PREFIX}first{args.max_frames}-crf{args.crf}-p{args.preset}-{run}-{attempt}'
    release = api(f'repos/{repo}/releases/tags/{quote(tag, safe="")}')
    if release.get('immutable') or release.get('draft'):
        raise ValueError('Use a published, mutable Release')
    # Read all assets, including Releases with more than one API page of past results.
    assets = []
    page = 1
    while True:
        batch = api(f'repos/{repo}/releases/{release["id"]}/assets?per_page=100&page={page}')
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    selected, split = source_assets(assets)
    expected_original = original_digest(repo, assets) if split else digest(selected[0])
    size = sum(a['size'] for a in selected)
    if shutil.disk_usage('.').free < size + 2_000_000_000:
        raise ValueError('Insufficient space: source size plus 2 GB reserve required')
    output = Path('output')
    output.mkdir(exist_ok=False)
    combined = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='release-input-', dir=Path.cwd()) as temporary:
        source = Path(temporary) / 'source.mp4'
        with source.open('x+b') as sink:
            for index, asset in enumerate(selected, 1):
                print(f'Downloading and verifying input {index}/{len(selected)}', flush=True)
                download(repo, asset, sink, combined)
        if combined.hexdigest() != expected_original:
            raise ValueError('Reconstructed original SHA-256 mismatch')
        try:
            subprocess.run([sys.executable, 'scripts/compress_av1.py', str(source),
                            '--output-dir', str(output / 'encoded'), '--crf', str(args.crf),
                            '--preset', str(args.preset), '--threads', '4',
                            '--max-frames', str(args.max_frames)],
                           env=encoder_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=18000)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Preserve diagnostics in the same private Release, never in public Artifacts.
            uploaded = upload_reports(repo, tag, output / 'encoded', prefix + '-failed')
            verify_uploaded(repo, release['id'], uploaded)
            raise RuntimeError('Encoding/verification failed; diagnostics returned to the source Release') from None
    encoded = output / 'encoded'
    report = json.loads((encoded / 'report.json').read_text())
    target = encoded / 'output_av1.mp4'
    if report.get('status') != 'success' or not target.is_file():
        raise ValueError('Encoding/verification did not produce a completed video')
    output_hash = local_asset(target)['digest']
    expected = upload_video(repo, tag, target, prefix)
    expected.extend(upload_reports(repo, tag, encoded, prefix))
    verify_uploaded(repo, release['id'], expected)
    manifest = output / f'{prefix}.manifest.json'
    manifest.write_text(json.dumps({'status': 'complete', 'input_sha256': expected_original,
                                   'output_sha256': output_hash, 'crf': args.crf,
                                   'preset': args.preset, 'max_frames': args.max_frames,
                                   'assets': expected}, indent=2) + '\n')
    marker = publish(repo, tag, manifest)
    verify_uploaded(repo, release['id'], [marker])
    summary = (f'AV1 prefix complete: first {args.max_frames} frames, CRF {args.crf}, '
               f'preset {args.preset}, output {report["output_bytes"]} bytes, '
               f'encoding {report["encode_seconds"]:.1f} seconds. '
               'MP4 and detailed logs were appended to the private Release.\n')
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as sink:
            sink.write(summary)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print traceback, source names or private API responses in a public repository.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(f'::error::Release AV1 failed: {message}', file=sys.stderr)
        sys.exit(1)
