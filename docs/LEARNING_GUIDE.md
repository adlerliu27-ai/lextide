# 用 Lextide 学 Python：从一个按钮读懂完整程序

这份指南面向能运行 Python、但尚不熟悉桌面项目的学习者。先追踪一份文本怎样变成任务，再学习函数、数据结构、Qt 事件、异步进程、接口和文件完整性。每一节都指向仓库中的真实实现；练习使用自写短句，避免直接调用在线合成或修改已有用户任务。

本文的 Python 小例子已在 Mac 源码环境执行。0.6.1 是已公开且已验收的源码版本；当前 0.9.0 是 G9 稳定版候选，本文不声明它已完成独立验收或发布。这些例子证明本地函数的行为，不代表 Windows 已适配，也不代表真实语音生成或听音质量。Windows 的开发、构建和测试由另一台 Windows PC 独立进行；此源码候选没有 Windows 兼容性验收。

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

G9 把普通用户的选择缩为 `Keep Original / English Only`；English Only 使用上述增强规则，立即产生可编辑预览。底层算法仍接收稳定的模式 ID，显示标签可以翻译，ID 不翻译。`service.dispatch()` 接收 `cleaning_mode`，`service._provenance()` 保存模式与版本快照；缺少这个可选字段的旧记录按保守模式解释。启动任务时只保存已经审阅的文本，不重新执行清理。阅读 [test_g6_integration.py](../tests/test_g6_integration.py) 的文本来源与审核测试，理解为什么原文、清理稿和最终人工修改稿需要分别记录。

再读 [reading.py](../src/lextide/reading.py) 的 `validate_reading_text()`：清理与校验是两个不同职责。前者生成候选稿，保留不能可靠判断的内容；后者在 English Only 或英文声线工作流中阻止仍含中文的稿件进入合成。它不改写文本、不猜中文名字的英文译法，也不发送网络请求。小练习：构造纯英文、英中对照、英文句中中文地名和非英文声线四种请求，观察哪些被拒绝；同时断言输入字符串和 SHA-256 没有变化。

## 第二阶段：追踪一个按钮，不急着阅读所有文件

先读 [window.py](../src/lextide/desktop/window.py) 的 `MainWindow._studio_page()`，找到 `generate_button`。窗口创建控件，控制器接收事件，worker 执行耗时工作。

```text
Send to Edge & Generate / Generate Audio 按钮
  → controller.Controller.generate()
  → 本地 preview worker，返回到 Controller._submit_prepared(data, snapshot)
  → controller.Controller._start_synthesis(payload)
  → desktop.process.WorkerClient / QProcess
  → desktop.worker.main() / service.dispatch()
  → desktop.service.start(request, emit)
  → longform.tasks.create_task() / run_task()
```

对应源码：[controller.py](../src/lextide/desktop/controller.py)、[process.py](../src/lextide/desktop/process.py)、[worker.py](../src/lextide/desktop/worker.py)、[service.py](../src/lextide/desktop/service.py)、[tasks.py](../src/lextide/longform/tasks.py)。

G8 的 `Controller.generate()` 检查关闭状态、已有任务与引擎条件，然后直接捕获当前可见编辑器的文本、设置、signature、revision 和 SHA-256。这次明确点击只授权这个快照。本地 preview 返回后，`_submit_prepared()` 再核对快照身份、设置与文本；取消、编辑或重复回调不能沿用旧点击。基础规范化若改变文本，先把结果展示出来，等待用户再次明确点击。验证通过后才构造包含 `text`、`settings`、`approved_sha256`、`allow_online` 和本地输出目录的字典。

小练习：一起画出 `generate()` 和 `_submit_prepared()` 的 `return` 分支，对照 [test_g8_generation.py](../tests/test_g8_generation.py) 的 `test_late_preview_cannot_reuse_old_click`、`test_duplicate_click_and_callback_are_consumed_once` 与 `test_local_normalization_requires_new_explicit_click`。再读 `_resume_ready()`：恢复在线任务仍通过 `confirm_transmission()` 展示任务自身的正文，并从未勾选开始；这里不能拿编辑器的另一份文本代替保存任务的审核对象。

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

`service.start(request, emit)` 是 `async def`。它重新校验设置、实际朗读文本与当前英文工作流约束、批准哈希，建立任务，保存 `source-extracted.txt` 与 `document-review.json`，再等待 `_run()` 完成。GUI 禁用按钮只是第一道提醒；提交层必须独立拒绝不合适的正文，不能相信旧回调或绕过 GUI 的请求。不要把 `async` 理解成自动创建线程；这里 GUI 使用独立 QProcess，worker 内部再运行 asyncio。`emit` 是传入的函数，用于上报任务创建、进度和结果。

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

