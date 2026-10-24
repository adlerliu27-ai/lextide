# Lextide 0.5.0 — macOS 开发版构建、安装与发布准备

G5 提供可本地运行的 Apple Silicon `.app`，不是公开发行版。正式 Bundle ID、发布主体、
Developer ID 签名、公证、完整许可证履约、干净 Mac 和用户安装验收尚待完成。
不会自动上传数据、安装模型、升级应用或请求管理员权限。

## 构建与产物

在 macOS arm64、Python 3.14.6、Xcode Command Line Tools 环境，从源码根目录执行：

```sh
uv sync --frozen --extra desktop --group dev --group bundle
.venv/bin/python scripts/build_macos.py
```

固定 PyInstaller 6.22.3 / hooks 2026.8，其余依赖固定于 `uv.lock`。构建脚本不自动安装
Python 或系统工具；不使用任何 Apple 身份。每次创建新的 `.artifacts/codex-g5/builds/时间/`，
也可传 `--output /新的构建目录`，已存在的目录会被拒绝。产物包括：

- `dist/Lextide.app`：目录式 Bundle；Python、GUI、Qt、核心 CLI 与导入器已包含。
- `Lextide-0.5.0-macOS-arm64-development.zip`：`ditto` 创建，保留内部 symlink。
- `build.log`、`build-record.json`、`bundle-inventory.json`：代码 commit/dirty 状态、源码
  SHA-256、依赖版本、所有文件哈希/链接目标、ZIP 哈希和体积。
- `staging/` 与 `work/`：可识别的构建中间产物，不含用户学习资料。

不承诺跨工具链的 ZIP 字节级重现；重现的是固定依赖与明确代码输入的构建流程。
源码或文档修改后必须重新构建，不能把旧构建的 commit 当作当前产物身份。
`Contents/Resources/build-info.json` 保存内嵌来源；`.app` 自身不能用单个文件 SHA 表示，
对账使用 inventory 的 SHA 和 ZIP SHA。移动时用 Finder 或 `ditto`，不要展开内部 symlink。

```sh
open /实际构建目录/dist/Lextide.app
/实际构建目录/dist/Lextide.app/Contents/MacOS/Lextide --cli --version
/实际构建目录/dist/Lextide.app/Contents/MacOS/Lextide --cli --help
```

命令执行时不要求源码目录为 cwd。Finder 启动不依赖 Python、uv 或 shell 激活环境。
现有 `.venv/bin/lextide`、`.venv/bin/tts-studio` 继续有效。App 内 `--cli` 是诊断和高级
操作入口，普通双击默认进入 GUI。`--desktop-worker` 和 `--supervise` 是内部协议；
后者在 frozen executable 中分派原有 G3 进程监管器，不更改父进程存活和取消规则。

## 安装和首次启动

1. 对比 `shasum -a 256` 与 build record。解压 ZIP，用 Finder 将完整 `Lextide.app`
   放到用户可写的 `~/Applications`，或用户有权限的 `/Applications`。无需安装器。
2. 双击启动。只读程序、图标、动态库保存在 Bundle；任务和配置保存在用户目录。
3. Settings → 检查 FFmpeg / ffprobe；必要时选择已安装的可执行文件，保存偏好并点击
   `Check local status`。缺少工具会给出状态提示，已知依赖不可用时禁止生成。
4. Edge 仍要求文本预览和本次显式授权；更改文本、语音设置或运行环境路径会撤销批准。
5. Kokoro 可选，未配置不会自动下载，也不会回退在线。导入、编辑和历史浏览仍可使用。
6. Library 先选中一个项目再 `Verify & open in Listen`，或双击条目；界面已有明确提示。
7. 关闭重开验证偏好和历史。重新安装时只替换 App，不删除用户数据。

开发版只有 ad-hoc 签名。带 quarantine 的下载包可能被 Gatekeeper 拒绝，此时应记录
未签名/未公证状态，等待正式签名包；本项目不提供清除 quarantine、关闭 Gatekeeper 或
全局降低系统安全设置的安装办法。本机成功启动不能证明干净 Mac 安装通过。

## 依赖边界

