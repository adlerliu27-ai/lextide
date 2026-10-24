# G5 本地打包：许可证、二进制来源与签名审计

初审日期：2026-10-18；G5 续作复核：2026-10-20。以下路径相对于源码仓库。

**结论：本次产物只能标记为 `DEVELOPMENT_RELEASE_PENDING`。本审计支持已授权的本地 `.app` 构建与验收，不构成法律确认、公开分发许可或分发准备完成的声明。** 尚未完成的对应源码、二进制适用性映射、LGPL 修改/替换机制及项目/图形资产授权见下表。实际 bundle 清单须与本环境清单交叉核对；wheel 已安装不等于其全部内容被打包。

| 公开发布前待解决事项 | 当前证据与边界 |
|---|---|
| Lextide 自身许可证与图形资产范围 | `pyproject.toml` 未声明项目 license；用户设计包 PNG/SVG 仅已授权本地集成，未确认公开分发权利 |
| Qt/PySide/edge-tts/FFmpeg 对应源码及重新组合路径 | 已保存官方许可证与部分版权/第三方 attribution；没有提供完整匹配构建的源码、补丁、构建脚本和经验证的库替换/重新组合说明 |
| Qt 第三方组件与最终 native payload 一一对应 | 本文记录 wheel 内的 FFmpeg 与模块参考资料。官方页面是模块级超集；仍需核实最终每个 framework/plugin/静态并入的第三方组件，不能据此认定全部适用或完整 |
| Python 及其 native dependencies 的通知覆盖 | 已找到本机 CPython 许可证和 incorporated-software 文档；文档本身说明该清单不完整。OpenSSL 版本已实测，最终 bundle 中其它 native/static 成分仍需核对 |
| macOS Developer ID 与公证 | 本机只读检查为 **0 valid code-signing identities**；工具存在不等于账号、证书或公证凭据已配置。未使用任何证书或发送公证请求 |
| 外部服务与 optional offline runtime | edge-tts 的代码许可证不授予对上游语音服务的额外权利；Kokoro、phonemizer/eSpeak、模型另有许可证，未纳入本次 `.app` runtime 分发闭环 |

## 1. 证据、范围与可复核方式

本次读取 `.venv` 的 `importlib.metadata`、wheel RECORD/许可证文件、`otool -L`，并通过 `ctypes` 直接调用 Qt 自带 FFmpeg 五个动态库的 `*_license()` / `*_configuration()`；没有仅凭文件名判断许可证。

| 证据文件 | 内容 |
|---|---|
| `.artifacts/codex-g5/evidence/installed-distribution-license-inventory.json` | 本次安装环境的版本、metadata license、来源链接与实际 license 文件绝对路径；包括构建/测试工具，不能当作 bundle SBOM |
| `.artifacts/codex-g5/evidence/qt-ffmpeg-inventory.json` | 五个 wheel 原始 dylib 的 SHA256、字节数、实际 license/configuration、`otool -L` |
| `.artifacts/codex-g5/evidence/external-homebrew-ffmpeg.txt` | `/opt/homebrew/bin/ffmpeg -version` 与 `-buildconf` 的真实输出 |
| `.artifacts/codex-g5/evidence/signing-tools-summary.txt` | 签名身份数量、工具路径；不保存证书身份、指纹或凭据 |
| `packaging/macos/licenses/SOURCE_MANIFEST.json` | 每份离线原文的源 URL/本地来源、获取时间、字节数与 SHA256；保留原字节，不重新编写版权条款 |

基线：CPython **3.14.6**；PySide6-Essentials/Addons/shiboken6 **6.12.0**；PyInstaller **6.22.3**；pyinstaller-hooks-contrib **2026.8**。本地证据保存的是原 wheel 文件；PyInstaller 的 arm64 thinning、load-command 修改或 ad-hoc signing 可改变最终文件哈希，最终包应另留清单。

计划包内范围是 GUI、核心 Python runtime、实际依赖的 Qt frameworks/plugins 及其 FFmpeg dylib。FFmpeg 方案 B 使用用户配置的外部 `ffmpeg`/`ffprobe` 可执行程序；Homebrew 程序与依赖不应复制入包。Kokoro Python 3.13 runtime、ONNX/model/voices 与缓存是另行配置的 optional 资源，不应复制入包。

