# AstrBot 缓存规范器

[![版本](https://img.shields.io/github/v/release/Boston-Lobster/astrbot_plugin_cache_governor)](https://github.com/Boston-Lobster/astrbot_plugin_cache_governor/releases)
[![许可](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

通过调整请求中背景信息和工具定义的顺序，尽量保留更长的稳定前缀，让支持前缀缓存的模型服务有机会复用输入。同时提供不含对话正文的数值统计，帮助判断哪些变化打断了前缀。

**当前版本为 v0.1.0，已在 AstrBot 4.29.1 验证。它是部分通用的布局规范插件，背景处理主要适配天使之心、天使之魂与 EmotionAI 的既有格式，不能保证对所有插件或模型都提高命中率。**

## 能做什么

- 对连续且可识别的临时背景排序：回复长度规则 → 记忆 → 情绪 → 工作状态。
- 将天使之魂背景中的画像、记忆、笔记放在易变的灵魂状态之前，每个子段落内容保持原样。
- 按名称稳定当前请求的工具列表，保留工具对象、参数、权限与执行入口。
- 检测 system、工具和历史的前缀变化，仅在内存保存有数量和有效期限制的哈希及长度。
- 从服务商返回的 usage 中读取缓存用量，记录输入、缓存命中、未命中和输出 token。

本插件没有模型请求、主动发消息或答案复用功能。未知消息结构原样保留；历史、工具链和多模态消息作为排序边界，不跨越边界移动内容。

## 适用范围

| 项目 | v0.1.0 的范围 |
| --- | --- |
| AstrBot | 声明支持 `>=4.29.1,<4.30.0`，实际验证版本 4.29.1 |
| 通用能力 | 工具列表排序、前缀变化审计、主 agent 最终响应用量统计 |
| 已知背景格式 | 天使之心的临时回复长度/工作账本，天使之魂的 `<system_context>`，EmotionAI 的 `<emotion_context>` |
| 后台直接调用 | 其他插件直接调用 `provider.text_chat()` 时通常不经过本插件钩子 |
| 模型服务 | 应由服务商本身支持输入前缀缓存；插件不会替服务商开启缓存 |

已识别背景必须是带 `_no_save=true` 的临时 user 消息。天使之魂分段只有完整识别、无未知或重复嵌套时才会重排。格式不同的上游版本可能被原样保留。

**v0.1.0 尚未提供供其他插件调用的背景注册接口。** 当前使用格式适配；之后如果引入注册接口，会单独说明兼容边界。

## 安装

1. 从 [Releases](https://github.com/Boston-Lobster/astrbot_plugin_cache_governor/releases) 下载 `astrbot_plugin_cache_governor-v0.1.0.zip`。
2. 在 AstrBot 插件管理中上传 ZIP，并确认插件加载成功。
3. 在“缓存规范器”设置中选择需要启用的规范规则。

也可在支持仓库安装的 AstrBot 插件管理中使用仓库地址。仓库 `main` 会继续开发；需要固定版本时，应使用 Release 安装包或 `v0.1.0` 标签。升级前请核对 AstrBot 版本和变更记录。

没有第三方运行依赖，不需要填写模型 API Key。详细设置见 [配置说明](docs/CONFIGURATION.md)。

## 配置

所有规范开关默认开启，可单独关闭。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 规范与统计总开关 |
| `normalize_background_order` | `true` | 对连续的已识别临时背景排序 |
| `normalize_memory_sections` | `true` | 重排已识别记忆背景的内部子段 |
| `stable_tool_order` | `true` | 复制当前工具列表并按名称排序 |
| `metrics_enabled` | `true` | 数值统计开关 |
| `max_tracked_sessions` | `128` | 最多保留的会话指纹数量，内部限制 1–512 |
| `fingerprint_ttl_seconds` | `1800` | 指纹有效期，内部限制 30–86400 秒 |

## 统计与隐私

登录 AstrBot 后可访问受其鉴权保护的只读接口：

```text
GET /api/v1/plugins/extensions/astrbot_plugin_cache_governor/status
```

插件数据存放在 AstrBot 的 `data/plugin_data/astrbot_plugin_cache_governor/`：

- `metrics.jsonl`：数值记录，达到 4 MiB 后轮转，保留一份旧文件。
- `summary.json`：累计统计，重载后继续使用。

不记录提示词、聊天正文、模型回复、API Key、账号或用户标识。POSIX 数据目录权限限制为 0700，新建数值文件为 0600。内存中的会话状态只保留作用域哈希、内容哈希、长度和时间，不保留正文。

命中率计算为 **缓存输入 token / 已记录的总输入 token**。没有有效用量时显示 `null`。统计覆盖主 agent 最终响应钩子，不等于每一次工具 API 调用、全部后台调用或整个 API 账户账单；与其他插件的用量统计可能重叠，不能直接相加。

`shared_context_messages` 和 `shared_serialized_bytes` 是结构比较值，不是 tokenizer 结果或服务端命中数。更多说明见 [验证与限制](docs/VALIDATION.md)。

## 效果与限制

在一次六请求合成对照中，两种布局每次输入均为 1297 token；预热后，原顺序两次各命中 128 token，规范顺序两次各命中 1024 token，约为 **9.9% → 79.0%**。

该结果来自特定背景结构、模型和短时缓存状态，不代表全日实际收益，也不是对任何用户的效果承诺。完整数值、实验边界和测试方法见 [验证记录](docs/VALIDATION.md)。

插件不补回上游已删除、截断或摘要的历史，不扩大天使之心的决策窗口，也不冻结记忆、情绪或画像。这里的“锚点”用于观察稳定前缀，没有照搬 QQ-agent 的跨窗口历史补回机制。

首次改变布局可能需要重新预热；历史起点、system、工具集合或模型路由变化仍会影响缓存。总成本应结合输入量、未命中量和请求数一起评估。

## 开发与版本隔离

```bash
python tests/test_governor.py
python scripts/check_release.py
python scripts/build_release.py
```

纯逻辑测试无需安装 AstrBot；`tests/integration_astrbot.py` 需在已安装匹配版本 AstrBot 的隔离环境中运行，使用合成事件与临时数据，不发真实消息。

- `v0.1.0`：当前固定版本标签，对应独立 Release 和校验文件。
- `codex/v0.1-maintenance`：0.1 系列维护分支。
- `main`：后续开发入口，不作为固定安装版本。

已发布标签不改写；补丁版本递增。涉及新的注册协议或兼容边界变化时单独升版。详见 [版本策略](docs/VERSIONING.md) 和 [变更记录](CHANGELOG.md)。

## 回退

在 AstrBot 插件管理中禁用本插件即可停止其作用。它不修改其他插件源码或聊天数据库，不需要通过覆盖数据库回退。切换版本时建议先备份插件独立配置和统计数据，再安装指定 Release。

## 参考与许可

设计参考 [K0nd1us/QQ-agent](https://github.com/K0nd1us/QQ-agent) 的稳定前缀和提示词锚点思想；本项目独立实现，没有复制其源码。

使用 AstrBot 的官方 LLM 请求与响应钩子：[开发文档](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)。

以 [MIT License](LICENSE) 发布。报告问题前请阅读 [安全与隐私说明](SECURITY.md)，不要上传完整请求、配置、日志或聊天记录。

<!-- version-isolation:v1 -->
## 版本隔离与固定安装

默认分支是持续开发入口。需要固定版本时，请使用对应 Release、版本标签或下表的固定源码分支，不把默认分支当作某个已发布版本。

| 版本 | 固定源码 | 校验与附件 |
| --- | --- | --- |
| `v0.1.0` | [`7303d5afc95e`](https://github.com/Boston-Lobster/astrbot_plugin_cache_governor/tree/codex/releases/v0.1.0) | [Release](https://github.com/Boston-Lobster/astrbot_plugin_cache_governor/releases/tag/v0.1.0) |

版本标签与固定源码分支禁止更新和删除；后续修复使用新版本号，不覆盖旧版本。

本次为 1 个已有版本标签建立了独立的 `codex/releases/<标签>` 源码分支；完整标签、提交及分支映射见 [source-versions-20261025.json](https://github.com/Boston-Lobster/astrbot_plugin_cache_governor/releases/download/v0.1.0/source-versions-20261025.json)，配套提供独立 SHA-256 文件。这个目录快照也包含没有单独创建 Release 的版本标签。

每个历史 Release 增补 `version-isolation-v1.json` 和单独的 SHA-256 文件，记录原有源码提交及附件哈希。原有安装包、依赖分卷、原校验文件和下载地址保持不变。
仓库开启不可变发布后，今后应先创建草稿，上传并核验全部安装包与校验文件，最后发布；发布后需要修复就递增版本号。GitHub 不会自动追溯锁定历史 Release 的附件。
下载时使用版本专属地址并校验哈希。源码、运行依赖与安装包应分别标明版本；需要调整依赖包时发布新版本，不复用已发布文件覆盖旧内容。
<!-- /version-isolation:v1 -->
