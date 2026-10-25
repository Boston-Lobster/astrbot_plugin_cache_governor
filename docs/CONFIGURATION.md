# 配置与调用顺序

插件设置由根目录 `_conf_schema.json` 声明。通过 AstrBot 插件设置修改即可，不需要向本插件提供模型凭据。

## 默认行为

`enabled=false` 会停止所有请求改写与记录。其他开关可分别控制背景排序、记忆子段排序、工具排序和数值统计。若只希望停止记录，关闭 `metrics_enabled`；规范规则仍可继续生效。

## 背景识别

只识别带 `_no_save=true` 的 user 消息，且不得附带工具调用字段。

| 来源格式 | 条件 |
| --- | --- |
| 回复长度 | `sender_id=angelheart-reply-length`，正文以 `<system_reminder>` 包裹 |
| 工作账本 | `sender_id=angelheart-work-ledger`，正文以 `<system_reminder>` 包裹 |
| 记忆背景 | 完整 `<system_context>...</system_context>` |
| 情绪背景 | 完整 `<emotion_context>...</emotion_context>` |

排序只在连续、全部可识别的消息段中发生。未知消息、持久历史、system、assistant、tool 和多模态消息都是边界。记忆内部子段顺序为 `user_profiles → memories → notes → soul_state`，无法完整识别时原样保留。

背景排列会改变它在模型输入中的位置，可能影响模型对信息的关注。请在自己的插件组合中验证回复行为；发现不合适时可单独关闭相应规则。

## 钩子顺序

AstrBot 4.29.1 按优先级从高到低执行请求钩子。

| 钩子 | 优先级 | 行为 |
| --- | ---: | --- |
| `normalize_request` | -500 | 规范已注入的背景与当前工具列表 |
| `audit_request` | -1000000 | 只读观察最终请求布局 |
| `record_response` | -1000000 | 读取最终响应的实际 usage |

已验证组合中的背景注入在 -500 之前执行，防注入最终检查在 -1000 执行。如果其他插件采用不同优先级、较晚修改请求或提前终止事件，应重新核对顺序。此配置不是适用于所有插件的全局执行保证。

## 指纹与容量

指纹按会话来源、conversation ID、人设、provider 和请求 model 区分，保存于有容量与有效期限制的内存容器。进程或插件重载会清空指纹，但不会清除模型服务商的缓存。

前缀状态包括：`cold`、`system_changed`、`tools_changed`、`history_append`、`history_stable`、`history_changed`。这些状态用于诊断，不替代服务商返回的命中 token。