## 2. Python 与构建依赖清单

下列许可证来自本机对应版本 metadata 与实际 license 文件。精确来源链接保存在上述 JSON；原先 `THIRD_PARTY_NOTICES.md` 保留历史/开发背景，不替代随包原文和分发义务。

| 组件 | 版本 | 本次读取的许可证 | 预期边界 / 需要保留的内容 |
|---|---|---|---|
| CPython | 3.14.6 | PSF-2.0 及历史/并入组件条款 | 包内 interpreter/stdlib；保留 LICENSE 与 incorporated-software notices，核对 native/static dependencies |
| edge-tts | 7.3.1 | LGPLv3；`srt_composer.py` 单独 MIT | 包内核心；两种原文与版权都需保留；LGPLv3 还引用 GPLv3，冻结 PYZ 中的库不能被默认认定可替换 |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | 包内；主 LICENSE 及 vendor/llhttp/LICENSE |
| certifi | 2026.7.22 | MPL-2.0 | 包内 CA bundle；保留原文/来源，确认 MPL Covered Software source availability 与改动说明适用性 |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | 运行时传递依赖；原文/版权 |
| aiosignal | 1.4.0 | Apache-2.0 | 运行时传递依赖；原文/版权 |
| attrs | 26.1.0 | MIT | 运行时传递依赖；原文/版权 |
| frozenlist | 1.8.0 | Apache-2.0 | 运行时传递依赖；原文/版权 |
| idna | 3.20 | BSD-3-Clause | 运行时传递依赖；原文/版权 |
| multidict | 7.1.0 | Apache-2.0 | 运行时传递依赖；原文/版权 |
| propcache | 0.5.4 | Apache-2.0 | 运行时传递依赖；LICENSE **与 NOTICE** |
| tabulate | 0.10.0 | MIT | edge-tts 依赖；实际冻结模块按构建 inventory 核对 |
| typing_extensions | 4.16.0 | PSF-2.0 | 运行时传递依赖；原文/版权 |
| yarl | 1.25.1 | Apache-2.0 | 运行时传递依赖；LICENSE **与 NOTICE** |
| PySide6-Essentials / Addons / shiboken6 | 6.12.0 | metadata：LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 包内实际用到的 binding/Qt libraries；三个 wheel 的 RECORD 均未列出许可证文件，已另外保存官方原文；不得将此 metadata 覆盖到全部 Qt 第三方库 |
| pypdf | 6.21.1 | BSD-3-Clause | 包内；LICENSE |
| markdown-it-py | 4.2.0 | MIT | 包内；LICENSE **与 LICENSE.markdown-it** |
| mdurl | 0.1.2 | MIT | 包内；LICENSE |
| PyInstaller | 6.22.3 | GPL-2.0-or-later，含 bootloader exception；runtime hooks / additional runtime modules 为 Apache-2.0 | 构建工具与部分包内 loader/hooks；保留完整 COPYING.txt，不把 PyInstaller 的例外扩大到应用依赖 |
| pyinstaller-hooks-contrib | 2026.8 | standard hooks GPL-2.0-or-later；runtime hooks Apache-2.0 | 构建工具及可能内嵌 runtime hook；保留完整 LICENSE |
| altgraph / macholib / packaging / setuptools | 0.17.5 / 1.16.4 / 26.3 / 84.0.0 | MIT / MIT / Apache-2.0 OR BSD-2-Clause / MIT（vendor 另计） | 首先属于构建依赖；仅在实际包内时扩展 runtime notice，特别核对 setuptools vendor |
| pytest / ruff / iniconfig / pluggy / Pygments | 8.4.2 / 0.14.0 / 2.3.1 / 1.6.0 / 2.21.0 | MIT / MIT / MIT / MIT / BSD-2-Clause | 开发/测试依赖，不应因全 `.venv` 已安装就全量打包 |

