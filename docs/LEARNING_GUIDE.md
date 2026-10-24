# 用 Lextide 学 Python：从一个按钮读懂完整程序

这份指南面向能运行 Python、但尚不熟悉桌面项目的学习者。先追踪一份文本怎样变成任务，再学习函数、数据结构、Qt 事件、异步进程、接口和文件完整性。每一节都指向仓库中的真实实现；练习使用自写短句，避免直接调用在线合成或修改已有用户任务。

本文的 Python 小例子已在 Mac 源码环境执行。它们证明本地函数的行为，不代表 Windows 已适配，也不代表真实语音生成或听音质量。Windows 的开发、构建和测试由另一台 Windows PC 独立进行；此源码候选没有 Windows 兼容性验收。

## 先准备阅读环境

在项目根目录运行命令，使用项目的基础/desktop Python 环境。Mac 源码环境的解释器为 `.venv/bin/python`；Windows 环境与解释器由 Windows 端另行建立，不复制 Mac `.venv`。本指南下面的 `python` 指你已经确认的项目解释器，不指任意系统 Python，也不指独立的 Kokoro Python 3.13 环境。

先执行 `python -c "import sys; print(sys.executable); print(sys.version)"`，确认实际解释器。运行测试时把 `--basetemp` 指向自己获准使用的学习输出目录，保持原始材料只读。测试目录可由 pytest 清理，因此只能使用专门的临时目录。本文不要求安装新依赖；缺少包时先核对 [pyproject.toml](../pyproject.toml) 和当前环境。

阅读代码时，优先找函数名，不依赖会随修改漂移的行号。可以用编辑器全文搜索；安装了 `rg` 时也可以运行：

```sh
rg -n 'def generate|async def start|def clean_english|def tr|font_combo' src/lextide
```

## 第一阶段：理解输入、返回值和数据结构

从 [cleaning.py](../src/lextide/cleaning.py) 的 `CleanResult` 和 `clean_english(text)` 开始。这里没有窗口、文件写入或网络调用，适合第一次读项目代码。

`CleanResult` 是一个 dataclass，把同一次处理得到的原文、清理稿、规则版本、移除行数、修改记录和警告放在一起。`changes` 与 `warnings` 的 `default_factory=list` 使每个结果拥有独立列表；否则不同调用可能意外共享可变数据。`frozen=True` 限制字段重新赋值，但不把列表内容自动变成不可变对象。

```python
from lextide.cleaning import clean_english

source = "# A quiet morning.\n这是中文译文。\nHello（你好）."
result = clean_english(source)
print(repr(result.cleaned_text))
print(result.original_text == source)
print(result.removed_chinese_lines)
print([change["reason"] for change in result.changes])
```

本次实测输出：

```text
'A quiet morning.\nHello.'
True
1
['markdown_prefix_removed', 'chinese_line_removed', 'chinese_annotation_removed']
```

这段输出分别证明：Markdown 标记已处理；原文仍相等；删除了一整行中文；行内括注删除另有记录，不混入“删除中文行数”。`changes` 保存原始行号和 reason code，不复制原文片段。`RULE_VERSION` 当前为 `english-rules-v1`，用于说明是哪一套规则产生了结果。

规则保守处理明确中文译文和格式，不是通用语言检测器、翻译器或无损英文提取保证。不确定混排保留并警告；没有英文时会返回 `no_english_text` 警告，调用方必须阻止空内容继续生成。输入上限按 UTF-8 字节核验，不能只数 Python 字符数。

小练习：把第二行改成 `这里保留 DNA 专有名词。`，先预测结果，再查看 `warnings` 中的 reason。然后读 [test_cleaning.py](../tests/test_cleaning.py) 的 `test_conservative_cases_preserve_text`、`test_input_bound_counts_utf8_bytes_and_accepts_exact_boundary`，解释为什么“含中文”不等于“整行可以删除”。

