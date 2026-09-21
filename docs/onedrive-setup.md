# OneDrive 个人账户 → GitHub Actions AV1 批处理部署

## 当前实现与边界

- 仓库：`Grinch27/ffmpeg-compiler`（公开）；用户已明确允许 ffmpeg 专用目录的视频云端处理和公开仓库 Artifact 交付。
- 源目录固定为所选 OneDrive 根目录下的 `ffmpeg`，remote 固定为 `od`；只处理直接子文件，不递归。
- 首版每次最多 10 个视频；可在表单调整 max_files（1–100）。超限会整体拒绝，不会静默只取前几个。
- 单文件必须非空且小于 2 GiB；根据扩展名筛选视频，编码器仍会拒绝不支持的媒体内容。
- CRF 30、preset 6、lp=4，源 8/10-bit，AAC 复制；不支持 HDR/字幕/非 AAC 音轨等既有边界保持不变。
- 源目录 ffmpeg 不写入、不删除；成品写入 ffmpeg-output；本地临时下载副本会自动清理。每次重新运行都会重新处理文件，不会标记“已处理”。
- 按文件名排序，结果目录为 `0001`、`0002` 等；`batch.json` 提供原文件名和结果目录的映射。
- 某文件失败继续处理其余文件；已有成功 MP4 仍上传，整批有失败则任务标记失败。取消/强制超时可能来不及上传。
- 成品和报告 Artifact 均保存 3 天。下载配置不在工作区、不传入编码子进程、不上传 Artifact。
- GitHub Secret 里的刷新令牌不会自动更新；到期或撤销授权后需要再次运行连接脚本。

## 1. 准备 OneDrive 文件夹

登录自己的 OneDrive，在根目录创建 `ffmpeg`，先放一段小型、非敏感测试视频。
例如 `ffmpeg/110594-1080p.mp4`。等上传完成后再测试。
目录限制只是程序行为，不是微软服务端的文件夹级授权隔离。

## 2. 使用 rclone 内置 Microsoft 应用

无需自行注册 Entra 应用或客户端密钥。client_id、client_secret 留空。
必须在浏览器授权 `Files.ReadWrite offline_access User.Read`，回传需要写入权限。
如果之前只有 Files.Read，仅修改配置字符串不会升级令牌；必须重新浏览器授权。
Files.ReadWrite 覆盖账户文件，源目录只读和仅写 ffmpeg-output 由代码约束，不是服务端文件夹级隔离。
参考：https://rclone.org/onedrive/

## 3. 在自己的本地终端完成 rclone 授权

先确认 rclone 可用：

```bash
/home/user/.local/bin/rclone version
```

如果该可执行文件不存在，可从 https://rclone.org/install/ 安装官方版本。
在自己能够打开浏览器的终端执行连接脚本（不要把交互输出贴到聊天中）：

```bash
bash /home/user/github/ffmpeg-compiler/scripts/connect_onedrive.sh
```

连接脚本会先提示其行为，再进入 rclone 的交互配置：

| 提示 | 填写 |
|---|---|
| New remote | n |
| name | od |
| Storage | onedrive |
| client_id | 留空（内置应用） |
| client_secret | 留空（内置应用） |
| region | Global（普通国际版个人账户） |
| Edit advanced config | y |
| access_scopes | Files.ReadWrite offline_access User.Read |
| root_folder_id | 留空 |
| 其他高级参数 | 未有明确需要时保留默认 |
| Use web browser | y |
| 网盘类型 | OneDrive Personal or Business |
| 网盘选择 | 自己的个人网盘，确认根目录 |
| 保存 remote | y，然后退出配置菜单 |

浏览器中登录目标个人 Microsoft 账户，检查应用名及文件读写权限后同意；这次写入权限用于回传成品。
不要为这份专用配置设置额外的 rclone 配置加密密码，当前工作流只接收独立明文配置并由 GitHub Secrets 加密保存。

脚本会在本地验证配置和 `od:ffmpeg` 可达性，然后上传仓库 Secret：`ONEDRIVE_RCLONE_CONFIG`。
配置路径：`/home/user/.config/rclone/ffmpeg-onedrive.conf`（设置 XDG_CONFIG_HOME 时跟随该目录），权限 600。
仅此一个专用 remote 可上传，不能混入其他网盘配置。