PyInstaller 官方条款只对其 bootloader/related files 给出对应组合分发例外，runtime hooks 另采用 Apache-2.0；应用的 Qt、edge-tts、FFmpeg 等许可证仍分别适用。[PyInstaller 官方许可证](https://pyinstaller.org/en/stable/license.html)

## 3. Qt Multimedia 实际随带的 FFmpeg

实际目录：`.venv/lib/python3.14/site-packages/PySide6/Qt/lib/`。`libffmpegmediaplugin.dylib` 的 `otool -L` 显示它链接下列五库；不是对外部 Homebrew `ffmpeg` 的调用。

| wheel 原始文件 | 运行时许可证 | SHA256 |
|---|---|---|
| `libavutil.61.1.101.dylib` | LGPL version 2.1 or later | `2d2b0139dfce380955f490da1fa8d93cd8608da9523fa36225728dd77ba98930` |
| `libavcodec.63.1.101.dylib` | LGPL version 2.1 or later | `b63abb1dda408a6f0c4b77380b0b40f197f947c6cbf91d1848bc59594aab4da9` |
| `libavformat.63.1.101.dylib` | LGPL version 2.1 or later | `5013594c4d657b0dbd5286c2099d347453460e991d8b836db9b44c9f4f2c85b8` |
| `libswresample.7.1.101.dylib` | LGPL version 2.1 or later | `e120197c2699b84f668fc97a57d18d54561239aceabcfdb74306ab227449851a` |
| `libswscale.10.1.101.dylib` | LGPL version 2.1 or later | `13cf067ce307493db876e94b470da8ad6cad1676b31563958c32d67d57d409b9` |

`av_version_info()` 返回 **9.0.1**。五库的 arm64 slice 均报告相同配置：

```text
--disable-programs --disable-doc --disable-debug --enable-network --disable-lzma
--enable-pic --disable-vulkan --disable-v4l2-m2m --disable-decoder=truemotion1
--disable-avdevice --disable-avfilter --enable-shared --disable-static
--disable-libxcb --disable-xlib --prefix=/usr/local/FFmpeg-n9.0.1
--enable-cross-compile --arch=arm64 --cc='clang -arch arm64'
```

本次调用未出现 `--enable-gpl`、`--enable-version3` 或 `--enable-nonfree`。原 wheel 二进制为 x86_64/arm64 双架构，运行时查询只执行了 arm64 slice。外部依赖可见 Apple 系统 frameworks，以及系统 libz/libbz2/libiconv 等；完整 `otool` 原文在 evidence JSON。

Qt 6.12 官方页对应 FFmpeg 9.0.1，并列出该 FFmpeg 副本内还存在 BSD、ISC、MIT、MPL 等条款，因此单独保存 LGPL 文本不足以代表所有 notice。已保存官方 FFmpeg attribution 全页和对应 LGPL 文本；尚未取得这组 Qt 预构建二进制的完整匹配源码、补丁和构建 provenance。[Qt Multimedia](https://doc.qt.io/qt-6/qtmultimedia-index.html)、[Qt FFmpeg attribution](https://doc.qt.io/qt-6/qtmultimedia-attribution-ffmpeg.html)

## 4. 外部 FFmpeg 与 optional Kokoro

`/opt/homebrew/bin/ffmpeg` 实测版本为 **9.0.2**，构建配置包含 `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265`，还有 SVT-AV1、Opus、LAME、dav1d、libvmaf、libvpx、OpenSSL 等功能。不能将这个构建标为 LGPL-only。FFmpeg 官方说明 GPL 组件及 build configuration 会影响整体条款；本次采用外部工具配置，不复制这个二进制及 Homebrew 依赖链。[FFmpeg 官方 licensing](https://ffmpeg.org/legal.html)

Qt 的 LGPL FFmpeg playback dylib 仍会随 `.app` 存在，和外部 FFmpeg policy B 是两条独立边界。后续若改为捆绑外部 FFmpeg/ffprobe，应重做该二进制构建配置、全部依赖、源码和通知审计。

`offline-runtime/uv.lock` 的 Kokoro/ONNX/phonemizer/eSpeak 与模型是另行安装范围。历史 `THIRD_PARTY_NOTICES.md` 已记录 phonemizer GPL-3.0-or-later、espeakng-loader wrapper MIT 与 eSpeak NG GPL-3.0-or-later；本次没有重新安装或独立复验整条 optional 链。独立进程/目录不自动消除其分发义务；不能将 optional runtime 描述为 MIT/Apache-only。实际产物必须确认没有 `.onnx`、voices、offline venv 或模型缓存。

## 5. LGPL 与通知工作剩余边界

Qt 官方 LGPL 指引要求评估显著使用声明、许可证文本、对应源码、修改/反向工程权利以及用户替换或重新组合库的能力。保留 shared dylib 是有利结构，但不是对这些条件的验证。edge-tts 被放入冻结 Python archive 后，也需要专门验证重建/替换方案。不能仅凭 `.app` 可以启动或者复制了 LICENSE 就宣称满足全部要求。[Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)

`Qt-GPL-exception-1.0.txt` 已作为官方原文保留；仅在上游明确带该 exception 的文件上适用，不能据此给全部 Qt/PySide/第三方 runtime 提供总括豁免。

已保存的 Qt attribution 是官方模块级参考超集；并不意味着包内实际包含页面上的每一个组件。正式发布前需要以最终 framework/plugin/native/static inventory 与 Qt 6.12.0 对应源码/SBOM 对照，补齐精确版权、许可证、NOTICE、源码与修改记录。Qt 官方明确要求按实际分发的第三方组件履行条款。[Qt Third-Party Code](https://doc.qt.io/qt-6/licenses-used-in-qt.html)

## 6. 构建脚本应复制的本地许可证来源

项目目录 `packaging/macos/licenses/` 整体复制入 bundle 的 `licenses/`，保持相对层次；其中 `SOURCE_MANIFEST.json` 记录原始来源与 SHA256。它不替代 installed package 的 notice。构建脚本还应逐发行包读取 RECORD，保留其中所有 LICENSE/COPYING/NOTICE 文件的相对结构，避免同名覆盖。

开发环境基准路径：`<project>/.venv/lib/python3.14/site-packages/`。

| 包 | 相对此基准的实际原文路径 |
|---|---|
| edge-tts | `edge_tts-7.3.1.dist-info/licenses/LICENSE` |
| aiohttp | `aiohttp-3.14.5.dist-info/licenses/LICENSE.txt`；`aiohttp-3.14.5.dist-info/licenses/vendor/llhttp/LICENSE` |
| certifi | `certifi-2026.7.22.dist-info/licenses/LICENSE` |
| aiohappyeyeballs | `aiohappyeyeballs-2.7.1.dist-info/licenses/LICENSE` |
| aiosignal | `aiosignal-1.4.0.dist-info/licenses/LICENSE` |
| attrs | `attrs-26.1.0.dist-info/licenses/LICENSE` |
| frozenlist | `frozenlist-1.8.0.dist-info/licenses/LICENSE` |
| idna | `idna-3.20.dist-info/licenses/LICENSE.md` |
| multidict | `multidict-7.1.0.dist-info/licenses/LICENSE` |
| propcache | `propcache-0.5.4.dist-info/licenses/LICENSE`；`propcache-0.5.4.dist-info/licenses/NOTICE` |
| tabulate | `tabulate-0.10.0.dist-info/licenses/LICENSE` |
| typing_extensions | `typing_extensions-4.16.0.dist-info/licenses/LICENSE` |
| yarl | `yarl-1.25.1.dist-info/licenses/LICENSE`；`yarl-1.25.1.dist-info/licenses/NOTICE` |
| pypdf | `pypdf-6.21.1.dist-info/licenses/LICENSE` |
| markdown-it-py | `markdown_it_py-4.2.0.dist-info/licenses/LICENSE`；`markdown_it_py-4.2.0.dist-info/licenses/LICENSE.markdown-it` |
| mdurl | `mdurl-0.1.2.dist-info/LICENSE` |
| PyInstaller | `pyinstaller-6.22.3.dist-info/licenses/COPYING.txt` |
| hooks-contrib | `pyinstaller_hooks_contrib-2026.8.dist-info/licenses/LICENSE` |

CPython 原文为 `/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/LICENSE.txt`；其本机对应 documentation 原文为 `/Library/Frameworks/Python.framework/Versions/3.14/Resources/English.lproj/Documentation/_sources/license.rst.txt`，渲染版本为同级 documentation 的 `license.html`。已保存到项目 notices 目录。Python 官方也说明 incorporated-software 清单不完整，须结合实际二进制核查。[Python licenses](https://docs.python.org/3.14/license.html)

## 7. 签名与公证只读结果

```text
/usr/bin/security find-identity -v -p codesigning → 0 valid identities found
/usr/bin/xcrun --find notarytool → /Library/Developer/CommandLineTools/usr/bin/notarytool
/usr/bin/xcrun --find stapler → /Library/Developer/CommandLineTools/usr/bin/stapler
/usr/bin/xcrun --find codesign → /usr/bin/codesign
/usr/bin/xcode-select -p → /Library/Developer/CommandLineTools
```

未枚举/输出私钥、证书详细身份、Keychain 凭据或 notary profiles。没有做 Developer ID 签名或公证提交。本地 PyInstaller arm64 bundle 的 ad-hoc signature 只作为本地运行完整性机制；不能称为已获 Apple 认可的发布签名。最终 bundle 的实际签名状态须由构建验收另行记录。

后续公证应按 Apple 当前流程，先获得用户指定身份/账号与提交授权，再进行 Developer ID、hardened runtime/entitlements、notarytool 提交、结果核验与 stapling。此处不替用户选择或申请证书。[Apple notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

## 8. 本次没有验证的事项

- 未给出法律合规结论，也未获得项目或图形资产公开发布许可。
- 未测试修改 Qt/edge-tts/FFmpeg 后替换与重新组合的完整流程；未验证 future hardened runtime/signature 与该流程的兼容性。
- 未构建 Windows 包；Windows 对应 DLL、Qt plugins、MSVC runtime、签名与安装范围需在 Windows 实机重新列清单并验证。
- 未将官方 attribution 参考页面当作完整对应源码或最终 SBOM；未将本机签名工具存在视为公证授权/凭据已具备。

## 9. 续作实际 Bundle 对账（2026-10-20）

候选包逐项检查了 **574 个普通文件和 141 个 symlink**，文件 SHA 与清单相符，没有断链
或越界链接。最终交付包会因文档/通知增补改变文件数，精确清单以该包的
`bundle-inventory.json` 为准，不能沿用候选包哈希。实包包括：

- Python framework；15 个 Qt frameworks：Concurrent、Core、DBus、Gui、Multimedia、
  MultimediaWidgets、Network、OpenGL、Qml、QmlMeta、QmlModels、QmlWorkerScript、
  Quick、Svg、Widgets；Cocoa、SVG、图像格式、媒体、TLS 等插件。
- 上述 5 个 Qt FFmpeg 动态库，以及 PySide/shiboken、OpenSSL 3.5.7、
  **libzstd 1.5.7**。其余 Python 扩展与冻结模块按 inventory / PyInstaller TOC 对账。
- native Zstandard 通过 `ZSTD_versionString()` 得到 1.5.7；源自 CPython 发行包，
  arm64 的 10 个 file-backed section 与打包前一致。上游提供 BSD/GPLv2 双选项；
  本地包记录 **BSD-3-Clause** 选项并保留原始 LICENSE、另附 COPYING；仍需发布审查。
  [zstd 1.5.7 LICENSE](https://github.com/facebook/zstd/blob/v1.5.7/LICENSE)
- 没有 FFmpeg CLI、Kokoro runtime、ONNX 模型、声线、缓存或历史音频；没有附带字体文件，
  使用 macOS 系统字体。品牌 PNG/SVG 保留用户设计原字节；其公开范围与条款现已由根目录 `TRADEMARKS.md` 与 `ASSETS_LICENSE.md` 确定（品牌保留，个人/非商业教育可用，商业品牌使用需书面授权）。

QtGui hook 曾收集未使用的 Virtual Keyboard（官方为 Commercial/GPLv3），本机原生输入
不需要它。现已同时从 binaries 和 datas/symlinks 排除两个 framework 及其插件；
候选包路径命中为 0，其他 Mach-O 未链接它们。此排除不取消其余 Qt 的许可证义务。
[Qt Virtual Keyboard 官方许可](https://doc.qt.io/qt-6/qtvirtualkeyboard-index.html)

候选包全部文件未命中开发者用户名或仓库绝对路径 literal，且没有 editable-install
`direct_url.json`。随包 `licenses/components.json` 是 Python 依赖/通知映射，
native 列表与上述证据补足其边界；仍不是已完成法律审查的完整 SBOM。