0.6.1 增加了一个显式关键字参数 `mode`。不传参数或传 `conservative` 时仍走旧规则；传 `remove_chinese_translations` 才启用 `english-translations-v1`。例如可以观察：

```python
source = "Maya Stone recorded 1,500 birds.玛雅记录了1,500只鸟。"
safe = clean_english(source)
enhanced = clean_english(source, mode="remove_chinese_translations")
print(safe.cleaned_text)
print(enhanced.cleaned_text)
print(enhanced.removed_chinese_segments)
```

本次实测依次输出原始混排全文、`Maya Stone recorded 1,500 birds.` 和 `1`。原文仍在 `source` 与结果的 `original_text` 中。

这里的学习重点是**先找到可审查的片段边界，再判断是否移除**，不能用整行中文占比直接删除混排行。数字和标点不计入辅助的字母占比；英文人名也不自动使整段变成英文。看 [test_g61_cleaning.py](../tests/test_g61_cleaning.py) 的 `test_explicit_translation_mode_preserves_complete_english_in_order` 和 `test_ambiguous_and_technical_content_is_not_deleted`，分别检查“中文移除”和“英文完整”两个要求。练习：增加一句含中文地名的英文正文，证明它会被保留并提示人工审核。

GUI 的 `cleaning_strength_combo.currentData()` 使用稳定的模式 ID；显示标签可以翻译，ID 不翻译。`service.dispatch()` 接收 `cleaning_mode`，`service._provenance()` 保存模式与版本快照；缺少这个可选字段的旧记录按保守模式解释。启动任务时只保存已经审阅的文本，不重新执行清理。对照集成测试 `test_enhanced_cleaning_is_explicit_and_reviewed`，理解为什么规则选择、清理稿和最终人工修改稿需要分别记录。

## 第二阶段：追踪一个按钮，不急着阅读所有文件

先读 [window.py](../src/lextide/desktop/window.py) 的 `MainWindow._studio_page()`，找到 `generate_button`。窗口创建控件，控制器接收事件，worker 执行耗时工作。

```text
Generate audio 按钮
  → controller.Controller.generate()
  → controller.Controller._start_synthesis(payload)
  → desktop.process.WorkerClient / QProcess
  → desktop.worker.main() / service.dispatch()
  → desktop.service.start(request, emit)
  → longform.tasks.create_task() / run_task()
```

对应源码：[controller.py](../src/lextide/desktop/controller.py)、[process.py](../src/lextide/desktop/process.py)、[worker.py](../src/lextide/desktop/worker.py)、[service.py](../src/lextide/desktop/service.py)、[tasks.py](../src/lextide/longform/tasks.py)。

`Controller.generate()` 先检查窗口是否正在关闭、是否已有任务、预览是否仍有效，以及审核勾选。它复制当前 preview，保存 signature，然后按引擎决定是否展示在线授权。对话框关闭后还会重新比较 signature：用户在对话框打开期间编辑了文本，也必须使旧授权失效。最后构造包含 `text`、`settings`、`approved_sha256`、`allow_online` 和本地输出目录的字典。

小练习：在纸上画出 `generate()` 的所有 `return` 分支。分别写出“没有预览”“取消在线授权”“授权期间修改文本”“输出目录不是绝对路径”会在哪一步结束。对照 [test_desktop_gui.py](../tests/test_desktop_gui.py) 中 `test_online_dialog_nested_edit_rejects_stale_generation`；测试使用 monkeypatch 模拟授权期间编辑，并断言不会启动任务。

## 第三阶段：理解预览、快照与异步任务

`service.preview(request)` 在本地清理基础文本并计算分句/分块，返回字典。下面的代码不会启动合成：

```python
from lextide.desktop.service import preview

snapshot = preview({"text": "A quiet morning. Hello."})
print({name: snapshot[name] for name in (
    "character_count", "sentence_count", "chunk_count"
)})
```

本次实测为 `{'character_count': 23, 'sentence_count': 2, 'chunk_count': 1}`。查看返回的 `text` 与 `sha256`，理解“授权当前文本”为什么要绑定文本内容，而不能只存一个永久 `approved=True`。

