# ffmpeg-compiler

本项目仅保留 `main`，实现、修复与测试在主分支维护。

## GitHub Release 前 1200 帧 AV1 压缩及日志分析

工作流 `.github/workflows/compress-av1_ghrelease.yml` 只压缩原始视频开头连续 **1200 帧**，单次执行一组 CRF/preset，不再运行 benchmark、SSIM/VMAF，也不进行全片转码。
保留原分辨率、8/10-bit、显示比例和源帧时间节奏；支持 AAC 音轨的同步拷贝。输出保留为 MP4。默认 `CRF=30`、`preset=6`。

| 工作流输入 | 默认值 | 用途 |
| --- | --- | --- |
| `release_url` | 必填 | 私有来源 Release 链接 |
| `crf` | `30` | SVT-AV1 质量（1–40） |
| `preset` | `6` | SVT-AV1 速度（4–9） |

输出文件以 `av1-result-first1200-crf<CRF>-p<PRESET>-<run_id>-<attempt>` 开头，全部回传同一**私有 Release**：

| 文件 | 内容 |
| --- | --- |
| `.mp4` | 开头 1200 帧 AV1 成品 |
| `.encode.log` | FFmpeg/SVT-AV1 版本、参数、处理信息、警告、编码耗时与资源统计 |
| `.progress.log` | 周期性 frame、fps、bitrate、total_size、speed、out_time |
| `.decode.log` | 解码过程的错误诊断 |
| `.report.json` | 探测信息、实际帧数、耗时与输出体积 |
| `.report.md` | 人工可读结果摘要 |
| `.manifest.json` | 完成标记、参数与附件清单 |

只对输出的前 1200 帧报告真实统计值。由于源文件大小是**全片**，不能用其大小与 1200 帧压缩结果直接计算压缩率。
采用 `READONLY`（私有源仓库读取权限）、`WRITEONLY`（私有源仓库读写权限）两个 Actions Secrets，编码进程不接收令牌；不覆盖原始附件，不创建公开 Artifact。分片校验和原始文件校验沿用当前代码。运行前要求源文件大小加 2 GB 可用空间，本次工作流最长 180 分钟。
媒体处理仅支持原项目现行 SDR 4:2:0 8/10-bit、AAC 约束；不支持的 HDR、字幕和非 AAC 音轨会明确失败。完整画质是否可接受仍需要对成品进行主观审片。

## 工作流清单

- `build-ffmpeg.yml`：源码编译、CI 和定时构建。
- `compress-av1.yml`：OneDrive 转码，保持原有全片行为。
- `compress-av1_ghrelease.yml`：私有 Release 视频开头 1200 帧压缩、详细日志及成品回传。

## 用户视频 → AV1 MP4

OneDrive 部署全过程见 [docs/onedrive-setup.md](docs/onedrive-setup.md)。唯一来源为 OneDrive，在 action job 内逐个下载、压缩 `od:ffmpeg` 直接子视频文件，并回传 `od:ffmpeg-output/<run_id>-<attempt>-<随机后缀>/`，不使用 Artifact 中转；本地授权入口 `bash scripts/connect_onedrive.sh`。需要先由本人完成微软浏览器授权并设置专用 Secret。

独立工作流 `.github/workflows/compress-av1.yml` 仅支持 **OneDrive 根目录 ffmpeg 批处理**，
输出 AV1 MP4（随原视频保持 8/10-bit）、压缩报告和日志，不重复编译 FFmpeg。
参考 U-Boot All in One 的 `runner-image → action` 结构，不引入 toolchain。
`runner-image` 在 `ubuntu-latest` 上调用 `Grinch27/github-actions/.github/actions/resolve-runner-image@main`，
从 GitHub 官方 runner 列表选取最高版本 Ubuntu x64 标签；解析失败时该 Action 回退到 `ubuntu-latest`。
`action` 使用解析结果运行，系统镜像版本和 CPU 信息保存在 `runtime/runner.log`。
工作流沿用现有 `permissions: write-all`。每次从 Docker Hub 拉取 `linuxserver/ffmpeg:latest`，
本次所有 ffmpeg/ffprobe 调用均固定使用刚拉取的 image ID，不使用 runner 的 APT FFmpeg。
镜像是 LinuxServer.io 维护的第三方构建。报告包含 image ID、RepoDigests、FFmpeg 和 SVT-AV1 版本。
容器内只执行媒体命令，Python 留在 runner；无需镜像预装 Python。输入只读挂载，输出单独可写，编码容器禁用网络。

### 测试输入

- 当前输入：OneDrive 根目录 `ffmpeg/110594-1080p.mp4`；19,439,290 字节。
- 约 90 秒、1502×1080、30 fps、H.264 + AAC；SAR `31279:30892`，不能强制改为方形像素。

### 运行与下载

1. 工作流部署到默认分支后，进入 Actions → Compress Video to AV1 MP4 → Run workflow。
2. 填写 `max_files`、`crf`、`preset`，建议先用 `crf=30`、`preset=6`。
3. OneDrive 成品和单文件报告直接回传 ffmpeg-output 的独立运行目录，读回校验通过后才报告成功。也可从运行页面下载 `av1-mp4-<run_id>-<attempt>` 和 `av1-report-<run_id>-<attempt>`。
4. Summary 展示体积、节省比例、编码耗时和验证结果；成品、报告和日志 Artifact 均保存 3 天。
   此设置适用于后续上传，不会修改既有 Artifact 的到期时间。

后续输入直接上传到 OneDrive 根目录 `ffmpeg`。工作流从该目录下载，完成后回传根目录 `ffmpeg-output`。
授权使用 rclone 内置应用；需 `Files.ReadWrite offline_access User.Read`，详见部署指南。

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

## FFmpeg 源码编译

统一入口 [build-ffmpeg.yml](.github/workflows/build-ffmpeg.yml)，保留原生 Runner 和 `ubuntu:devel` 容器两条路线，共享输入、Runner 解析和相同构建步骤。无需独立的调用、CI 或定时工作流。

| 触发 | 行为 |
| --- | --- |
| 手动 / `workflow_call` | `route=container`（默认）、`native` 或 `both` |
| 修改编译工作流的 main Push / 指向 main 的 PR | 同时验证两条编译路线，默认不发布 |
| 每日 UTC 20:00（北京时间次日 04:00） | 仅容器编译上游 `master`，不发布 |
| `repository_dispatch` | 默认容器编译一次，不重复触发多个入口 |

媒体脚本、视频处理工作流及文档修改不触发源码编译。`repo_url`、`ffmpeg_branch`、`test_seconds`（默认 8 秒）、`runner_image`、`custom_env` 和 `create_release` 输入继续保留。URL 仍接受 gitweb/shortlog 形式，`masterd` 分支名纠正为 `master`。除引导 Job 外沿用动态 Runner，容器发行版保持 `ubuntu:devel`。

`create_release` 默认 false。发布时只允许选择单条路线，避免两条路线写入同名 Release 附件。构建保留安装压缩包、SHA-256、AV1 合成样本指标、构建日志及失败摘要；Artifact 保留期沿用 14 天。编译产生的合成样本测试，与真实私有视频的质量验收分开判断。
