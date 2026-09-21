#!/usr/bin/env python3
# 需求：只读下载 od:ffmpeg 的直接子视频文件，逐个调用现有云端编码器。
# 待确认：真实 OAuth 授权和首次 OneDrive 云端运行；不递归子目录。
# 后续研究：刷新令牌持久化、大文件、HDR；优化：逐文件下载以减少磁盘占用。
# 风险：配置含凭据，仅写 RUNNER_TEMP，不交给编码子进程，不记录 rclone 错误原文。
# 验证：目录边界、非法文件名、文件变化、下载失败、部分成功、凭据清理。
import argparse
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

EXTENSIONS = {'.mp4', '.mkv', '.mov', '.m4v', '.webm', '.avi', '.ts', '.m2ts', '.mpg', '.mpeg'}
REMOTE = 'od:ffmpeg'


def select_files(entries, max_files):
    selected = []
    for entry in entries:
        if entry.get('IsDir'):
            continue
        name = entry.get('Name', '')
        if Path(name).suffix.lower() not in EXTENSIONS:
            continue
        if (not name or name in ('.', '..') or '/' in name or '\\' in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or entry.get('Path') != name):
            raise ValueError('Unsafe or non-root filename in listing')
        if not isinstance(entry.get('Size'), int):
            raise ValueError('Invalid file-size metadata')
        selected.append(entry)
    if not selected:
        raise ValueError('No supported video filenames in the ffmpeg root folder')
    if len(selected) > max_files:
        raise ValueError(f'{len(selected)} videos exceed max_files={max_files}; no files were downloaded')
    names = [e['Name'] for e in selected]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate source filenames')
    return sorted(selected, key=lambda e: e['Name'])


def clean_env():
    return {k: v for k, v in os.environ.items()
            if not k.startswith(('RCLONE_', 'ONEDRIVE_')) and k not in ('GH_TOKEN', 'GITHUB_TOKEN')}


def remote_call(config, *arguments):
    # Never include upstream stderr in public reports: OAuth errors can contain sensitive data.
    result = subprocess.run(['rclone', '--config', str(config), '--log-level', 'ERROR',
                             '--retries', '3', '--low-level-retries', '10', *arguments],
                            env=clean_env(), capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(f'rclone {arguments[0]} failed (exit {result.returncode}); check authorization/path/network locally')
    return result.stdout


def validate_config(text):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(text)
    if config.sections() != ['od'] or config['od'].get('type') != 'onedrive':
        raise ValueError('Secret must contain only one [od] remote of type onedrive')
    if config['od'].get('root_folder_id', '').strip():
        raise ValueError('root_folder_id must be empty: ffmpeg is relative to the drive root')
    scopes = set(config['od'].get('access_scopes', '').split())
    if not scopes or not scopes <= {'Files.Read', 'offline_access', 'User.Read'} or 'Files.Read' not in scopes:
        raise ValueError('Configure explicit read-only access_scopes: Files.Read offline_access User.Read')
    if not config['od'].get('token') or not config['od'].get('drive_id'):
        raise ValueError('Complete browser authorization and drive selection before uploading the config')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--crf', type=int, choices=range(1, 41), default=30)
    parser.add_argument('--preset', type=int, choices=range(4, 10), default=6)
    parser.add_argument('--max-files', type=int, choices=range(1, 101), default=10)
    args = parser.parse_args()
    output = Path('output')
    output.mkdir(exist_ok=False)
    rows = []
    batch_error = None
    try:
        secret = os.environ.pop('ONEDRIVE_RCLONE_CONFIG', '')
        if not secret:
            raise ValueError('Missing ONEDRIVE_RCLONE_CONFIG repository secret')
        validate_config(secret)
        temp_root = Path(os.environ['RUNNER_TEMP']).resolve()
        if temp_root.is_relative_to(Path.cwd().resolve()):
            raise ValueError('RUNNER_TEMP must be outside the mounted workspace')
        with tempfile.TemporaryDirectory(prefix='onedrive-auth-', dir=temp_root) as auth_dir:
            config = Path(auth_dir) / 'rclone.conf'
            config.touch(mode=0o600)
            config.write_text(secret)
            del secret
            entries = select_files(json.loads(remote_call(config, 'lsjson', REMOTE, '--files-only', '--max-depth', '1')), args.max_files)
            for index, entry in enumerate(entries, 1):
                key = f'{index:04d}'
                row = {'id': key, 'source_name': entry['Name'], 'input_bytes': entry['Size'], 'status': 'failed'}
                rows.append(row)
                print(f'Processing video {index}/{len(entries)} ({entry["Size"]} bytes)', flush=True)
                try:
                    if not 0 < entry['Size'] < 2 * 1024**3:
                        raise ValueError('Source video must be nonempty and smaller than 2 GiB')
                    if shutil.disk_usage('.').free < entry['Size'] * 4:
                        raise ValueError('Insufficient disk space for next video')
                    with tempfile.TemporaryDirectory(prefix='onedrive-input-', dir=Path.cwd()) as input_dir:
                        source = Path(input_dir) / 'source.mp4'
                        remote = REMOTE + '/' + entry['Name']
                        remote_call(config, 'copyto', remote, str(source), '--checksum')
                        after = json.loads(remote_call(config, 'lsjson', remote, '--stat'))
                        if (source.stat().st_size != entry['Size'] or after.get('Size') != entry['Size']
                                or after.get('ModTime') != entry.get('ModTime')):
                            raise ValueError('Source size or modification time changed during transfer')
                        child = subprocess.run([sys.executable, 'scripts/compress_av1.py', str(source),
                                                '--output-dir', str(output / key), '--crf', str(args.crf),
                                                '--preset', str(args.preset), '--threads', '4'], env=clean_env())
                        if child.returncode:
                            raise RuntimeError('Encoding/validation failed; see per-file report')
                        report = json.loads((output / key / 'report.json').read_text())
                        if report['status'] != 'success' or not (output / key / 'output_av1.mp4').is_file():
                            raise RuntimeError('Completed output is missing')
                        row.update(status='success', output_bytes=report['output_bytes'],
                                   saved_percent=report['saved_percent'], encode_seconds=report['encode_seconds'])
                except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                    row['error'] = 'Transfer timed out' if isinstance(error, subprocess.TimeoutExpired) else str(error)
                    print(f'Video {index} failed; continuing remaining videos', flush=True)
    except Exception as error:
        # Avoid exposing configparser exceptions, which can quote secret lines.
        batch_error = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        if isinstance(error, configparser.Error):
            batch_error = 'Invalid rclone configuration format'
    finally:
        success = sum(r['status'] == 'success' for r in rows)
        report = {'source': REMOTE, 'files': rows, 'success_count': success,
                  'failed_count': len(rows) - success, 'error': batch_error}
        (output / 'batch.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        lines = ['# OneDrive batch', '', f'- Successful: {success}', f'- Failed: {len(rows)-success}']
        if batch_error:
            lines.append(f'- Error: {batch_error}')
        lines += ['', 'Source filenames and numeric output-folder mapping: see batch.json.',
                  'Only validated MP4 files are uploaded; source files remain in OneDrive.']
        markdown = '\n'.join(lines) + '\n'
        (output / 'batch.md').write_text(markdown)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
                summary.write(markdown)
        print(markdown)
    return 1 if batch_error or any(r['status'] != 'success' for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