`service.start(request, emit)` 是 `async def`。它重新校验设置、实际朗读文本和批准哈希，建立任务，保存 `source-extracted.txt` 与 `document-review.json`，再等待 `_run()` 完成。不要把 `async` 理解成自动创建线程；这里 GUI 使用独立 QProcess，worker 内部再运行 asyncio。`emit` 是传入的函数，用于上报任务创建、进度和结果。

`service._authorize()` 对 Edge 要求本次明确的布尔 `allow_online=True`，同时检查文本哈希；Kokoro 不会因为这个字段而自动获得在线行为。G6 的文本处理记录保留清理快照，启动阶段不会在用户批准后悄悄重新执行另一遍英文清理。用户最终审阅后的文本才是合成输入。

小练习：阅读 [test_desktop_service.py](../tests/test_desktop_service.py) 的 `test_missing_or_stale_confirmation_never_creates_task`，找出它怎样证明“失败发生在创建任务之前”。再读 [test_g6_integration.py](../tests/test_g6_integration.py) 的 `test_clean_edit_preview_approved_snapshot`，区分原文、清理稿、用户编辑稿和最终预览稿。

## 第四阶段：界面语言与业务数据为什么要分开

[i18n.py](../src/lextide/desktop/i18n.py) 的 `configure_language()` 加载本地 JSON；`tr(source, **values)` 只处理界面文字。英语源字符串是回退值，中文资源在 [translations](../src/lextide/desktop/translations)。译文的占位符必须与源字符串一致，否则退回英语。

```python
from lextide.desktop.i18n import configure_language, tr

for language in ("en", "zh_CN"):
    configure_language(language)
    print(tr("{count:,} characters · preview to calculate chunks", count=1234))
```

本次实测输出分别为 `1,234 characters · preview to calculate chunks` 和 `1,234 个字符 · 预览后计算分块`。`count` 仍是数字；语言更改只改变外层说明。

在 `MainWindow` 中找 `PAGES`、`engine_combo`、`language_combo` 和 `_fill_table()`。`PAGES` 仍为 `Overview/Studio/Library/Listen/Tasks/Settings`；`engine_combo.currentData()` 仍返回 `edge-tts/kokoro`。中文显示使用 `tr()`，业务判断使用稳定值。用户标题、字幕原文、job ID、路径、manifest key 都不送入翻译函数。

Settings 的语言选择保存到偏好，并提示重启。不要为实现即时翻译，顺带重建任务、改写字幕或更新历史 manifest。

小练习：新建一条标题恰好为 `Settings` 的模拟历史记录。中文界面中，导航应显示“设置”，用户标题仍应为 `Settings`。现有 [test_g6_window.py](../tests/test_g6_window.py) 的 `test_translated_display_preserves_internal_keys_and_user_content` 就验证这个边界。

## 第五阶段：Qt signal、slot 与真实像素字号

在 `MainWindow._listen_page()` 查找 `font_combo`。这是不可编辑的 QComboBox，选项为 12、14、16、18、20、22、24、28、32、36、40 px，默认 18。每个选项显示字符串，同时通过 item data 保存整数。

`currentIndexChanged` 触发后，代码从 `currentData()` 取整数，交给 `_set_reader_font(pixels)`。它调用 `QFont.setPixelSize()`，并给字幕列表设置 `font-size: …px`；18 point 与 18 pixel 不是同一个单位。[preferences.py](../src/lextide/desktop/preferences.py) 的 `FONT_SIZES` 约束可持久化的合法字号，控制器负责保存偏好。

`TranscriptDelegate.sizeHint()` 用实际 viewport 宽度和字体布局计算每行高度，保留已有主题 padding。只改变字体、不重算行高，会让大字号末行被截断。本次在 980×700 的窗口、40 px、三行中英字幕上，原 Qt delegate 行高为 140 px，低于所需的 177 px；修复后为 200 px，并通过实际滚动到底的 PNG 核验末行完整。

