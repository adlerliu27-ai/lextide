# G8：生成、引擎选择与即时语言切换

本文描述 0.8.0 本地开发代码的实际交互，不是公开发布或用户验收声明。源码、测试和本地运行证据需要分别判断；真实 Edge 服务可靠性、听音质量及 Windows 平台不由离线测试证明。

## 从当前朗读文本生成

Studio 的 **Reading text / 朗读文本** 是本次生成的内容。先核对可见文本，再按引擎使用按钮：

| 当前实际引擎 | 明确操作 | 行为 |
|---|---|---|
| Edge | **Send to Edge & Generate / 发送至 Edge 并生成**，旁边显示发送至 Microsoft Edge TTS 的说明 | 只授权本次点击捕获的文本和设置快照 |
| 已就绪的 Kokoro | **Generate Audio / 生成音频** | 用本地引擎生成，不上传正文 |
| 引擎未就绪或声线/语速不兼容 | 先修正设置 | 生成按钮保持不可用，不自动换声线或引擎 |

点击后先在本地检查文本、分句和分块。控制器保存文本 SHA-256、设置 signature、编辑 revision 和本次快照对象；回调只消费一次。准备期间编辑文本、更改有关设置或点击 **Cancel Preparation / 取消准备** 会使旧点击失效。基础规范化若改变了文本，界面展示新文本并要求再次点击，不会按旧授权发送不同字节。

**恢复保存任务是另一条流程。** 在线任务恢复仍弹出原有审核窗口，显示任务保存的完整正文和 SHA-256，确认框每次从未勾选开始。恢复对象可能与当前编辑器内容不同，不能用编辑器的一次生成点击代替任务授权。CLI 的授权约定不变。

## Auto 的准确含义

Auto 是用户明确选择的第三个引擎选项。它先检查现有本地依赖，再使用上游 `edge_tts.list_voices()` 请求声线列表，整个检查不带文档正文，不调用语音合成，也不下载模型。

- Edge 连通性检查与本地条件都通过时，Auto 选择 Edge。
- 无法确认 Edge 连接或本地条件时，Auto 可以选择已经就绪、模型验证通过的 Kokoro。
- 两者都不满足条件时，界面说明原因，等待用户修正或重试。

声线列表可访问只证明连通性，**不保证随后能合成音频**。检查使用经验证的 TLS 连接和整体超时；上游库负责其代理及重定向行为。用户明确选择 Edge 或 Kokoro 时，Auto 的检查结果不会改写该选择。

Auto 保留当前声线和语速；若所选引擎不支持它们，生成保持阻止状态，需用户主动调整。合成开始后的错误不会触发另一个引擎自动重试。该策略没有改变 G5/G6 的文本身份、音频完整性或本地模型验证约束。

## 文本处理、设置与折叠信息

**Text Processing / 文本处理** 按“文本模式 → 清理强度 → Apply Cleaning / 应用清理”排列。Keep Original、English Only、Conservative 与 Remove Chinese Translations 使用既有算法；这次只调整控件排列和说明。选项变化本身不改正文，替换已编辑的稿件仍需确认，并保留撤销。

**Review changes / 查看更改**、Listen 的 **Session details / 项目详情** 和 **Advanced diagnostics / 高级诊断** 收起次要信息，正文、进度与主要动作保持可见。Listen 的时间和正文分列，英文与中文使用同一正文起点；它们仍沿用原英文 cue 时间轴。

Settings 底部固定显示 **Save Settings / 保存设置**，并区分未保存、已保存与保存失败。保存语言后立即更新现有控件及 Qt 内置文字；编辑器内容、选择、任务、引擎与播放器实例不因语言刷新重建。只改语言不会重查引擎或重载媒体。实际修改引擎运行路径后保存，仍会重新检查相关条件。已打开的模态窗口阻止通过 Settings 切换语言，其文字按打开时的语言生成。

## Library 与 data directory：OBS-G7-4

默认数据目录是 `~/Library/Application Support/Lextide/desktop`。启动时的 `--data-dir` 选择另一套本地偏好、Library 根目录、缓存及独立中文字幕附件；新的测试目录可能显示空 Library。这不表示原音频被删除。

回到原数据目录启动，或在 Library 选择已有导出目录，即可查看相应导出。选择旧导出目录不会自动把另一数据目录中的中文字幕附件迁移过来。测试与日常使用应明确区分数据目录，不在 `.app/Contents` 内存放用户数据。

## 代码与验证入口

| 边界 | 真实入口 | 对应测试 |
|---|---|---|
| 本次点击与快照 | `Controller.generate()`、`_submit_prepared()`、`cancel_preparation()` | `tests/test_g8_generation.py` |
| Auto 选择和无正文探测 | `engine_policy.resolve_engine()`、`probe_edge_reachability()`、`service.auto_probe()` | `tests/test_g8_engine_policy.py` |
| 原地翻译 | `i18n.TranslatedText`、`join_translated()`、`localized.retranslate_ui()` | `tests/test_g8_i18n.py` |
| 布局与双语对齐 | `window.disclosure()`、`TranscriptDelegate` | `tests/test_g8_layout.py` |

GitHub Actions 仅在原有离线 core 列表中增加 `test_g8_engine_policy.py`；它用替身覆盖网络探测，没有加入 GUI、真实网络合成或模型推理。其余 G8 GUI 测试需要本地 desktop 依赖与 offscreen Qt 环境，不能替代实际渲染检查和用户听音。
