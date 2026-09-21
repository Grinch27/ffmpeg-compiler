#!/usr/bin/env bash
# 需求：用户在自己的交互终端完成 od 授权，验证只读配置后上传专用 GitHub Secret。
# 待确认：个人账户 Entra 应用注册、浏览器授权及 ffmpeg 文件夹存在。
# 后续研究：令牌自动持久化；当前失效后重新授权并再次上传配置。
# 风险：配置含凭据，权限 600；不得在录屏、共享日志或聊天中展示授权结果。
# 验证重点：单个 od remote、只读 scopes、根目录 ffmpeg 可列出；不下载或压缩视频。
set -euo pipefail
umask 077
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/rclone"
config_file="$config_dir/ffmpeg-onedrive.conf"
rclone_bin="$(command -v rclone || true)"
if [[ -z "$rclone_bin" && -x "$HOME/.local/bin/rclone" ]]; then
  rclone_bin="$HOME/.local/bin/rclone"
fi
[[ -n "$rclone_bin" ]] || { echo 'Install rclone first; see docs/onedrive-setup.md'; exit 1; }
command -v gh >/dev/null
mkdir -p "$config_dir"
printf '%s\n' 'Create a remote named od (type onedrive).' \
  'Enter your client ID and client secret in the local rclone prompts.' \
  'Advanced access_scopes MUST be: Files.Read offline_access User.Read' \
  'Choose the drive root; leave root_folder_id empty. Do not encrypt this dedicated config.' \
  'After validation this script uploads ONEDRIVE_RCLONE_CONFIG to Grinch27/ffmpeg-compiler.'
"$rclone_bin" --config "$config_file" config
chmod 600 "$config_file"
python3 - "$script_dir" "$config_file" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from onedrive_batch import validate_config
try:
    validate_config(Path(sys.argv[2]).read_text())
except Exception:
    raise SystemExit('Config validation failed. Check od/type/scopes/token/drive_id/root_folder_id locally; do not paste tokens into chat.')
print('Read-only dedicated config validated')
PY
"$rclone_bin" --config "$config_file" lsjson od:ffmpeg --files-only --max-depth 1 |
  python3 -c 'import json,sys; print("ffmpeg folder reachable; direct files:",len(json.load(sys.stdin)))'
gh secret set ONEDRIVE_RCLONE_CONFIG --repo Grinch27/ffmpeg-compiler < "$config_file"
printf '%s\n' 'Secret uploaded. No video was transferred. You can now start the OneDrive workflow.'