脚本不会把密钥作为命令行参数，也不会主动打印配置；rclone 自己的交互界面可能显示 token，因此只在自己的终端操作。
如果机器不能打开浏览器，按 https://rclone.org/remote_setup/ 的无界面授权步骤处理，不要通过聊天传递 token。

## 4. 检查 Secret 是否设置成功

```bash
gh secret list --repo Grinch27/ffmpeg-compiler
```

应看到名称 `ONEDRIVE_RCLONE_CONFIG`。列表只显示名称，不显示密钥值。
也可访问仓库 Settings → Secrets and variables → Actions。

## 5. 首次云端测试

先确保 ffmpeg 文件夹只有一段测试视频；本工作流会处理目录中全部匹配的视频，而非只处理最新文件。
进入 Actions → Compress Video to AV1 MP4 → Run workflow：

- source_type：onedrive
- asset_id：留空
- max_files：10
- crf：30
- preset：6

等价命令：

```bash
gh workflow run compress-av1.yml --repo Grinch27/ffmpeg-compiler --ref main \
  -f source_type=onedrive -f max_files=10 -f crf=30 -f preset=6
```

流程：解析最新 runner → 拉取 Docker Hub linuxserver/ffmpeg:latest → 安装 rclone → 列出 ffmpeg → 在同一个 action job 中逐文件下载/编码/验证 → 回传 ffmpeg-output → 读回校验 → Artifact 备份。没有下载源视频到 Artifact 再供另一 job 获取的中间步骤。
下载时配置在 RUNNER_TEMP，编码子进程移除 OneDrive/rclone 凭据环境变量；FFmpeg 容器没有网络访问权限。
下载采用 rclone 自带传输校验，另核对大小和远端修改时间；不是声明所有 OneDrive 文件均有 SHA-256。

## 6. 回传与验收

- OneDrive 成品位置：`ffmpeg-output/<run_id>-<attempt>-<随机后缀>/<文件编号>/output_av1.mp4`，同目录有单文件报告。
- 不覆盖已有成品，不创建公开分享链接。上传使用 immutable，随后 check --download 验证远端字节。
- batch.json 记录源文件名、output_remote、upload_verified；只有回传校验通过才计为成功。
- 上传失败可能留下不完整批次；保留现有结果用于排查，不自动清空远端目录。

- MP4 Artifact：只包含已通过验证的 `output_av1.mp4`，按数字目录区分。
- 报告 Artifact：`batch.json`/`batch.md`、各文件报告、编码日志、镜像和 runner 信息。
- 核对所有预期文件是否出现在 batch.json，成功/失败数是否正确。
- 核对源位深、尺寸、帧数、音轨和完整解码验证；这些不等于主观画质验收。
- 核对 Artifact 到期时间为 3 天。
- 首次 OneDrive OAuth/下载/编码只有在真实云端运行成功后才算端到端部署完成；mock 测试不能替代此验收。

## 7. 授权维护与故障

- Client Secret 到期：创建新 Value，在本地 rclone 配置中更新后重新授权并重新上传专用配置。
- 用户令牌失效：运行 `rclone --config /home/user/.config/rclone/ffmpeg-onedrive.conf config reconnect od:`，再执行下列命令更新 Secret。
- rclone 下载错误原文不会公开上传，以免 OAuth 异常泄露敏感信息；需要在自己的本地终端诊断具体错误。
- 找不到目录：确认是网盘根目录 `ffmpeg`，remote 未设置 root_folder_id；不要填本地路径或分享链接。
- 文件超限：单文件 <2 GiB；数量超过 max_files 会拒绝整批。不得期待 350 分钟 job 能处理任意数量/大小的视频。
- 部分视频失败：先查看对应数字目录的报告，HDR/字幕/非 AAC 音频等仍属于不支持范围。

```bash
gh secret set ONEDRIVE_RCLONE_CONFIG --repo Grinch27/ffmpeg-compiler \
  < /home/user/.config/rclone/ffmpeg-onedrive.conf
```

配置刷新不会自动持久化到 GitHub Secret，也未授予工作流修改 Secrets 的高权限令牌。
参考：https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens
