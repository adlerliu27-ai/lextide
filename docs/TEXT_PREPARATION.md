# G6.1 本地英文清理规则

`lextide.cleaning.clean_english(text, *, mode="conservative")` 是无网络、无模型依赖的确定性过滤器。默认 `conservative` 保留 0.6.0 的 `english-rules-v1` 行为；显式 `remove_chinese_translations` 使用独立的 `english-translations-v1` 规则。输入上限为 **1 MiB UTF-8 字节**；非字符串、无效 Unicode、超限输入以及未知 mode 通过现有 `InputError` 拒绝。不会静默截断。

Studio 的 `Original / English Only` 决定是否清理；选择 English Only 后，`Cleaning Mode / 清理模式` 可选 `Conservative / 保守` 或 `Remove Chinese Translations / 移除中文译文`。改变选项本身不改写稿件，点击应用才产生新候选。新稿覆盖人工编辑前须确认；最终须按 `Clean → Preview/Edit → Approve → Synthesize` 审阅，批准后不再次清理。

返回 `CleanResult` dataclass，可通过 `dataclasses.asdict()` 序列化：

| 字段 | 含义 |
| --- | --- |
| `original_text` | 输入原文，逐字符保留，包括 BOM 和原换行 |
| `cleaned_text` | 清理后的可编辑候选文本 |
| `version` | `english-rules-v1` 或 `english-translations-v1` |
| `mode` | `conservative` 或 `remove_chinese_translations`；省略参数等于保守 |
| `removed_chinese_lines` | 完全删除的中文行数；同行译文、括注不计入 |
| `removed_chinese_segments` | 增强规则移除的译文片段数，与整行数分开记录 |
| `changes` | 原始行号、操作类型、reason code；不保存正文摘录 |
| `warnings` | 原始行号、reason code；不保存正文摘录 |

行号从 1 开始，按输入的 CRLF、CR、LF 分行。删除前面的行不会改变后续审计行号；OCR 合并另记录 `previous_line`。结构化审计不是可逆字符级 diff，恢复原文使用 `original_text`。本函数不写文件、不打印日志、不修改导入源文件；宿主程序负责保存 `CleanResult` 和提供预览确认。

## 保守模式规则与边界

| 类型 | 行为与限制 |
| --- | --- |
| 英文正文 | 保留内容、顺序、引号、撇号、中间标点、数字、小数、缩写、C++、C#、标识符下划线。只执行下面列出的显式规则。 |
| Markdown | 删除正文前明确的 ATX 标题、引用、项目符号、1–3 位有序列表前缀；前缀后须有空格且正文以字母、汉字、引号或部分 Markdown 标记开始。成对粗体、斜体、三重强调、行内反引号移除，保留其中的文字。反引号内内容不再套用强调规则。 |
| Markdown 限制 | 不实现完整 CommonMark 解析。嵌套或不配对强调、链接、表格、HTML 等可能保留；不配对反引号、围栏标记保留并提示。2026 等四位数字行首不当作有序列表；`+ 2`、`- 3`、`> 5` 保留。 |
| 整行中文 | 至少含一个 Han 字符，且所有字母均为 Han 时删除；可含数字与标点。含 `Python` 等 Latin 词、kana 或其他字母的中文行保留，提示检查。此规则不识别中文语义，也不区分简繁。 |
| 同行译文 | 英文完整句后有空白，后段从汉字开始且以 `。！？` 结束时，删除后段；英文前段至少两个 Latin 词，结尾不得是已知缩写或 initialism。后段最多允许一个已在前段出现、大小写相同的 Latin 词，例如 `Learning Python is useful. 学习 Python 很有用。`。其余混排保留。 |
| 中文括注 | 非嵌套圆括号/全角圆括号，前面已有英文时，删除纯 Han 括注；也删除只含一个 Latin 词、以该词开头且重复括号前词语的中文解释。`Python（Python 编程语言）is widely used.` → `Python is widely used.`。英文括注、其他 Latin 词、嵌套括号保留。 |
| OCR 断行 | 仅对原文相邻、无空行/列表边界的 ASCII 英文行操作：前段至少三个词且末词是限定的冠词、介词、连词等，后行以小写字母开始时，以一个空格连接。续接使用片段列表，最后统一拼接。 |
| OCR 限制 | 不猜拼写、词典、跨行连字符，也不连接已删除中文行/页码两侧的文本。不做图片识别，不处理 PDF 阅读顺序、多栏重排或语义断句。 |
| 页码 | 仅删除整行 `Page n of m`，其中 `0 < n <= m`。不把独立数字当页码；保留并提示。 |
| 空白与控制符 | 移除输入开头 BOM；CRLF/CR 统一 LF；NBSP、Tab、其他空白转空格并压缩；去行首行尾空白；最多保留一个空白行；未知 Cc 控制符转空格并提示，避免拼接单词；其余隐藏字符保留并提示。 |
| 空结果 | 全中文、空输入可能得到空字符串，并返回 `no_english_text`。不会补造英文。只含数字或符号时保留这些内容并提示缺少英文。 |

这些规则无法从语义上证明中文确实是译文，或括号内容只是解释。含关键术语、密码、界面文字或中英混排正文时，用户应在候选文本中逐项核对删除记录。G6 不承诺所有输出都只含英文；保留不确定内容有助于避免误删。

## 显式移除中文译文模式

增强模式先沿顶层句末 `. ! ? 。 ！ ？` 或明确 `|` / `：` 分隔分析候选片段，再判断其中是否存在可移除的中文译文。引号和括号内部不分片；已知缩写、小数和域名中的点不当作普通句末。英文与译文可以黏连，译文内可以包含年份、带千位分隔的数字，以及受规则约束的人名和技术词；不再把“存在 Latin 字母”作为整段保留的唯一理由。**不是按整行中文占比删除。**

| 自撰输入 | 目标候选 |
|---|---|
| `In 1921, Lewis Terman conducted a study.1921年，Lewis Terman开展了一项研究。` | `In 1921, Lewis Terman conducted a study.` |
| `Maya Stone recorded 1,500 birds.玛雅记录了1,500只鸟。` | `Maya Stone recorded 1,500 birds.` |
| `Practice every day. \| 每天练习。` | `Practice every day.` |
| `Practice every day：每天练习。` | `Practice every day` |
| `I visited 北京 last year.` | 保留并提示人工审核 |
| `A title中文翻译` | 缺少可靠边界，保留并提示 |

纯 Han 字母片段可以移除。含 Latin 的候选须同时满足：至少 4 个 Han 字符；**Han 字符数 / 所有字母数 ≥ 0.35**；每一组 Latin 词都属于代码中明确列举的技术词，或是 2–3 个首字母大写、其余小写且不含英语功能词的人名式词组。数字、标点与空白不计入比例分母。具有独立英文终止标点的 Latin 词组不能凭专名规则删除；包含其他文字系统的片段也保留。

这些条件共同判断已经分出的候选片段，不能证明语义，也不能仅凭比例删掉一整行。普通英文句式、中文专名承担英文句意、未知文字或不清楚边界的内容需要保留并审核。对被移除且含 Latin 名称/术语的译文额外提示 `translation_with_latin_removed`。规则有意不覆盖所有人名写法；保留而未清掉的译文可以由用户在预览中编辑。

`changes` 的增强移除记录包含原始行号与 `removed_characters`，不会复制私人全文到诊断记录。Original Text 和候选文本可直接对照；计数不是逐字符可逆 diff。英文主体是否完整、中文是否属于译文，仍须用户在批准前确认。

保存时 `document-review.json` 的 `text_preparation` 增加可选 `cleaning_mode`；旧记录缺少该字段时按保守模式解释。原文、候选稿和最终审核稿保留各自 SHA，历史 manifest schema 1/2/3、音频、SRT 和缓存身份不因清理规则升级而重写。

## Reason codes

`changes` 的操作类型为 `normalize`、`replace_span`、`remove_span`、`remove_line`、`join_lines`。每次变更带原始 `line`，同一行可以有多条变更。

| Changes reason | 说明 |
| --- | --- |
| `bom_removed` | 移除输入起始 BOM |
| `line_ending_normalized` | CRLF/CR 统一换行 |
| `whitespace_normalized` | 空白归一、压缩或去两端空白 |
| `control_character_replaced` | 未知控制字符替换为空格 |
| `markdown_prefix_removed` | 删除明确的 Markdown 前缀 |
| `markdown_emphasis_removed` | 删除成对强调标记 |
| `markdown_code_delimiters_removed` | 删除成对行内反引号 |
| `chinese_line_removed` | 删除整行中文 |
| `chinese_annotation_removed` | 删除匹配规则的中文括注 |
| `chinese_translation_suffix_removed` | 删除匹配规则的同行译文尾段 |
| `chinese_translation_segment_removed` | 增强模式删除有边界的译文片段，另记 `removed_characters` |
| `page_marker_removed` | 删除明确的独立页码行 |
| `blank_line_removed` | 删除首尾或重复空白行 |
| `ocr_line_joined` | 与前行保守连接，另带 `previous_line` |

| Warnings reason | UI 可用中文说明 |
| --- | --- |
| `control_character_replaced` | 已将异常控制字符替换为空格，请核对原文。 |
| `hidden_character_retained` | 保留了无法确定用途的隐藏字符，请检查。 |
| `replacement_character_retained` | 文本含替换字符，可能存在解码或 OCR 问题。 |
| `mixed_chinese_retained` | 保留了不确定的中英混排内容，请手动检查。 |
| `translation_with_latin_removed` | 已移除含人名或技术术语的中文译文，请核对结果。 |
| `ambiguous_page_marker_retained` | 页码范围不合理，已保留原文。 |
| `numeric_line_retained` | 独立数字可能是正文，已保留。 |
| `unresolved_markdown_retained` | 保留了未能确定处理方式的 Markdown 标记。 |
| `unrecognized_noise_retained` | 保留了无法确认用途的符号行。 |
| `hyphenated_line_break_retained` | 连字符断行未自动拼接，请核对词语。 |
| `no_english_text` | 清理结果未检测到 ASCII 英文字母，请检查后再生成语音。 |

