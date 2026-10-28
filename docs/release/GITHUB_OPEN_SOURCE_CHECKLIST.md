# Lextide 0.9.0 源码发布检查清单

当前目标是 **`G9_STABLE_RELEASE_CANDIDATE`**。源码候选、独立验收通过、GitHub 实际发布是三个不同状态。G9 尚未完成 DeepSeek 独立验收和用户最终确认；不得据本清单宣称 `G9_PASS` 或 `PUBLIC_RELEASE_PASS`。

2026-10-26 通过只读 GitHub 接口核对：公开 `main` 为
`3cd6c781e860100555792bb738e65d72fabfb67c`；已发布 `v0.6.1` 指向
`341f457591dae91d083cc14b0836c6f00335cbf9`。README 顶部保留官方透明 SVG 的 64 px Logo。
发布执行时必须重新读取远端状态，不能假设 main 此后没有变化。

## 1. 已确认授权与待完成门槛

| 项目 | 状态与范围 |
|---|---|
| 自有源码 | **APPROVED**：MIT，`Copyright (c) 2026 Mingjia Liu`；以根目录 `LICENSE` 为准 |
| 品牌与图形 | **APPROVED**：遵循 `TRADEMARKS.md`、`ASSETS_LICENSE.md` 和 [资产清单](ASSET_RELEASE_MANIFEST.md)，不扩大既有授权 |
| 0.9.0 源码发布意愿 | **CONDITIONALLY AUTHORIZED**：用户已授权在独立工程验收、真实用户验收和安全门槛全部通过后，由 DeepSeek 执行 |
| G9 工程与用户验收 | **PENDING**：须有真实结果，不能以单元测试替代用户验收 |
| 最终源码候选审计 | **PENDING PER CANDIDATE**：对最终 commit、ZIP、清单和 SHA-256 逐次核对 |
| macOS 二进制分发 | **PENDING**：开发版是 ad-hoc，未完成 Developer ID、公证和适用的二进制分发审查；本次公开目标为源码 |

发布条件为 `engineering_status = PASS`、`manual_acceptance_status = USER_ACCEPTED`、
`github_release_readiness = READY` 同时成立。已批准的源码许可与阶段发布意愿无须重复询问；
实际登录仍必须使用正常的账号授权流程。P0/P1、未授权上传、敏感信息、数据损坏或第三方权利问题必须阻止发布。

## 2. 保护现有公开历史

- 使用最新 Public main 的独立干净 clone 作为更新基础，只追加经过审查的源码差异。
- **禁止**推送原始私人开发历史、force push、重写 main、删除或移动 `v0.6.1`。
- 保留 README 64 px Logo、GitHub About 中性描述，以及 `LICENSE`、`TRADEMARKS.md`、`ASSETS_LICENSE.md`、`THIRD_PARTY_NOTICES.md`。
- G7/G8 功能可在验收后的 0.9.0 中一起公开，不虚构未实际发布过的 `v0.7.0`、`v0.8.0` 标签。
- Codex 只完成开发与交接；DeepSeek 在门槛满足后执行追加 commit、`v0.9.0` tag 与源码 Release。

## 3. 许可和分发范围

根目录 MIT 许可覆盖有权授权的原创 Python、测试、脚本、配置与文档文字。第三方代码、模型、声线和许可原文仍遵循各自条款，见 [THIRD_PARTY_NOTICES](../../THIRD_PARTY_NOTICES.md)。

- 品牌符号与 App icon 不采用 MIT，按品牌保留条款使用。
- 功能图标与 design tokens 按既有资产许可使用。
- README 截图仍是已审阅的自撰示例，标明历史界面版本；新截图须另审私人路径、正文和权利。
- 本次没有新增翻译、OCR、模型或字体依赖。源码开源不等于模型和二进制可以一并分发。
- FFmpeg、Kokoro runtime 和模型由用户明确安装/配置；开发版构建不代表干净 Mac 安装已通过。

## 4. 最终源码候选检查

`scripts/prepare_source_snapshot.py` 只导出 allowlist 内的已跟踪文件，生成无旧 `.git` 历史的源码目录、ZIP 和逐文件 manifest。输出必须是新的独立或忽略目录。工具默认将候选标记为待放行；它不代替本阶段验收结论。

- [ ] 软件版本为 0.9.0，来源 commit、修复 commit、依赖锁、Python 版本可追溯。
- [ ] 全部必要源码、测试、GUI/翻译资源、README、学习指南和构建脚本齐全。
- [ ] 无 `.git` 私人历史、`.venv`、`.artifacts`、用户任务、音频、模型、缓存、内部交接或研究资料。
- [ ] 无 Token、Cookie、密码、签名材料、私人绝对路径和未经授权的第三方素材。
- [ ] Gitleaks 与人工审查覆盖最终文件和准备追加到公开仓库的差异；扫描结果脱敏并留在候选之外。
- [ ] ZIP 无路径穿越、重复/大小写冲突路径、逃逸链接或异常文件；CRC、SHA-256 与清单一致。
- [ ] 所有公开文件在大小限制内；准备暂存的文件与批准的最终清单逐项一致。
- [ ] 许可证及品牌文件与已批准的公开基线一致；README 功能与实际代码一致。

原开发历史继续保留在私人仓库。Gitleaks 零命中只表示规则范围内未发现秘密，不证明原历史可以公开。公开候选不携带原历史，后续更新必须保留已经公开的历史。

## 5. 独立离线验证

[CI](../../.github/workflows/ci.yml) 使用 macos-latest、Python 3.13、uv 0.12.5；Actions 固定 commit，权限 `contents: read`，checkout 不持久化凭据。依赖安装可访问包仓库，测试阶段由 `tests/conftest.py` 阻断 DNS/socket；不安装 Qt、模型或证书，不上传文本。

- [ ] 从最终 ZIP 解压到新的独立目录，创建独立 Python 3.13 环境。
- [ ] 按 [CONTRIBUTING](../../CONTRIBUTING.md) 和实际 CI 列表运行，不复用旧阶段通过数量。
- [ ] Ruff、format、compileall、CLI/version 和离线测试全部通过。
- [ ] 确认候选验证前后哈希一致；缓存、日志、venv 和测试临时文件存放在候选之外。
- [ ] G9 GUI、真实 Edge/Kokoro、手动中文字幕、用户体验由独立验收报告分别记录。

## 6. DeepSeek 发布及发布后验证

门槛全部满足后，核对正确账号 `adlerliu27-ai` 与仓库 `lextide`，在最新 main 上应用经过审查的差异并创建新的公开提交。提交前检查 staged diff 和文件清单；不得用本机私人历史替换公开仓库。源码 Release 可只使用 GitHub 自动生成的 source archives，不上传未公证 App ZIP。

发布后必须记录并核对：

- 仓库 URL、所有者、Public、main 的新 commit 与其父提交关系。
- `v0.9.0` 指向正确公开 commit，`v0.6.1` 仍指向原 commit。
- GitHub Actions 的真实状态和运行 URL；失败不能用本地测试结果替代。
- 公开 clone 后再次运行离线安装/测试，核对版本、源码清单和 SHA-256。
- README Logo、许可证、资产链接完整，没有私人文件或未经批准二进制附件。

独立验收与实际发布结果分别留在开发侧交接报告中，不把私人机器路径、测试文章或完整诊断日志上传公开仓库。未经实际上传和复核，不使用 `PUBLIC_RELEASE_PASS`。
