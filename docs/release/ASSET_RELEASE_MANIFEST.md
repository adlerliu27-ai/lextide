# Lextide 0.6.1 — Asset release manifest

**资产公开状态：`AUTHORIZED`（2026-10-24）。** 所有者已批准资源发布条款：源码采用 MIT；品牌资产（应用图标、品牌符号）权利保留，个人与非商业教育使用可用，商业品牌使用需事先书面授权；功能图标、设计 tokens、文档截图分别适用 `ASSETS_LICENSE.md` 中的对应条款。本清单记录逐文件分类与审查事实，条款正文以根目录 `TRADEMARKS.md`、`ASSETS_LICENSE.md` 为准；它不是法律意见，也不构成对第三方权利的保证。

审查日期：2026-10-24。基线：`1f4d1e7a083d6134165bdbecd1f316bf77a4a0cd`；审查对象为 G6 修复后的干净源码候选中的原始字节。本次未改动任何图形、业务源码、原始设计包或历史报告。

| 已核验项目 | 结果 |
|---|---|
| 图形 | **34 文件 / 547,697 bytes**：3 PNG + 31 SVG；与 G6 §5.4 相符 |
| 补充审查 | `design-tokens.json` 与资产 `README.md`；合计 **36 文件 / 551,729 bytes** |
| 字节身份 | 36/36 与基线 Git 文件和当前工作树相同；31/31 设计包来源文件与只读原件相同 |
| 源码候选内 ICNS / ICO / 字体 | **0 / 0 / 0**；未发现 TTF、OTF、TTC、WOFF、WOFF2 文件 |
| PNG 内容及元数据 | 实际查看全部 3 张 PNG；无可见私人信息；无 PNG 文本/EXIF chunk 或 IEND 后附加数据 |
| SVG | 31/31 XML 可解析；未见外部资源引用、script、event handler、foreignObject、嵌入 image 或 metadata 元素；实际查看品牌 SVG 渲染图与 30 个图标 contact sheet |
| 当前 GUI 静态引用 | 19 个图形有引用；13 个图标未发现引用；design tokens 无运行加载点 |

## 来源证据与判断边界

| 代码 | 来源证据 |
|---|---|
| S1 | 两个品牌文件均与所有者只读 **Lextide UI Design Pack** 的 `assets/brand/` 同名原件逐字节相同。其 README 说明沿用并更名先前 **TTS Studio Logo Pack** 图形；Logo Pack 记录原始设计在 ChatGPT 中确认，生产矢量由其轮廓重绘。PNG 首次加入 `9408b68`，symbol SVG 首次加入 `ab0f961`。这能追溯材料链，不能替代所有者公开授权。 |
| S2 | 28 个通用线条图标与 UI Design Pack 的 `assets/icons/` 同名原件逐字节相同，首次加入 `9408b68`。原包 `developer/README-icons.md` 声明是专门创建的通用 glyph，并声明未分发 Lucide / SF Symbols 图像。此为来源文件声明，本审查未独立证明创作过程或权属。 |
| S3 | `design-tokens.json` 与 UI Design Pack 的 `developer/design-tokens.json` 逐字节相同，首次加入 `9408b68`。其 `meta.version=1.0.0`；现有 `theme.py` 使用后续获准的 1.1 色彩常量，没有加载此 JSON。 |
| S4 | 两个三点折线 chevron 首次加入 `34e11eb`。资产 README 与本地 G6 visual review 记载其为项目自行编写，SVG 仅含 `M2 2 L6 6 L10 2` 路径及不同主题颜色。项目已确认公开许可，见 `ASSETS_LICENSE.md` §2。 |
| S5 | 两张 PNG 首次加入 `34e11eb`；本地 G6 Cocoa capture 脚本、执行记录及 PNG 清单可追溯。真实 Qt 窗口以自撰四句英文示例截取；无音频生成或加载。截图为 0.6.0 界面，README 已说明 0.6.1 新增 Cleaning Mode。截图含品牌/UI 资产，公开范围须一并确认。 |
| S6 | 项目编写的资产来源说明，首次加入 `9408b68`，`34e11eb` 更新。文字与其中描述的资产权利应分别处理。 |

