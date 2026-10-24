#!/usr/bin/env python3
# 需求：只显示两个 Secret 的可用状态，GET 检查指定 Release；不下载媒体、不执行写入。
# 待确认：GET 成功不能证明写入权限；后续研究：以实际报告上传验证写入能力。
# 风险：公开日志；不输出令牌、长度或 API 错误正文；优化：失败时在下载前停止。
# 验证：空值、目标仓库、读权限；Secret 的名称大小写不是访问边界。
"""Preflight both credentials without exposing their values or writing remote data."""

import argparse
import os
import subprocess
import sys
from urllib.parse import quote

from release_av1 import encoder_env, release_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-url', required=True)
    args = parser.parse_args()
    repo, tag = release_target(args.release_url)
    failed = False
    for name, variable in (('READONLY', 'READ_TOKEN'), ('WRITEONLY', 'WRITE_TOKEN')):
        token = os.environ.get(variable, '')
        if not token:
            print(f'::error::{name} is unavailable to this job')
            failed = True
            continue
        environment = {key: value for key, value in encoder_env().items()
                       if key not in ('READ_TOKEN', 'WRITE_TOKEN')}
        environment['GH_TOKEN'] = token
        result = subprocess.run(['gh', 'api', '--hostname', 'github.com',
                                 f'repos/{repo}/releases/tags/{quote(tag, safe="")}'],
                                env=environment, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=120, check=False)
        if result.returncode:
            print(f'::error::{name} is present but cannot read source Release')
            failed = True
        else:
            print(f'{name} is present and can read source Release', flush=True)
    if not failed:
        print('Both read checks passed; upload permission awaits an actual upload')
    return int(failed)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'::error::Credential preflight failed ({type(error).__name__})', file=sys.stderr)
        sys.exit(1)
