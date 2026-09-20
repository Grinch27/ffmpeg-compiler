# ffmpeg-compiler

## 用户视频 → AV1 MP4

新增独立工作流 `.github/workflows/compress-av1.yml`，手动读取**本仓库 Release 附件 ID**，
输出 AV1 MP4（随原视频保持 8/10-bit）、压缩报告和日志，不重复编译 FFmpeg，不自动发布成品 Release。
参考 U-Boot All in One 的输入、执行、报告和产物阶段，使用普通 `ubuntu-24.04` runner
和最小 `contents: read` 权限。每次从 Docker Hub 拉取 `linuxserver/ffmpeg:latest`，
本次所有 ffmpeg/ffprobe 调用均固定使用刚拉取的 image ID，不使用 runner 的 APT FFmpeg。
镜像是 LinuxServer.io 维护的第三方构建。报告包含 image ID、RepoDigests、FFmpeg 和 SVT-AV1 版本。
容器内只执行媒体命令，Python 留在 runner；无需镜像预装 Python。输入只读挂载，输出单独可写，编码容器禁用网络。

### 已上传的测试输入

- Release: https://github.com/Grinch27/ffmpeg-compiler/releases/tag/av1-test-110594-20260921
- 文件：`110594-1080p.mp4`；附件 ID：`577680438`；大小：19,439,290 字节。
- 约 90 秒、1502×1080、30 fps、H.264 + AAC；SAR `31279:30892`，不能强制改为方形像素。
- 该仓库公开，Release 原视频可公开下载。

### 运行与下载

1. 工作流部署到默认分支后，进入 Actions → Compress Video to AV1 MP4 → Run workflow。
2. 填写 `asset_id=577680438`，建议先用 `crf=30`、`preset=6`。
3. 从运行页面下载 `av1-mp4-<run_id>-<attempt>` 和 `av1-report-<run_id>-<attempt>`。
4. Summary 展示体积、节省比例、编码耗时和验证结果；Artifact 保存 7 天。

后续输入先上传到本仓库 Release，再读取附件 ID。Actions 表单没有文件上传控件。
下载按本仓库附件 ID 定位，凭据只传给下载步骤，不接受任意脚本或 URL。

### 支持范围和质量边界

- 单视频流、逐行 SDR 4:2:0，支持无音轨或多个 AAC 音轨，音频直接复制。
- 位深随源：`yuv420p` → 8-bit AV1；`yuv420p10le` → 10-bit AV1，验证输出位深一致。
- 保留分辨率、帧时间节奏、显示比例、元数据和章节；HDR、旋转/附加视频 side data、
  字幕、数据流、非 AAC 音频明确失败，避免静默丢弃或未经确认转换。
- CRF 1–40，preset 4–9；CRF 越低通常越大，preset 越低通常越慢。
- 默认 CRF 30 / preset 6 是画质优先的起点，有损转码不保证缩小，也不保证肉眼无损。
- 报告检查 AV1、分辨率、平均帧率、SAR、解码帧数、音轨参数、时长差≤0.1秒和完整解码。
  不含 VMAF/SSIM；最终画质需要观看成品确认。
- 输入小于 2 GiB；下载前检查磁盘；编码步骤限时 310 分钟，整个 job 限时 350 分钟。
  文件过大、下载失败、输入不支持或编码失败时退出非零；未验证的 partial 文件不上传为成品。
- GitHub 托管运行时长、Artifact 存储和权限仍受账户配额限制。

### 本地执行

依赖 Python 3、带 `libsvtav1` 的 FFmpeg 和 ffprobe，无 Python 第三方依赖。
输出目录必须不存在，避免覆盖旧结果。

```bash
python3 scripts/compress_av1.py /path/to/input.mp4 --output-dir /path/to/new-output --crf 30 --preset 6 --threads 4
```

`report.json` 保存输入/输出探测结果及 FFmpeg 版本；`encode.log` 包含 SVT-AV1 实际版本。
本地测试不能替代 GitHub runner 实际运行验证。

GitHub Actions workflows for building FFmpeg from upstream `master` and running AV1 compression tests.

## What this project does

- Pull FFmpeg from upstream source (git.ffmpeg.org).
- Auto-normalize gitweb/shortlog URLs into a cloneable Git URL.
- Build FFmpeg with common external codec libraries.
- Run an AV1 compression test and collect metrics.
- Upload build artifacts and optional GitHub Release assets.

## Workflows

- `.github/workflows/build-ffmpeg_dev.yml`
  - Main runner-based build workflow (uses `ubuntu-latest`).
- `.github/workflows/build-ffmpeg_indocker_dev.yml`
  - Main container-based build workflow (runner `ubuntu-latest`, container `ubuntu:devel`).
- `.github/workflows/call-build.yml`
  - Scheduled/matrix caller workflow.
- `.github/workflows/ci.yml`
  - Push/PR auto CI workflow that tests both `build-ffmpeg_dev.yml` and `build-ffmpeg_indocker_dev.yml`.

## Important compatibility behavior

The workflows accept inputs like:

- `https://git.ffmpeg.org/gitweb/ffmpeg.git`
- `https://git.ffmpeg.org/gitweb/ffmpeg.git/shortlog/refs/heads/masterd`

They will normalize the URL to a cloneable Git endpoint and then build the `master` branch.
If `masterd` is provided by mistake as the branch, it is auto-corrected to `master`.

## Manual usage

From GitHub Actions, run one of:

- `Build FFmpeg (Dev)`
- `(Docker) Build FFmpeg (Dev)`

Key inputs:

- `repo_url`: upstream source URL.
- `ffmpeg_branch`: branch name, default `master`.
- `test_seconds`: AV1 test duration, default `8`.
- `create_release`: whether to create/update release.

Fixed runtime images in workflow definitions:

- Runner image: `ubuntu-latest`
- Docker container image: `ubuntu:devel`

Note: `create_release` defaults to `false` to keep workflow permissions minimal by default.

## Recommended repository Actions settings

Use least privilege in repository settings:

1. `Settings` -> `Actions` -> `General`.
2. Set `Workflow permissions` to `Read repository contents permission`.
3. Keep `Allow GitHub Actions to create and approve pull requests` disabled unless required.

## VS Code + Copilot failure-fix loop

The `CI Build and Test` workflow runs automatically on push and pull requests.

It runs these two workflows every time:

- `build-ffmpeg_dev.yml`
- `build-ffmpeg_indocker_dev.yml`

On failure each build workflow still uploads logs and artifacts, including `failure-summary.md` when available.

Then you can ask Copilot in VS Code to fix based on that summary and push again to retrigger CI.

## Output

Each run provides:

- FFmpeg install tarball.
- AV1 test metrics (`av1_metrics.txt`).
- Build logs (`configure.log`, `make.log`, encoder list).
- Optional GitHub Release with assets.