在 `MainWindow` 中找 `PAGES`、`engine_combo`、`language_combo` 和 `_fill_table()`。`PAGES` 仍为 `Overview/Studio/Library/Listen/Tasks/Settings`；`engine_combo.currentData()` 返回稳定的 `edge-tts/kokoro/auto`。中文显示使用 `tr()`，业务判断使用稳定值。用户标题、字幕原文、job ID、路径、manifest key 都不送入翻译函数。

G8 的 `tr()` 返回 `TranslatedText(str)`，保留界面文案的 source 和格式化值。[localized.py](../src/lextide/desktop/localized.py) 的控件只登记明确传入的翻译对象，普通字符串会清除旧绑定。`Controller.save_settings()` 保存成功后调用 `refresh_localized_text()`：加载 catalog、替换 Qt 内置 translator、用 `retranslate_ui()` 刷新现有控件。组合提示使用 `join_translated()` 保留各片段身份；正文和路径只作为普通值。语言单独保存不重建播放器、重新加载音频或重新发现引擎。对照 [test_g8_i18n.py](../tests/test_g8_i18n.py)，检查来回切换后 combo data、光标、选区、cue 与控件实例仍相同。

小练习：新建一条标题恰好为 `Settings` 的模拟历史记录。中文界面中，导航应显示“设置”，用户标题仍应为 `Settings`。现有 [test_g6_window.py](../tests/test_g6_window.py) 的 `test_translated_display_preserves_internal_keys_and_user_content` 就验证这个边界。

## 第五阶段：Qt signal、slot 与真实像素字号

在 `MainWindow._listen_page()` 查找 `font_combo`。这是不可编辑的 QComboBox，选项为 12、14、16、18、20、22、24、28、32、36、40 px，默认 18。每个选项显示字符串，同时通过 item data 保存整数。

`currentIndexChanged` 触发后，代码从 `currentData()` 取整数，交给 `_set_reader_font(pixels)`。它调用 `QFont.setPixelSize()`，并给字幕列表设置 `font-size: …px`；18 point 与 18 pixel 不是同一个单位。[preferences.py](../src/lextide/desktop/preferences.py) 的 `FONT_SIZES` 约束可持久化的合法字号，控制器负责保存偏好。

`TranscriptDelegate.sizeHint()` 用实际 viewport 宽度和字体布局计算每行高度。当前类在 [bilingual_ui.py](../src/lextide/desktop/bilingual_ui.py)：英文与可选中文分开布局，再加上间距与上下留白；`window.py` 导入同一个类。只改变字体、不重算行高，会让大字号末行被截断。G6 的历史修复记录来自 980×700 窗口、40 px、三行中英文本：原 Qt delegate 行高 140 px，低于所需 177 px，修复后为 200 px；这些旧数值不能当成 G7 新双语布局的测量结果。

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


## 第九阶段：G7 怎样增加中文而不改英文时间轴

先读 [中文字幕操作说明](BILINGUAL_SUBTITLES.md)，再把功能拆成三个边界：已验证英文、独立中文附加文件、界面显示。界面语言 `language` 与字幕模式 `subtitle_mode` 是不同偏好；`english` 是后者默认值，`bilingual` 只决定是否显示已有中文。

| 真实入口 | 读取时要回答的问题 |
|---|---|
| [bilingual.py](../src/lextide/desktop/bilingual.py) 的 `SubtitleSource.from_verified()` | 为什么要重新读 SRT，并把其解析结果与已经验证的 cue 列表比较？ |
| `SubtitleSource.assert_current()` | 为什么仅改 SRT 换行、字节或空格，也要阻止沿用旧附件身份？ |
| `import_translation(path, source)` | SRT 如何精确比对编号、数量与时间？TXT 为什么只能证明物理行对应，不能证明语义正确？ |
| `validate_translation_text(value)` | 字符串类型、空白、控制字符与 UTF-8 字节上限分别保护什么边界？ |
| `TranslationStore.load()` / `save()` / `current_revision()` | 原英文 SHA、cue 身份和独立 JSON 当前版本如何共同防止串错附件或覆盖别人刚保存的修改？ |
| [bilingual_ui.py](../src/lextide/desktop/bilingual_ui.py) 的 `TranslationPreviewDialog` | 为什么要列出全部英文与中文，并让确认框每次从未勾选开始？ |
| `TranslationEditDialog` / `BilingualController.edit_selected()` | 为什么英文只读、中文可编辑，而未填写的其他 cue 继续用 `None` 表示？ |
| `TranscriptDelegate` / `CHINESE_ROLE` | 为什么中文放进单独 item role，而不把它拼回英文 cue 文本？ |