运行必要性是对当前源码引用的静态判断：`app.py` / `window.py` 引用品牌 PNG，`theme.py` 渲染 symbol 与两种 chevron，`window.py` 的导航、`_add_icon()` 和播放按钮引用其余图标。CLI 不依赖这些 GUI 图形。标为“是”意为保持当前验收界面需要保留对应资源；不表示缺失必然导致启动失败。标为“未发现”不等于已验证删除安全，若日后排除，仍需重新核对最终 payload 与 GUI。

隐私检查代码：**P1** = 已检查 UTF-8/XML/JSON/Markdown 内容，未发现个人路径、邮箱或凭据形态；**P2** = 逐张查看截图，只有自撰示例和 UI，未见姓名、账号、私人路径或研究材料；**P3** = 实际查看品牌 PNG，仅白底蓝色符号。PNG 另已检查文本/EXIF chunks；SVG 中标准 namespace URI 只是声明，未发现外部资源加载引用。以上是可见内容与结构审查，不是全能隐私检测。

历史推荐代码：**R1** = 纳入 GUI 源码资源；**R2** = 曾列为可考虑排除的非运行必需素材（本次**全部保留**，以便界面完整，已由 `ASSETS_LICENSE.md` 覆盖）；**R3** = README 展示图；**R4** = 来源说明文档。推荐代码仅反映当时的建议，最终范围以 `ASSETS_LICENSE.md` 与下表分类为准。

## 逐文件清单

路径相对源码候选根目录。SHA-256 为原始字节完整摘要；来源、隐私、建议代码按上文解释。

