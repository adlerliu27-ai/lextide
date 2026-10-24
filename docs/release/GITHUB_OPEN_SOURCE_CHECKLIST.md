# Lextide v0.6.1 源码首发检查清单

更新：2026-10-24。G6 工程和用户验收已完成：**G6_PASS / USER_ACCEPTED**；
BUG-G6-1、BUG-G6-2 均 CLOSED。已验收业务基线为
`1f4d1e7a083d6134165bdbecd1f316bf77a4a0cd`。本轮只整理发布文档、资产清单和隐私配置，
不修改业务源码、测试、依赖锁或 CI，不生成新的 App。

**源码工程检查、权利放行、实际 GitHub 发布是三种不同状态。**

- `github_source_release_readyness`：**READY** —— 所有者已批准 MIT 源码许可与品牌资源条款
  （`LICENSE`、`TRADEMARKS.md`、`ASSETS_LICENSE.md`），资产范围见
  [ASSET_RELEASE_MANIFEST.md](ASSET_RELEASE_MANIFEST.md)。
- `macos_binary_distribution_readiness`：**PENDING** —— 未签名、未公证，本仓库不含 App。

本仓库由独立干净快照建立新的初始历史，不包含原私有开发历史。发布后的核对结果保存在
开发侧的发布结果报告中，不写入本公开仓库。

## 1. 四项必须由所有者确认的内容

| 项目 | 当前状态 | 已确定的决定 |
|---|---|---|
| 自有代码许可与版权 | **APPROVED** | 采用 MIT；`Copyright (c) 2026 Mingjia Liu`；范围见根目录 `LICENSE` |
| 图形/品牌/截图 | **APPROVED** | 源码 MIT + 品牌权利保留 + 非商业教育引用许可；商业品牌使用需事先书面授权。逐文件分类见 [ASSET_RELEASE_MANIFEST.md](ASSET_RELEASE_MANIFEST.md)，条款见 `TRADEMARKS.md` 与 `ASSETS_LICENSE.md` |
| 最终内容人工复核 | **APPROVED** | 已复核最终清单/哈希、图片、第三方通知与公开 Git 身份；不含私人材料或无权公开内容 |
| 发布目标和操作 | **APPROVED** | 个人账号 `adlerliu27-ai`、仓库 `lextide`、`main`、Public、`v0.6.1` tag；先私有暂存再转 Public |

完整资产许可未确认时，不得仅删除资产声明而保留图形对外发布；必要运行资源的权利不清
继续阻塞，非必要资源只有在明确决定排除并复核引用后才可移除。

## 2. LICENSE 的准备与生效

根目录 **`LICENSE` 已生效**：标准 MIT 正文，`Copyright (c) 2026 Mingjia Liu`，
不含占位符或审查包装文字。此前用于评审的草稿文件已移除，避免与生效许可冲突。

MIT 覆盖有权授权的原创代码、测试、脚本、配置和文档文字，要求保留版权与许可声明。
第三方原文、模型、声线、字体、二进制、品牌和图形资源分别处理：

- 品牌资产（应用图标、品牌符号）：`TRADEMARKS.md` + `ASSETS_LICENSE.md`，**不适用 MIT**。
- 功能图标与设计 tokens：与 MIT 兼容，以便界面可随代码复用。
- 文档截图：允许 README 展示、个人学习与非商业教育引用，需注明来源。
- 第三方依赖：保留原许可与声明，见 [THIRD_PARTY_NOTICES](../../THIRD_PARTY_NOTICES.md)。

逐文件分类与证据见 [ASSET_RELEASE_MANIFEST.md](ASSET_RELEASE_MANIFEST.md)。
本清单不是法律意见或权属保证。