| 组件 | App 内 | 用户操作与限制 |
|---|---|---|
| Python 3.14 / PySide6 6.12 / Qt Cocoa、SVG、Multimedia | 包含 | GUI 不要求系统 Python；目标为 arm64，Bundle 声明 macOS ≥13，跨系统版本仍待实机验证 |
| Edge、aiohttp、certifi、导入器 | 包含 | 网络可用性不由本地状态检测保证，真实生成仍有逐次授权 |
| Qt 的 FFmpeg 动态库 | 包含 | 只用于 Qt Multimedia；不是 `ffmpeg` / `ffprobe` CLI 的替代品 |
| 外部 FFmpeg / ffprobe | 不包含 | 本轮选择方案 B；用户安装可信构建后在 Settings 配置。检测只确认可执行文件位置，后端保留真实解码验证 |
| Kokoro Python 3.13 / ONNX / phonemizer / eSpeak | 不包含 | 用户显式准备独立 runtime；App 不猜源码仓库路径 |
| 模型与声线（约 135.7 MiB） | 不包含 | 使用已有受管模型目录，或显式执行原有模型安装命令；SHA 验证规则不变 |

FFmpeg 外部工具应同时具备 `ffprobe`、MP3 解码及 `libmp3lame` 编码；建议使用与 G3
已验收相同的版本。G3 恢复会核对保存的 FFmpeg 版本，升级工具后不强行混用旧任务。
可参考 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html) 选择可信来源；本项目
不静默安装 Homebrew，也不将开发机 Homebrew GPL 构建复制进 App。
已配置的路径是用户本机路径；Bundle 本身不需要开发机的 `/opt/homebrew` 或仓库路径。

已有 Kokoro 用户直接在 Settings 选择原 `.venv/bin/python` 和模型目录，保持 venv
入口的 symlink，不把它手动解析成系统 Python。保存后重新检查状态。App 带有小型
`worker-source/lextide` 源码供外部 runtime 加载，不依赖开发仓库，也不加载其中 GUI。

若没有 runtime，先由用户从可信渠道安装 uv 和 Python 3.13。可把 Bundle 内两份锁定配置
复制到独立用户目录，再执行显式安装。以下命令仅适用于**该目录尚不存在**的新安装：

```sh
APP="$HOME/Applications/Lextide.app"
RUNTIME="$HOME/Library/Application Support/Lextide/offline-runtime"
if test -e "$RUNTIME"; then
  printf '%s\n' '已有 runtime 目录：保留原文件，请检查现有安装。'
else
  mkdir -p "$RUNTIME" &&
  cp "$APP/Contents/Resources/offline-runtime/pyproject.toml" "$RUNTIME/" &&
  cp "$APP/Contents/Resources/offline-runtime/uv.lock" "$RUNTIME/" &&
  UV_PYTHON_DOWNLOADS=never uv sync --frozen --project "$RUNTIME" --python python3.13
fi
"$APP/Contents/MacOS/Lextide" --cli engines --offline-python "$RUNTIME/.venv/bin/python"
"$APP/Contents/MacOS/Lextide" --cli models status
# 仅在明确愿意下载所列模型时执行下一条；安装器会显示来源、大小并校验 SHA。
"$APP/Contents/MacOS/Lextide" --cli models install --allow-download
```