`SubtitleSource` 是冻结 dataclass，cue 又被复制为只读映射；UI 后续改动传入列表不会改变已捕获的来源。`TranslationDocument` 保存与每条英文对齐的 `texts` 和当前附加文件 `revision`。`save()` 需要显式传入 `expected_revision` 与 `origin`：首次写入用 `None` 表示“我看到没有旧文件”，后续编辑使用实际读到的指纹。不能为绕过冲突直接读取新指纹、再把旧内容写下去；应重新打开并让人检查最新内容。

实际导入调用链为：

```text
Listen → Import Chinese
  → BilingualController.import_file()
  → TranslationStore.current_revision() + import_translation()
  → TranslationPreviewDialog：完整对应预览、默认未勾选确认框
  → 用户确认并保存
  → TranslationStore.save(expected_revision=..., origin=...)
  → BilingualController._display() / MainWindow.set_chinese_translations()
```

这条链不调用语音合成。`Controller._save_subtitle_mode()` 只保存显示偏好；原播放器 `cue_at()` 和 `LocalPlayer.seek_cue()` 继续使用英文时间轴。`TranscriptDelegate.chinese_font()` 将中文设为英文字号约 80%，`sizeHint()` 与 `paint()` 使用相同文字布局；长句和 40 px 需要同时检查测量与实际绘图。

先运行一个无文件、无网络的小例子：

```python
from lextide.desktop.bilingual import validate_translation_text
from lextide.errors import ValidationError

print(validate_translation_text("安静的清晨。"))
print(validate_translation_text("Maya counted 1,500 birds."))
try:
    validate_translation_text("   ")
except ValidationError as error:
    print(type(error).__name__)
```

本次在项目 `.venv/bin/python` 的实际输出为：

```text
安静的清晨。
Maya counted 1,500 birds.
ValidationError
```

第二行也被接受，因为该函数只验证合法文字，不声称检测了中文或翻译正确性。对照 [test_g7_bilingual.py](../tests/test_g7_bilingual.py) 的 `test_txt_semantic_order_is_not_claimed_to_be_verified`：把两行语义交换但保持行数，结构检查无法辨认，必须由完整预览后的人工确认补上。不要在教材里把“文件通过格式校验”写成“翻译准确”。

再阅读同文件的 `test_save_load_records_hashes_timing_and_partial_manual_edit`、`test_stale_revision_cannot_overwrite_current_translation`、`test_source_byte_change_rejects_load_import_and_save`、`test_failed_atomic_replace_preserves_previous_file`，分别理解部分译文、乐观版本检查、来源变化和原子替换。使用 `tmp_path` 或专用学习目录练习；原 MP3、SRT 与任务 manifest 始终只读。可以运行以下离线存储测试，结果以你当次输出为准：

```sh
python -m pytest tests/test_g7_bilingual.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-g7-bilingual"
```

GUI 契约见 [test_g7_gui.py](../tests/test_g7_gui.py)：它使用 Qt 和本地测试替身，检查确认与取消、逐句编辑、显示偏好、旧文件保护和双语行高；不包含真实音频播放或用户听音验收。

附加文件存在不代表可加载。JSON 损坏或 cue 身份不符时，GUI 显示警告并保留英文播放，禁止在未知旧内容上逐句编辑；完整重新导入仍须人工确认，保存还要检查从预览起是否有人改动旧文件。这是可恢复失败，不是把损坏记录默默当成新文件。

## 第十阶段：G8 怎样把引擎选择与实际生成分开

先读 [G8 交互说明](G8_UX.md)，再读 [engine_policy.py](../src/lextide/desktop/engine_policy.py) 的 `resolve_engine()`。它只接收引擎选项、已有发现结果和可选探测结果，返回包含 `engine`、`ready`、`reason_code`、`probe_required` 的字典，不在函数内部执行 I/O，也不修改输入。`ready` 只说明运行前提，不代替声线、语速、正文审核与在线授权检查。