依据：[OSI MIT 标准文本](https://opensource.org/license/mit)、
[GitHub 仓库许可指引](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)，2026-10-24实时核对。
这份检查清单不是法律意见或权属保证。

## 3. 源码与二进制分发边界

- 源码候选保留 Python/配置/锁/测试/文档/必要资源、翻译 JSON、Clean English 和构建脚本。
- 无 `.git` 旧历史、`.venv`、`.artifacts`、模型/voices、任务/音频、Token/Cookie、研究材料、App/ZIP/DMG。
- 依赖与模型由用户按 README 单独安装；不提供来源不明的模型或第三方二进制。
- 源码中选留的上游 LICENSE/COPYING/NOTICE 原文不重新许可；保留原作者和来源。
- `packaging/macos/licenses/SOURCE_MANIFEST.json` 是历史构建审查资料，其 HTML 引用按
  源码导出策略排除。它不是此次源码清单；当前文件以候选 manifest 为准。
- PySide6/Qt、edge-tts、FFmpeg、Kokoro/phonemizer/eSpeak 等原许可与适用义务继续有效。
  参阅 [第三方声明](../../THIRD_PARTY_NOTICES.md) 和 [G5二进制审查](G5_LICENSE_AUDIT.md)。
- Apple Developer ID、公证和二进制 LGPL/GPL 分发审查仍 PENDING，**不是纯源码开源的
  Apple 前置条件**。本次不发布已有未公证 App。

## 4. 身份、快照与审查

首版采用独立干净快照；不直接推送包含私人路径的原开发历史，不 reset/clean/rewrite。
旧 G6.1 源码候选来自已验收 commit，已由双方验证；发布文档更新后的候选必须另存，
不能将新文档套用旧 ZIP 哈希。

此次文档修改不由 Codex commit。候选需记录：已验收基线commit、精确文档/config overlay、
每个文件SHA256、ZIP、未提交文档状态；**不要声称文档overlay已属于基线commit**。
首次公开 commit 由 DeepSeek 在新的独立目录建立；不要把原目录或其 `.git` 复制过去。

逐项核对：

- [ ] 来源、overlay、逐文件和ZIP哈希一致，解压无穿越/重复路径/符号链接；所有文件低于导出上限。
- [ ] Gitleaks覆盖候选、原refs/reflogs和存储blobs；人工补查路径、fixture、图片和隐藏配置。
- [ ] 候选不含私有材料；Gitleaks零命中只表示规则范围内未发现，不替代人工检查。
- [ ] 新独立Python3.13环境能安装；CLI可用、翻译/图形资源完整。
- [ ] 离线CI、Ruff、format、compile通过；业务文件与已验收基线逐字节一致。
- [x] 有效 LICENSE、资产范围、文件清单与最终上传 SHA 已记录。
- [x] 新仓库暂存文件与批准清单一致；公开作者姓名与邮箱已获准（不使用私人本地配置）。

本地结果由随候选交接的审计报告提供，不将私人证据或绝对本机路径放进公开仓库。

## 5. 最小CI与复现边界

[CI配置](../../.github/workflows/ci.yml)：macos-latest、Python3.13、uv0.12.5；actions固定到commit，
权限contents:read、checkout不持久化凭据。依赖安装可访问包仓库；之后离线测试使用
conftest禁止DNS/socket，Edge及下载由fake覆盖。不安装Qt、模型、Apple证书或Kokoro runtime。

命令见 [CONTRIBUTING](../../CONTRIBUTING.md)。13个文件涵盖CLI/授权、jobs、models、
subtitles/G5markers/G6.1quotes、document/storage、packaging、两类Cleaner与源码导出。
G6.1 已验收候选为 382 项通过；本次最终候选已在隔离 Python 3.13 环境中独立重新执行。
远端 Actions 的真实结果以运行时日志为准，本项目不预先宣称通过。

## 6. DeepSeek执行与发布后检查

在所有必要授权到位后，用正确账号的私有仓库暂存可减少误公开风险。已有`lextide`需先
检查，不覆盖。只推新的main初始历史；私有Actions日志也要在转Public前检查。
确认有效LICENSE、内容清单、最终SHA、目标账号和Public授权后才更改可见性。

发布后核对owner/URL/可见性/main/initial SHA、README和图形、许可证识别、文件完整性、
Actions真实状态、另目录clone安装测试；tag仅在获准后添加。此次不上传macOS App或创建
带二进制的Release。结果由DeepSeek返回，状态为PUBLISH_BLOCKED、PRIVATE_STAGING_READY、
PUBLIC_PUBLISHED_VERIFICATION_PENDING或PUBLIC_RELEASE_PASS。

G6_PASS 保持不变。本轮不开发 G7、自动翻译、OCR、Windows 或新音频功能。
