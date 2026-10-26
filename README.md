# ffmpeg-compiler

本项目仅保留 `main`；实现、修复、凭据预检和运行都在 `main` 完成，不创建测试或开发分支。

## Release AV1 参数测试

使用 `.github/workflows/compress-av1_ghrelease.yml`，默认 `mode=benchmark`，填写 `release_url`。从原视频开始连续解码**前 1200 帧**，生成一份 FFV1 无损参考，以此分别比较 CRF 24/30/36 × preset 6/8，共 **6 个样本**。每个 AV1 样本都须通过恰好 1200 帧、像素格式及完整解码验证，记录体积、耗时、SSIM 和可用时的 VMAF；测试后删除样本。取样按帧数而非固定秒数，保留原帧时间节奏。报告使用 `av1-benchmark-<run_id>-<attempt>` 唯一文件名回传原 Release，逐个检查上传大小和 GitHub SHA-256；不上传视频样本，不使用公开 Artifact。片段分数不代表全片码率预测，也不能独自判定最优参数。下载复用分片流式合并，至少需要原片大小加 1 GB；创建 FFV1 参考前额外检查磁盘空间。只有明确选择 `mode=compress` 才执行全片转码，其空间要求见下文。

## GitHub Release 视频 → AV1 → 原 Release

独立工作流 [compress-av1_ghrelease.yml](.github/workflows/compress-av1_ghrelease.yml)，与下面的 OneDrive 流程分开运行，不重新编译 FFmpeg，不安装 rclone，不使用 Actions Artifact。

在本仓库 Actions Secrets 中配置 `readonly`（源仓库 Contents: Read-only）和 `writeonly`（源仓库 Contents: Read and write）。下载和上传分别使用对应 Secret，编码子进程不接收这两个令牌。PAT 的显示名称不会自动创建 Secret。

同一入口的 `mode=benchmark`（默认）比较前 1200 帧的六组参数，`mode=compress` 为显式全片压缩；CRF/preset 输入仅用于全片压缩。

| 输入 | 默认值 | 用途 |
| --- | --- | --- |
| `release_url` | 空，运行时必填 | `https://github.com/OWNER/REPO/releases/tag/TAG` |
| `mode` | `benchmark` | 默认比较前 1200 帧；`compress` 显式执行全片压缩 |
| `crf` | `30` | 全片画质/体积；支持 1–40 |
| `preset` | `6` | 速度；支持 4–9 |

Release 须已发布、允许追加附件，只包含一个源视频或一组 `视频扩展名.part001` 起的连续原始字节分片；忽略本工作流产生的 `av1-result-` 附件和非视频文件。源附件须有 GitHub SHA-256 digest，分片输入另须有单行 `ORIGINAL_SHA256SUMS`。下载按序直接追加到一个临时输入文件，检查每个附件的大小/哈希及合并后原文件哈希，不同时保留全部分片。`manifest.json` 和原说明不执行为代码。

复用 `scripts/compress_av1.py` 的 SDR/AAC 支持范围和完整解码验证。每次运行一组 CRF/preset，属于基线压缩，不宣称已找到最优参数或主观无损。完成后向**同一 Release** 追加成品、报告和日志；私有源的结果随该 Release 保持私有。文件使用 `av1-result-crf<CRF>-p<PRESET>-<run_id>-<attempt>` 前缀，不覆盖或删除原附件。成品超过 1,900,000,000 字节时按序拆片回传，输出 SHA-256 与分片顺序见最后上传的 `.manifest.json`。

只有编码/完整解码通过、上传大小与 GitHub digest 校验通过，并发布 `status: complete` 的 manifest 后才报告成功；上传校验依据 GitHub 元数据，未进行全量下载回读。失败可能留下本次部分附件，不能只凭成品附件存在认定整次运行成功。编码失败时尝试把诊断报告/日志回传原 Release，不上传未验证视频。下载前要求可用磁盘至少为原视频大小的 4 倍加一个 1.9 GB 上传分片，本次运行限时 350 分钟，需考虑账户 Actions 配额。

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

## 工作流清单

| 文件 | 用途 |
| --- | --- |
| [build-ffmpeg.yml](.github/workflows/build-ffmpeg.yml) | FFmpeg 源码编译、CI 和定时运行 |
| [compress-av1.yml](.github/workflows/compress-av1.yml) | OneDrive 批量压缩及回传 |
| [compress-av1_ghrelease.yml](.github/workflows/compress-av1_ghrelease.yml) | Release 前 1200 帧 AV1 参数测试、全片压缩及回传 |

旧 `build-ffmpeg_dev.yml`、`build-ffmpeg_indocker_dev.yml`、`call-build.yml`、`ci.yml` 和 `gh-cache-av1-benchmark.yml` 已并入上述入口。旧路径的手动/CLI 调用需更新；已有运行使用其启动时的提交继续执行。
