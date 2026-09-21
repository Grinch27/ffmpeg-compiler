# OneDrive → GitHub Actions AV1 MP4 部署指南

## 1. 当前路线和验证边界

唯一视频来源是个人 OneDrive 根目录 `ffmpeg`。工作流文件为 `.github/workflows/compress-av1.yml`，不再提供来源选择或附件 ID。

流程：`runner-image` 选择最新可用 Ubuntu → `action` 拉取 `linuxserver/ffmpeg:latest` → rclone 下载 → AV1 编码及完整解码验证 → rclone 回传 `ffmpeg-output` → 读回字节校验。
下载、编码、回传全部在同一个 `action` job 中执行，Artifact 只备份最终成品和报告，保留 3 天，不作源视频中转。本机只负责授权与连接检查，不执行视频压缩。

- 默认 CRF 30、preset 6、编码线程 4；输出 MP4，保留源 8/10-bit，AAC 音频复制。
- 只读取 `ffmpeg` 的直接视频文件，不递归；每次按文件名排序重新处理全部匹配文件。
- `max_files` 默认 10，可设 1–100；超过上限整批拒绝，不是只取前 N 个。首次只放一个测试视频并设 1。
- 单文件须非空且小于 2 GiB；HDR、字幕、非 AAC 音轨、旋转 side data 等现有不支持内容会明确失败。
- 不删除或改写源视频；每次输出使用独立目录，防止覆盖历史成品。
- 个别文件失败继续其他文件，整批最终失败；已经通过编码验证的本地成品仍可备份为 Artifact。只有回传校验通过才计入批处理成功数。
- `action` 限时 350 分钟，批处理步骤限时 310 分钟；取消或强制终止不保证来得及保存报告。
- OneDrive 路线已在 [运行 35616764844](https://github.com/Grinch27/ffmpeg-compiler/actions/runs/35616764844) 验证下载、压缩、回传成功。当前移除旧输入分支后的文件仍需另行云端重跑验收。

## 2. 先准备账户和目录

1. 打开 [OneDrive](https://onedrive.live.com/)，用存放视频的个人 Microsoft 账户登录；没有账户时按微软页面注册并自行完成验证。
2. 首次使用先确认能进入“我的文件”，并有足够空间容纳源视频和成品。不要把账户登录成功等同于网盘已完成初始化。
3. 在“我的文件”根目录创建 `ffmpeg`，上传 `110594-1080p.mp4` 或其他允许公开处理的测试视频，等待上传结束。
4. 输出根目录为 `ffmpeg-output`，工作流回传时可自动创建，也可以在网页上预先创建。
5. 使用自己的个人网盘目录，不用“共享给我”的目录、快捷方式或分享链接。路径不是电脑上的 `Documents/ffmpeg`。

```text
我的文件/
├── ffmpeg/
│   └── 110594-1080p.mp4
└── ffmpeg-output/
    └── <run_id>-<attempt>-<随机后缀>/
        └── 0001/
            ├── output_av1.mp4
            ├── report.json
            └── report.md
```

## 3. 理解需要取得的三类权限

| 层次 | 必需权限/凭据 | 获取位置及用途 |
|---|---|---|
| Microsoft / OneDrive | 用户委托 OAuth 授权 | 浏览器登录个人账户并同意 rclone 访问，用于下载和回传 |
| 本机 rclone | 专用配置中的访问令牌和刷新令牌 | rclone 在浏览器授权后自动保存，文件权限设为 600 |
| GitHub | 仓库 Actions Secret 管理权限；触发 Actions 的权限 | 用有相应仓库权限的 GitHub 账户登录 gh；管理员可配置 Secret |

rclone 不是另外一个需要购买权限的云服务。它使用 Microsoft OAuth；不需要提供 OneDrive 密码给 GitHub。
本方案使用 rclone 内置 Microsoft 应用，**不需要 Entra 租户、自建应用、Client ID、Client Secret 或 Azure 订阅**。客户端 ID 和密钥字段留空，使用共享应用；遇到限流时重试，不能通过扩大文件权限解决限流。

高级配置必须显式填写以下三个 scopes：

```text
Files.ReadWrite offline_access User.Read
```

| Scope | 用途 | 边界 |
|---|---|---|
| `Files.ReadWrite` | 读取源视频、创建输出目录、上传成品和报告 | 允许读取、创建、修改、删除登录用户的文件；并非仅授权两个文件夹 |
| `offline_access` | 取得刷新令牌，让无人值守 Actions 刷新访问令牌 | 不是永久有效承诺，撤销授权或微软策略仍可能使其失效 |
| `User.Read` | 读取登录账户的基本资料 | 不需要额外目录管理员权限 |

不要添加 `Files.ReadWrite.All`、`Sites.Read.All` 或其他无关权限。本方案不使用应用级权限或 client-credentials 流程。
`ffmpeg` 只读和只写 `ffmpeg-output` 是代码约束，**不是 Microsoft 服务端的文件夹级隔离**。若不接受账户级文件读写权限，应停止授权，改用专门的个人账户存放可公开处理的视频。

依据：[rclone OneDrive 文档](https://rclone.org/onedrive/)、[Microsoft Files.ReadWrite 定义](https://learn.microsoft.com/en-us/graph/permissions-reference#filesreadwrite)、[Microsoft offline_access](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc#the-offline_access-scope)。

## 4. 本机工具准备

以下命令适用于当前 Linux 电脑；换机器需修改仓库路径。在同一个终端依次执行后续命令，保留这些变量：

```bash
export PATH="$HOME/.local/bin:$PATH"
ONEDRIVE_REPO_DIR=/home/user/github/ffmpeg-compiler
ONEDRIVE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/rclone/ffmpeg-onedrive.conf"
umask 077
mkdir -p "$(dirname "$ONEDRIVE_CONFIG")"
rclone version
python3 --version
gh --version
```

缺少工具时从 [rclone 官方安装说明](https://rclone.org/install/) 和 [GitHub CLI 安装说明](https://cli.github.com/) 安装对应系统版本。不要从未知网站下载附带“网盘令牌”的配置。

```bash
gh auth status
```

如果尚未登录，运行 `gh auth login`，选择 GitHub.com、HTTPS、浏览器登录，登录可管理 `Grinch27/ffmpeg-compiler` 的账户。它与 Microsoft 账户可以不同。
上传配置前确认目标仓库正确；迁移至自己的仓库时，下面命令及连接脚本里的仓库名称都需修改。

## 5. 新建 rclone remote 并完成 Microsoft 授权

为避免将其他网盘凭据上传到 GitHub，使用单独的配置文件，仅包含 `[od]`：

```bash
rclone --config "$ONEDRIVE_CONFIG" config
```

按字段名称操作，不依赖菜单编号：

| 提示 | 填写或选择 |
|---|---|
| `New remote` | `n`；已有 `od` 时改用编辑，不要创建重复 remote |
| `name` | `od` |
| `Storage` | `onedrive` 或 Microsoft OneDrive |
| `client_id` / `client_secret` | 留空；编辑旧配置时回车可能保留原值，需按提示清除自建应用凭据 |
| `region` | Global / `global`（普通国际版个人账户） |
| `Edit advanced config?` | `y` |
| `access_scopes` | `Files.ReadWrite offline_access User.Read` |
| `root_folder_id` | 留空：要选择网盘根目录，不要把 remote 根设成 ffmpeg |
| `auth_url` / `token_url` | 留空，不填此前 Entra 报错页面的租户地址 |
| 其他高级选项 | 保持默认，不启用 client-credentials |
| `Use web browser to automatically authenticate?` | 有本机浏览器选择 `y` |

浏览器操作：

1. 保持终端运行，使用 rclone 本次自动打开的登录页面，而不是旧 Entra 门户页面。
2. 登录存放 `ffmpeg` 的个人账户；检查账户是否正确。
3. 核对应用及文件读写、基本资料、持续访问权限；由你本人确认同意。不要把授权码、回调 URL 或令牌发给其他人。
4. 浏览器显示 `Success!` 后回终端继续。没有自动打开时，在**运行 rclone 的同一台电脑**打开终端打印的 `http://127.0.0.1:53682/auth?...` 完整地址。授权服务只在当前进程运行期间有效。
5. 选择 `OneDrive Personal or Business`，再选自己的网盘；确认结果类型为 `personal`。该选项名称包含 Business，不代表应选组织网盘。
6. 确认保存 `od`，再输入 `q` 退出配置菜单。

不要为该专用配置设置 rclone 配置加密密码：当前工作流没有解密流程。改以本机权限 600 和 GitHub Secret 保管。rclone 交互界面可能显示 token，不要共享完整终端、截图或配置。

如果本机没有浏览器，可按 [rclone 无界面授权说明](https://rclone.org/remote_setup/) 在可信电脑完成授权并私下传回授权结果；不要借用公共网站中转令牌。

## 6. 从旧只读授权升级 / 令牌重新授权

已有 `Files.Read` 配置时，先用上一节的 `config` 菜单编辑 `od`，将高级 `access_scopes` 改成规定的三个值，保存退出。然后必须重新授权：

```bash
rclone --config "$ONEDRIVE_CONFIG" config reconnect od:
```

本人在浏览器同意文件读写权限，回终端完成提示。仅修改 scopes 字符串不会升级旧令牌。令牌失效时也使用此命令；若原来已是相同权限，属于重新连接。
不要重新创建已验证的网盘条目或凭空猜测 drive_id；保留原来正确的个人网盘选择。完成后继续连接验证，并重新上传 GitHub Secret，云端不会自动取得本机新配置。

## 7. 验证配置、读取和写入权限

先验证配置结构，不输出凭据：

```bash
chmod 600 "$ONEDRIVE_CONFIG"
python3 - "$ONEDRIVE_REPO_DIR" "$ONEDRIVE_CONFIG" <<'PY'
# 验证重点：结构通过不等于令牌权限已生效；不输出凭据或异常原文。
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts'))
from onedrive_batch import validate_config
try:
    validate_config(Path(sys.argv[2]).read_text())
except Exception:
    raise SystemExit('配置不符合要求，请在本机检查 od/type/scopes/token/drive_id/root_folder_id；不要粘贴令牌。')
print('专用配置结构检查通过')
PY
```

只列出源目录的直接文件，核对测试视频名称：

```bash
rclone --config "$ONEDRIVE_CONFIG" lsf od:ffmpeg --files-only --max-depth 1
```

创建目标目录；不存在时可初步验证创建目录权限，已存在时成功不证明文件上传权限：

```bash
rclone --config "$ONEDRIVE_CONFIG" mkdir od:ffmpeg-output
```

真正的文件写入验收以首次 Actions 成品上传及 `check --download` 读回比对为准，不通过修改配置文本或目录存在来宣称已验证。

## 8. 保存 GitHub Actions Secret

完成上述验证后，将**整个专用配置文件**通过标准输入上传，不在命令行参数中粘贴 token：

```bash
gh secret set ONEDRIVE_RCLONE_CONFIG --repo Grinch27/ffmpeg-compiler < "$ONEDRIVE_CONFIG"
gh secret list --repo Grinch27/ffmpeg-compiler
```

应在名称列表看到 `ONEDRIVE_RCLONE_CONFIG`；列表不显示值。CLI 会在本地加密后发送到 GitHub，见 [gh secret set](https://cli.github.com/manual/gh_secret_set)。

网页替代方式：仓库 Settings → Secrets and variables → Actions → New repository secret；名称填 `ONEDRIVE_RCLONE_CONFIG`，Secret 内容填专用配置全文。不能只填 token，也不要 base64 编码，更不要放入普通 Variables、工作流 YAML、Issue 或仓库文件。网页保存操作由本人在私有环境完成。
如提示无权限，使用仓库管理员账户或请管理员设置；不要把个人 GitHub PAT 另放进视频处理工作流。运行时仍保持 `contents: read`，访问 OneDrive 使用上述专用 Secret。

已有便捷脚本：

```bash
bash "$ONEDRIVE_REPO_DIR/scripts/connect_onedrive.sh"
```

它会启动交互配置、检查配置及源目录，并上传 Secret。与本节手动流程二选一即可；它不会自动替你同意微软新增权限，也不会执行真实写入测试。只读配置升级仍需完成第 6 节重新授权。

## 9. 在 GitHub Actions 运行和验收

进入仓库 Actions → Compress Video to AV1 MP4 → Run workflow → 分支 main。只需填写 `max_files`、`crf`、`preset`，不再填写来源或附件字段。

首次仅放一个测试视频：

```bash
gh workflow run compress-av1.yml --repo Grinch27/ffmpeg-compiler --ref main \
  -f max_files=1 -f crf=30 -f preset=6
```

验收：

1. `runner-image` 和 `action` 成功，实际编码发生在 GitHub runner。
2. OneDrive `ffmpeg-output/<run_id>-<attempt>-<随机后缀>/0001/output_av1.mp4` 存在，报告同目录可见。
3. 报告 Artifact 的 `batch.json` 含正确 `source_name`、`output_remote`，`upload_verified: true`，成功/失败数量符合预期。
4. 单文件报告验证 AV1、原始位深、分辨率、SAR、帧数、音轨、时长及完整解码；观看成品确认主观画质。
5. Artifact 备份到期为 3 天；OneDrive 成品不受此 Artifact 保留期影响，工作流不自动清理它们。

上传使用 `--immutable`，回传后 `check --download` 比对实际字节；失败不报告整条流程成功。失败可能留下部分输出目录，不自动删除，避免误删可用结果。

## 10. 故障、凭据维护和撤销

| 现象 | 操作 |
|---|---|
| Entra 提示账户不在 Microsoft Services 租户 | 使用 rclone 内置应用的授权链接，不继续应用注册流程 |
| 登录页空白或超时 | 保持 rclone 运行，在同机浏览器打开它本次生成的地址；仍失败检查网络，进程退出后需重新生成链接 |
| 本地回调连接失败 | 检查 53682 端口和本机防火墙；不能在另一台电脑访问本机 localhost |
| `ObjectHandle is Invalid` / 网盘类型错误 | 核对 Microsoft 账户和个人 drive 选择；用 config 编辑正确网盘，已有有效个人 drive_id 时保留，不盲选列表第一项 |
| 文件夹不存在 | 确认根目录名称准确为 ffmpeg，root_folder_id 为空，不是共享快捷方式 |
| 能下载但上传 403 / accessDenied | 检查 Files.ReadWrite，重新浏览器授权、更新 Secret，不能只改 scopes 文本 |
| 配置验证失败 | 确保仅含 od，type=onedrive、明确三个 scopes、token/drive_id 非空，不设自定义认证 URL |
| TLS/传输超时 | 先查网络与代理；可在同一 rclone 命令增加 --disable-http2 排查，不能由一次成功断言一定是 HTTP/2 原因 |
| gh 上传或触发无权限 | 检查 gh auth status、仓库名称和登录账户权限；不打印 gh auth token |
| 超过 max_files | 移走不需处理的源视频或明确提高上限；这不是抽样数量 |
| 工作流失败但有成品 Artifact | 编码可能已完成，回传或校验失败；查看 batch.json，不能只凭 Artifact 判断回传成功 |
| 授权撤销或令牌失效 | 按第 6 节 reconnect 后重新上传 Secret |

访问令牌可由 rclone 自动刷新，但 runner 临时配置销毁后，更新过的刷新令牌不会写回 GitHub Secret。没有固定的“永远有效”期限；需要时重新授权和上传，见 [Microsoft 刷新令牌说明](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)。内置应用方案没有自己创建的 Client Secret，不需要维护自建密钥到期日。

不再使用时：在 Microsoft 账户的应用访问/授权管理中撤销对应 rclone 应用访问，再删除仓库 `ONEDRIVE_RCLONE_CONFIG` Secret，并按自己的保管策略清理本机专用配置。仅删除 GitHub Secret 不会撤销已经签发的微软令牌；撤销共享 rclone 应用授权可能影响同账户其他 rclone 连接。

本机配置权限 600；配置不进 Git、不进 Artifact、不传入编码容器。公开日志避免使用 `rclone config show`、`--dump auth`、打印环境变量或 OAuth 错误原文。即使 GitHub 会掩码 Secret，也不应依赖掩码去公开完整配置。