| 相对路径 | bytes | SHA-256 | 来源 | 当前运行必要性 | 公开授权 | 隐私检查 | 推荐 |
|---|---:|---|---|---|---|---|---|
| `docs/images/g6-chinese.png` | 230675 | `53af8f0bff7df851d2bbd6a40b5fb6ec0322277e436411e429e4046f6f669d34` | S5 | 否；README 展示 | **DOCUMENTATION** | P2 | R3 |
| `docs/images/g6-english.png` | 235139 | `3419e0b5ed68a05d0b3d8e27b8dbcb27ed99e702a5596d198f91b14262bd0ff1` | S5 | 否；README 展示 | **DOCUMENTATION** | P2 | R3 |
| `src/lextide/desktop/assets/README.md` | 1319 | `1700d46c3be53b7b7ba4db9928b3fa94c919c72b7883683aeae3380909c115db` | S6 | 否；来源说明 | **OPEN_FUNCTIONAL_ASSET** | P1 | R4 |
| `src/lextide/desktop/assets/brand/lextide-app-icon-1024.png` | 62358 | `cffae7b808a294750ea4dd1b78879c3d0f7dd0293e806df641e4d2b3d6cc1565` | S1 | 是；窗口/App 图标 | **BRAND_RESERVED** | P3 | R1 |
| `src/lextide/desktop/assets/brand/lextide-symbol-editable.svg` | 11438 | `24d4049f636b5742f8cb599ed01c39a106bbf7d550638313e1d0529cb4d03669` | S1 | 是；侧栏/Overview 品牌图 | **BRAND_RESERVED** | P1（旧产品名） | R1 |
| `src/lextide/desktop/assets/design-tokens.json` | 2713 | `d91550ce7ddbc1f81c23aa7c0ed7ab84371eaa047c7403db0cf725fb12336263` | S3 | 否；1.0 设计参考 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/alert-circle.svg` | 266 | `f430effa389f45e41a02d6cfb015937c45efb9802c71169f13a0a04f597d0451` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/arrow-right.svg` | 242 | `7e3cab7c4a23e5875bfd2a01d219b2372d039d7485927971a3741a13bc849c41` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/check-circle.svg` | 266 | `51bb35e483528aa5124e6450243b4b0e0c5e993079b64c8e437279cbfd4539f0` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/check.svg` | 230 | `6c4336e88ba7176fd2a25119fc39ed008c5f635c0ebc2dd17c8939a135be8e68` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/cloud.svg` | 275 | `1a96fb68a00a0edf234f10da79dfb53777dbf84dee37226c9d2e1faad0a7ee45` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/eye.svg` | 288 | `e85085a5d403632d36356786ff6152322453ff7f952844d1f43b8c1dc716a1f0` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/file-text.svg` | 313 | `a1ea906426ca96665feeff92b2eeb27cd8bc5abf54e8eedd623b257f7ba3cb53` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/folder.svg` | 292 | `00324248fe8ca862e5cbc034983d9b4f8ad36c58b5fda7e3f7e7070a87176650` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/font-size-chevron-dark.svg` | 214 | `45792fae760d5a99a6265a71e75642bf8d431b638bd7ebe34b93b2cfa49af088` | S4 | 是；字号控件箭头 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/font-size-chevron-light.svg` | 214 | `f5a4e061a29ec63bc30f982d72a858a400f57d37ef44db3f25f42be0ae52ed49` | S4 | 是；字号控件箭头 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/hard-drive.svg` | 284 | `d216a3b229b5bc80096e2703ad67f5612adaf860abda44aaaaf34a4f44106bc1` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/headphones.svg` | 342 | `fb6e30a37f2344c616ee9d484b733fd783646b243ee8a4c1d657e0008c83bc43` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/home.svg` | 285 | `3da9d992d53a3499e0202469321f8667dde3d88636a9db2e520313e8deda8790` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/library.svg` | 280 | `2b445d13734132bac8cbbcb7054e878b3ec27e2956e2e9b4a15aa2fa0959fd04` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/list-check.svg` | 261 | `47c845baef554c8bc2960405b3575df7497ad66c26e16382c53f36d0cb37dd4b` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/lock.svg` | 287 | `44ad293dcbd620cf02ed05f9d50df2d0870abddfc51eb62cfbe8f50316adf192` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/moon.svg` | 253 | `554e413ea0938a2f1aac7b3a3761b7482cc3d8a824099dda381ecd1bd419c1d3` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/panel-left.svg` | 276 | `2ad7b83069ee3f280786ce0fdbbcef7b032e780d7e777c5ee4920e6a97204be7` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/pause.svg` | 247 | `a3bfdade26fdd0d3285f4b790fb23221a525c6fc7b6c3315c54c284090fe469a` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/play.svg` | 229 | `5b5f1c7de12719c8b740ea42b1aed2a599fc1ee3aeb7f34f04e0d33a7919462c` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/plus.svg` | 230 | `56c57dccc06c5ea877d052d39ec6f6b2826038ab32e9912313bbe4bb8a9ab8e1` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/refresh.svg` | 283 | `af448f4e87854b4d9b219da98701c839fe9874a7fbf0e3b74cc9eff99e847ac7` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/search.svg` | 257 | `83bb15b6df6885a4912e96bbe8320cd126612925a9bdb7f71e522524b47f6e47` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/settings.svg` | 362 | `40dc5dd10d1067a22ae7a2cc53670f0a54fcfa1367c0e84a8a223024328f32d9` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/skip-back.svg` | 243 | `9bcf6757c0bc81148983899521e053cb6d3dfd2174ac2455d56622fff112ff9b` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/skip-forward.svg` | 245 | `0a087fdfd65b24ad66ae50259d82a4389184f9d8a031497c51ddacf88a41663b` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/sun.svg` | 339 | `8e5e11d14a151fc02d84bc4c23347e4814c44df3b7a5d41ebbd58dc6329dde5c` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/upload.svg` | 278 | `afaba9942a1f2a83a02756dbb9904c72dac08735a372cea5473e9b9e82a96075` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |
| `src/lextide/desktop/assets/icons/volume.svg` | 275 | `7a55bc5a4fec34e3863d45a926e344332bc6c71a8656511afe9e3b7e99458f9c` | S2 | 未发现运行引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R2 |
| `src/lextide/desktop/assets/icons/x.svg` | 231 | `826c12cb19ac90b2853e4d668b89ccf6796085aeef7602cd9b4b4207a3c172d5` | S2 | 是；当前 GUI 引用 | **OPEN_FUNCTIONAL_ASSET** | P1 | R1 |

## 公开分类汇总（2026-10-24 授权后）

六类分类与本清单 36 个文件的对应关系：