该可选安装仍需用户配置工具，G5 没有提供全自动离线组件安装器。Python 缺失时命令失败，
不自动下载；用户可另行选择 [uv 官方 Python 安装流程](https://docs.astral.sh/uv/guides/install-python/)。
依赖总体体积取决于平台；本轮不重新下载模型或宣称此链已完成法律分发审查。
安装失败不影响已打包的 Edge GUI。App 的 Kokoro 网络隔离规则与原 G2/G3 相同。

## 用户数据与升级保护

继续使用 `~/Library/Application Support/Lextide/desktop`，无需迁移：

- `preferences.json`：主题、输出、历史和运行环境路径；不保存在线批准。
- 默认 `projects/`：job、原文、manifest、最终 MP3/SRT；用户也可指定其他输出目录。
- `~/Library/Application Support/Lextide/chunk-cache`：滤波前 PCM 缓存，与
  `desktop`、导出和模型分开；恢复使用实际样本元数据。
- `requests/`：短期私有 worker 请求；可能包含用户正文，0600 文件/0700 目录，不属于日志。
- `~/Library/Application Support/Lextide/models`：模型默认位置；已有自定义路径继续有效。

现有原子写入/锁不变；旧 schema 1/2/3 仍由已验收读取器处理。数据目录须可写。
不把正文写入诊断日志，不增加遥测。G5 不创建无界运行日志；测试日志由测试者显式保存
到 `.artifacts`。worker stderr 不展示原始异常。SIGKILL 遗留请求的既有行为没有被更改，
清理前需确认没有运行中的任务，不能把它们当作公共诊断附件。

## 签名、公证与发布门槛

当前使用 `local.lextide.development`，仅为开发用途，无他人域名。
`SIGNING_PENDING` / `NOTARIZATION_PENDING`。有效签名身份数为 0；工具存在不代表
账号、Developer ID 证书或公证授权存在。代码不会选择任何 Keychain 身份。

正式流程需另行确认发布主体、正式 Bundle ID、Apple Developer Program、Developer ID
Application 身份和适当的 notarization keychain profile；凭据不得放仓库或普通日志。
应在专门发布构建中采用已批准身份，PyInstaller `codesign_identity`、最小必要
entitlements 与 Hardened Runtime。当前 spec 固定 ad-hoc，不能直接将此 ZIP 视为正式包。

后续步骤（本轮未执行签名/上传）：

1. 完成许可证审查，确定合法分发内容；审查 Qt、Python 动态库和加载外部依赖所需的
   Hardened Runtime 权限，优先不增加禁用 library validation 等宽权限。
2. 对嵌套二进制/Framework 和最外层 App 从内到外用已批准 Developer ID 签名，并启用
   secure timestamp / runtime。不要把 `codesign --deep --sign` 当作发布签名策略。
3. `codesign --verify --deep --strict --verbose=2 "$APP"`，确认 Authority、TeamIdentifier、
   hardened flags；这只是签名检查，不是公证。
4. 用 `ditto -c -k --keepParent` 准备提交副本。凭据由用户另行安全存入 Keychain 后：
   `xcrun notarytool submit "$ZIP" --keychain-profile "$APPROVED_PROFILE" --wait`。
5. 只有 Apple 返回 Accepted 才 `xcrun stapler staple "$APP"`，再 `stapler validate`、
   `spctl --assess --type execute --verbose=4 "$APP"`。重新制作并记录最终 ZIP SHA。
6. 干净 Mac 上测试正常下载/quarantine、安装、移动、第一次启动、离线播放和升级数据保留。
   之后仍需用户明确批准才可公开发布。

依据：[Apple 公证说明](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、
[PyInstaller macOS 签名](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)。
应用既未 notarize 也没有上传 Apple；本轮不会读取 API Key、密码或证书私钥。

## 平台分工与已知边界

macOS 打包逻辑隔离在 `packaging/macos` 和构建脚本，当前只产 arm64，不宣称 Intel/
universal2、Windows或全部 macOS版本兼容。Windows 需本机构建 `Lextide.exe` 与 `.ico`、
Qt Windows插件；梳理 `fcntl.flock`、`O_NOFOLLOW`、POSIX mode、SIGINT/SIGTERM、
process groups、路径盘符与大小写、原子 rename、AppData及缓存目录、FFmpeg探测与
独立 Python/eSpeak DLL。外部进程 DLL 搜索路径清理也需 Windows 专门实现。
这些工作全部由用户另一台 Windows PC 上的 DeepSeek harness 独立开发、构建和测试；
当前 Mac Codex 只负责 macOS。本轮没有改变 G3 锁和信号语义。交接的是源码与契约，
不是把 `.app` 当作 Windows 程序。详见 `handoffs/to_windows/WINDOWS_PORT_HANDOFF.md`。

打包后的合成高负载响应、真实网络/离线长文、CPU/RSS、clean Mac、安装版用户体验
由 DeepSeek 验收。开发机窗口可见或轻量测试通过不能替代这些项目。
许可证具体版本、包含范围和未闭环条件见 [G5_LICENSE_AUDIT.md](G5_LICENSE_AUDIT.md)。

## macOS 后续顺序（本轮只记录）

G5 DeepSeek 与用户验收之后，再按用户批准顺序执行：

1. **English / 简体中文 GUI**：Settings 保存 Language，统一页面、按钮、对话框和错误
   提示；评估 QTranslator 或集中字符串资源。语言选择不改变合成正文、voice、字幕语言、
   manifest 或历史文件；G5 尚未改造字符串结构。
2. **英文 + 中文双语字幕**：英文在上，中文在下，字号约 70–80%、颜色稍浅，支持
   English Only / English + Chinese。英文 SRT 与原 cue 时间轴保持原样，译文独立保存，
   缺失则明确为空；先研究已有译文导入与逐句对齐。在线翻译必须单独授权上传，不能沿用
   Edge 授权；本地翻译模型留后续评估，G5 未安装模型或调用翻译 API。
3. 其他 macOS 增强。Windows 独立交接仅提供源码与契约，不占用当前 macOS 开发阶段。