小练习：在本地 UI 切到 12、18、40 px，缩窄窗口后滚动到长句末尾。查看 [test_g6_window.py](../tests/test_g6_window.py) 的 `test_large_mixed_transcript_reserves_space_for_last_wrapped_line`。能够选择 40 只是控件行为；文本完整显示还需要布局证据。UI 布局的调整不应改写 `player.cue_at()`、SRT 时基或音频速度。

## 第六阶段：用 Protocol 读懂可替换的语音引擎

[engines/base.py](../src/lextide/engines/base.py) 声明 `TTSEngine(Protocol)`，包括 `capabilities()` 和异步 `synthesize()`。Protocol 表达调用方需要的形状：有哪些属性、接受哪些参数、返回什么结果。它不是把所有实现塞进同一个父类的要求，也不会自动验证网络许可和音频完整性。

`EngineCapabilities` 说明引擎是否在线、可用以及支持的声音；`EngineResult` 记录实际音频字节、哈希、边界、模型和有效参数；`SentenceBoundary` 的时间单位为 100 ns ticks。业务层依赖这个共同契约，而 Edge 与 Kokoro 可以有不同实现。

小练习：并排读 [edge.py](../src/lextide/engines/edge.py) 和 [kokoro.py](../src/lextide/engines/kokoro.py) 的 `synthesize()` 签名。列出共同字段，再解释为什么相同请求的 cache key 不等于实际音频文件的 SHA-256。只做阅读和测试替身，不在练习中发送真实文本或安装模型。

## 第七阶段：异常、原子写入、缓存和 manifest

| 概念 | 在哪里看 | 要解决的问题 |
|---|---|---|
| 明确异常 | [errors.py](../src/lextide/errors.py) 的 `StudioError/InputError/ValidationError` | 调用方依据 code 和退出码识别失败，而不是把所有错误当成功结果 |
| 安全错误输出 | [worker.py](../src/lextide/desktop/worker.py) 的 `main()` | 已知错误保留语义；未知第三方异常不直接输出可能含正文、URL 的 traceback |
| 原子 JSON | [storage.py](../src/lextide/longform/storage.py) 的 `atomic_json()` | 临时文件写完、flush/fsync 后在同目录 replace，避免读到半个 JSON |
| 缓存身份 | 同文件的 `canonical_key()` | 对规范化请求计算 SHA-256，字典键顺序不改变身份 |
| 完整性复查 | 同文件的 `Cache.load()` | 同时检查状态、请求、普通文件、字节数、实际 SHA 与 PCM 元数据 |
| 任务记录 | [tasks.py](../src/lextide/longform/tasks.py) 的 `_load()`、`_transition()` | 恢复前验证状态、输入与分块，合法转换后再保存 |

`atomic_json()` 不是万能事务，也不能替代任务锁、缓存键锁和 Windows 平台的独立文件语义验证。`manifest.json` 是成功产物的记录；它的 schema、状态、哈希和元数据必须与文件一致。文件“存在”或“能打开”都不足以证明任务完成。

```python
from lextide.longform.storage import canonical_key

left = {"text": "Hello.", "voice": "demo"}
right = {"voice": "demo", "text": "Hello."}
print(canonical_key(left) == canonical_key(right))  # 本次实测 True
```

这个教学字典只演示规范化排序，不是完整真实合成请求。实际 cache key 还包含引擎、模型和规则等身份信息；`artifact_sha256` 则来自真实文件字节。破坏文件后必须重新验证，不能只重写哈希来“修好”记录。

小练习：阅读 [test_g3_storage.py](../tests/test_g3_storage.py) 的 `test_atomic_json_failed_replace_preserves_previous_file` 和 `test_cache_revalidates_actual_wav_even_if_byte_hash_is_updated`。前者用失败注入检查旧文件保留，后者检查“更新了字节哈希”仍不能绕过音频验证。用 `tmp_path` 做练习，禁止在真实任务上制造损坏。