| 分类 | 数量 | 文件 | 适用条款 |
|---|---:|---|---|
| `BRAND_RESERVED` | 2 | `assets/brand/lextide-app-icon-1024.png`、`assets/brand/lextide-symbol-editable.svg` | `TRADEMARKS.md`（品牌保留；个人/非商业教育可用；商业品牌使用需书面授权） |
| `OPEN_FUNCTIONAL_ASSET` | 32 | 30 个 `assets/icons/*.svg`、`assets/design-tokens.json`、`assets/README.md` | `ASSETS_LICENSE.md` §2（与 MIT 兼容，可随代码复用） |
| `SCREENSHOT_DOCUMENTATION` | 2 | `docs/images/g6-english.png`、`docs/images/g6-chinese.png` | `ASSETS_LICENSE.md` §3（README 展示、个人学习、非商业教育引用，需注明来源） |
| `THIRD_PARTY_LICENSE` | 0 | 本清单内无 | — |
| `PRIVATE_EXCLUDED` | 0 | 本清单内无需排除项 | — |
| `NEEDS_REVIEW` | 0 | 本清单内无 | — |
| **合计** | **36** | 34 图形 + `assets/README.md` + `design-tokens.json` | |

**保留来源不确定性说明（不阻塞发布，但如实记录）**：S2 的 28 个图标来自本项目自己的 UI 设计包，
该包声明这些 glyph 为专门创作且未分发 Lucide / SF Symbols 图像。这是来源记录，不是独立法律审计；
若日后确证某个文件存在第三方权利，该权利优先，相应文件将被更正或移除。
`assets/README.md` 为项目编写文档，作为文字随 MIT 提供。

**已知元数据事项（不阻塞）**：两个品牌 SVG 的 `title` / `desc` 与图层标签仍保留早期产品名
`TTS Studio` / `TTS Blue Symbol`。这是品牌元数据而非个人信息；本次按要求**未修改原始设计资产**，
未来如更新品牌资产应一并更正。

## 实际看图与历史待决定事项

两张 README 图均为 **2360×1600**，显示 Listen / 听读页面、四句自撰英文、18 px 字号与未加载音频状态。英文与中文 UI 可读，示例末句、底部状态和控制区未见裁切；画面不含桌面、其他应用、用户路径或私人正文。它们证明展示布局，不证明实际音频生成、播放同步或 0.6.1 Cleaning Mode。

品牌 PNG 为 **1024×1024** 的白色圆角底、蓝色文字转声波符号；品牌 SVG 渲染为两条蓝色符号路径。SVG 的 `title`、`desc` 与图层标签仍保留 **TTS Studio / TTS Blue Symbol** 旧产品名称。这不是个人信息，但属于公开前应知悉的品牌元数据；本轮未修改原件或候选。SVG/contact sheet 未见人物、个人文字或外部图片。

- **已解决（2026-10-24）：** 所有者已确认 Logo、28 个设计包图标、2 个项目 chevron、tokens 与 2 张截图的公开范围与条款，分别记录在 `TRADEMARKS.md` 与 `ASSETS_LICENSE.md`；本清单 36 个文件全部保留并分类。
- **范围决定：** 13 个无运行引用的图标与 1.0 tokens 已决定**随源码保留**（不删除），以免破坏界面完整性；它们由 `ASSETS_LICENSE.md` §2 覆盖。
- **ICNS 边界：** 干净源码候选未携带 ICNS；`scripts/build_macos.py:make_icon()` 会由上述品牌 PNG 生成 `.icns`。原始设计包另有历史 ICNS / ICO，均不在本清单发布范围。未来二进制图标仍继承品牌资产的待确认状态。
- **字体边界：** 候选无字体文件。tokens 中 SF Pro、Segoe UI、Inter、Arial 仅是字体名称/回退建议；当前 GUI 使用平台字体。此处不授予字体分发权，亦不把系统字体名称当作已随源码分发的文件。
- **发布复核：** 所有者已完成隐私/版权复核与发布批准；最终 payload 已重新计算哈希。本地候选、原始设计包与本机审计证据均不进入公开仓库。

本地证据保存在被 Git 忽略的 `.artifacts/github-first-release/audit/assets/`：`asset-inventory.json`、`audit_assets.py`、`visual-review.md`、`icons-review.png`、`brand-symbol-review.png` 与验证记录。原始素材的个人绝对路径只保留在本地证据，不写入此公开候选文档。该目录及本清单不是生效许可证，也不构成法律保证。