`probe_edge_reachability()` 才执行异步 I/O：调用 `edge_tts.list_voices()`，不接收文档正文。查看其整体超时、TLS connector 与异常分支，区分“能取得声线列表”“本地环境准备完毕”和“这次合成成功”。三者不是同一个布尔值。`service.auto_probe()` 在 worker 边界拒绝无关请求字段，然后把发现和探测证据交给纯策略函数；`Controller._probe_auto()` 用 revision 丢弃过期回调。

小练习：读 [test_g8_engine_policy.py](../tests/test_g8_engine_policy.py) 的 `discovery()`、`test_auto_prefers_reachable_edge`、`test_auto_selects_kokoro_only_after_failed_probe` 与 `test_policy_never_mutates_discovery_or_invents_voice_rate`，画出选择表。再对照 [test_g8_generation.py](../tests/test_g8_generation.py) 的 `test_auto_requires_explicit_compatible_voice_selection`：策略选中了 Kokoro，为什么 UI 仍可能禁止生成？当前声线属于 Edge 时，程序保留原值，必须由用户主动选择兼容声线。

运行时引擎失败不进入 Auto 重新选择。复用 G5/G6 的身份与完整性检查，不等于复用上一次点击的授权。可以只运行不含 GUI 的策略测试，再单独运行有 Qt 的交互测试：

```sh
python -m pytest tests/test_g8_engine_policy.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-g8-policy"
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_g8_generation.py tests/test_g8_i18n.py tests/test_g8_layout.py --basetemp "$LEXTIDE_LEARNING_DIR/pytest-g8-gui"
```

`window.disclosure()` 用 checkable `QToolButton` 控制次要面板的可见性；它不销毁 panel。与 `retranslate_ui()` 放在一起阅读，理解“只更新展示”如何保留同一对象的正文、选择和播放器状态。`test_language_save_preserves_pending_consent_and_editor` 检查语言单独保存不会把尚未返回的点击快照作废；若正文或有关设置发生变化，前述快照检查仍然生效。

本地运行时还要分清应用代码与数据目录。`preferences.default_data_dir()` 给出默认目录，`load_preferences(root)` 从指定 root 读取 Library 根目录和其他偏好。使用另一个 `--data-dir` 打开空 Library，不代表先前导出被删除；这正是 `OBS-G7-4` 的目录边界。不要为了让两个测试窗口看起来相同而复制或覆盖真实用户数据。

## 私有开发历史怎样对应阶段

首次公开源码 0.6.1 已从核验快照建立干净 Git 历史，首版为独立的 initial commit。以下短 commit 是所有者保留的 G1–G6 内部开发记录，方便在私有仓库用 `git show` 学习；它们不是公开仓库的提交，不能用来检出公开版本。公开读者应按本指南的真实文件、函数和测试名称阅读当前实现。

| 阶段 | 本机开发记录 | 阅读重点 |
|---|---|---|
| G1 | `1e1eb6a` | 在线 CLI、字幕、音频验证与原子保存 |
| G2 | `45017d1`、`bf7e47a` | 品牌迁移、离线接口、按需模型安装 |
| G2–G3 修复 | `7f7ab1f`、`4cbe02e`、`90d699f` | PCM 滤波、样本容差、profile 版本兼容 |
| G3 | `3d72604` | 分块、缓存键、任务恢复与全局时间轴 |
| G4 | `9408b68` | 原生 GUI、导入、预览、独立 worker |
| G5 | `ab0f961`、`251f06f`、`26cb2e3` | macOS 打包和 Edge 句首标记的精准修复 |
| G6 | `34e11eb`、`cdb722f`、`1f4d1e7`；已验收版本 0.6.1 | 界面翻译、字号偏好、本地清理；0.6.1 的引号边界修复与显式增强清理已通过工程复测和用户验收 |
| G7 | 0.7.0 本地开发，未在此声明提交或发布 commit | 独立中文字幕附件、精确来源绑定、人工映射审核、逐句编辑；独立验证与用户验收待完成 |
| G8 | 0.8.0 本地开发，未在此声明提交或发布 commit | 一次点击的文本快照、显式 Auto 策略、原地语言刷新与次要信息折叠；不表示已完成用户验收或公开发布 |

练习：在保留的私有仓库用 `git show --stat 251f06f` 看清一次小修复涉及哪些文件；在公开快照用测试名称追踪同一行为。不要为了复制学习历史而直接发布含私人路径的旧仓库。