## 候选库简评（2026-10-23 官方资料核对）

本次只阅读维护方仓库、配置及模型官方说明，没有安装以下库或下载模型。下表的 Python 要求是项目声明；**四个候选均未在本项目 Python 3.14、macOS arm64 或 PyInstaller 打包环境中执行验证**，版本声明不能代替本机兼容性测试。

| 候选 | 用途与许可证 | 模型、下载及依赖行为 | Python 声明与当前取舍 |
| --- | --- | --- | --- |
| [Lingua](https://github.com/pemistahl/lingua-py) | 短文本及混合文本语言识别；Apache-2.0。 | 官方说明安装后可完全离线使用；2.x 为 Rust 原生实现的 Python bindings，使用统计语言模型。新增原生扩展及模型装包验证工作。 | 当前[构建配置](https://github.com/pemistahl/lingua-rs/blob/main/pyproject.toml)要求 Python ≥3.12。可作为后续歧义语言标注候选；语言分类不能证明某段是可删译文。 |
| [split-lang](https://github.com/DoodleBears/split-lang) | 按语言划分并合并文本片段；自身代码 MIT。 | [依赖](https://github.com/DoodleBears/split-lang/blob/main/setup.py)含 fast-langdetect、BudouX、Pydantic；[检测代码](https://github.com/DoodleBears/split-lang/blob/main/split_lang/detect_lang/detector.py)明确选择 `model="full"`，因此缺少缓存时涉及下述完整模型下载；模型许可需独立核对。 | `setup.py` 声明 Python ≥3.9。后续确需语言片段边界时再评测；当前默认完整模型和跨标点/数字/换行合并行为需要适配。 |
| [ftfy](https://github.com/rspeer/python-ftfy) | 修复 mojibake 和 Unicode 异常；Apache-2.0。 | 检查到的[修复路径](https://github.com/rspeer/python-ftfy/blob/main/ftfy/__init__.py)采用本地规则，不依赖语言模型或模型下载；[配置文档](https://ftfy.readthedocs.io/en/latest/config.html)说明默认可改变弯引号、字符宽度等。 | [项目配置](https://github.com/rspeer/python-ftfy/blob/main/pyproject.toml)声明 Python ≥3.9，依赖 wcwidth。出现已确认的乱码修复需求时，再作为独立可预览步骤评测，并关闭不需要的改写。 |
| [fast-langdetect](https://github.com/LlmKira/fast-langdetect) | fastText 语言识别封装；代码 MIT，`lid.176` 模型 CC BY-SA 3.0。 | lite 的 `lid.176.ftz` 随包提供；full 的 `lid.176.bin` 按需下载，默认模型缓存位于系统临时目录，可配置路径；`auto` 先尝试 full，仅内存错误触发 lite 回退。模型官方体积为约 917 kB / 126 MB，见 [fastText 模型说明](https://fasttext.cc/docs/en/language-identification.html)。 | [项目配置](https://github.com/LlmKira/fast-langdetect/blob/main/pyproject.toml)声明 Python ≥3.9，3.14 使用 `fasttext-predict-v2` 原生依赖。后续识别需求可先评测显式 lite 模式；不得把 auto 当作无网络模式。 |

G6 的需求可用受限、确定性规则覆盖：明确格式删除、英文保留、原文审计，以及歧义内容保留并提示。因此当前没有引入这些依赖。这个选择是本项目的范围判断，不是对候选库准确率或质量的实测结论。

后续引入条件：先用经授权且人工标注的真实混排/乱码样本证明收益，并重点测量英文误删；锁定库与模型版本、核对代码和模型的分发许可；明确离线模式、下载与缓存位置；再验证 Python 3.14/macOS arm64 的安装和推理、PyInstaller 原生扩展/数据收集、冷启动断网行为及资源占用。检测结果先用于提示与预览，不直接决定不可撤销的删除。

## 范围与验证

自撰示例为 `examples/mixed_english.txt`。`tests/test_cleaning.py` 包括 T01–T10 参数化基本案例、保守保留案例、审计行号、原文保留、无网络调用、UTF-8 上限、重复调用确定性、长 OCR 续行与超长页码边界。

`tests/test_g61_cleaning.py` 覆盖增强模式的黏连/分隔译文、人名数字、技术字符、连续英文顺序、歧义保留及不泄露正文的审计记录。`tests/test_g6_integration.py` 检查增强模式控件、旧批准失效、人工编辑保护和最终发送文本 SHA；这些使用本地规则或 mock，不代表真实 TTS 已通过。

本模块没有新增 Lingua、split-lang、ftfy 或 fast-langdetect 依赖，候选取舍与未验证事项见上表。

截图 OCR 尚未实现。这里验证的是文本规则与程序行为，未进行真实 TTS 合成、音质或发音质量验证。