## 第八阶段：用 pytest 和 Git 完成一个小改动

pytest 的 `assert` 写可观察的要求；fixture 准备依赖；`tmp_path` 提供隔离文件；`monkeypatch` 临时替换外部行为；参数化使同一要求覆盖多个输入。测试替身通过，只证明被测试边界，不等于真实引擎、网络、声卡和打包都已验证。

按学习范围选择测试，不用每次都跑整套真实音频流程：

```sh
python -m pytest tests/test_cleaning.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-cleaning"
python -m pytest tests/test_g61_cleaning.py tests/test_g61_quotes.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-g61"
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_g6_window.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-window"
```

运行前先把 `LEXTIDE_LEARNING_DIR` 设为自己专用的绝对学习输出目录。以上为 Mac/POSIX shell 写法；Windows 的环境变量语法与 Qt 测试需由 Windows 端确认。offscreen 测试验证控件和布局，不能代替真实桌面播放、键盘交互或用户听音。

一个完整的小练习可以是“给已有设置增加一句更清楚的说明”：先找到源字符串与目录项；写下预期英文/中文显示；只改对应文案；运行相关测试；渲染并查看两种语言；最后检查差异。修改 clean 规则则应先加一个能失败的边界用例，再修改规则、跑清理和集成测试，并审视规则版本是否需要变化。

Git 分阶段记录你的思路：

1. `git status --short` 查看原有修改；`git diff -- <文件>` 确认这次究竟改了什么。
2. 用 `git diff --check` 查空白问题，运行与改动相关的 pytest；记录实际输出及未测试边界。
3. `git add -- <确认过的文件>` 只暂存本次内容，随后用 `git diff --cached` 阅读将提交的差异。
4. 在获准的练习分支中，确认测试和审阅后再提交。不要用 `git add .` 把私有数据、临时文件或他人未完成修改一起带入。

小练习的验收应能回答：改动触发条件是什么；前后行为如何；哪项测试能在改动前失败、改动后通过；没有做哪些平台或真实服务验证。本次 G6 UI 的独立 `test_g6_window.py` 已实测 14 项通过，ruff 对 window/player/该测试通过；这些数字只对应本次 UI 验证，不替代项目整体发布验收。


## 私有开发历史怎样对应阶段

首次公开源码将从已核验的 0.6.1 快照建立干净 Git 历史，首版只有一个 initial commit。以下短 commit 是所有者保留的 G1–G6 内部开发记录，方便在私有仓库用 `git show` 学习；它们不是公开仓库的提交，不能用来检出公开版本。公开读者应按本指南的真实文件、函数和测试名称阅读当前实现。

| 阶段 | 本机开发记录 | 阅读重点 |
|---|---|---|
| G1 | `1e1eb6a` | 在线 CLI、字幕、音频验证与原子保存 |
| G2 | `45017d1`、`bf7e47a` | 品牌迁移、离线接口、按需模型安装 |
| G2–G3 修复 | `7f7ab1f`、`4cbe02e`、`90d699f` | PCM 滤波、样本容差、profile 版本兼容 |
| G3 | `3d72604` | 分块、缓存键、任务恢复与全局时间轴 |
| G4 | `9408b68` | 原生 GUI、导入、预览、独立 worker |
| G5 | `ab0f961`、`251f06f`、`26cb2e3` | macOS 打包和 Edge 句首标记的精准修复 |
| G6 | `34e11eb`、`cdb722f`、`1f4d1e7`；当前源码版本 0.6.1 | 界面翻译、字号偏好、本地清理；0.6.1 的引号边界修复与显式增强清理已通过工程复测和用户验收 |

练习：在保留的私有仓库用 `git show --stat 251f06f` 看清一次小修复涉及哪些文件；在公开快照用测试名称追踪同一行为。不要为了复制学习历史而直接发布含私人路径的旧仓库。
