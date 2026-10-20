# 上下文窗口：原始聊天记录落盘 + 由模型自己写的滚动梗概

> AstrBot 插件 · 原文落盘、可主动翻查、压缩权在模型手上

## 它解决什么问题

大多数框架的短期上下文只存在内存里，进程一重启，最近几十条对话就没了。

后台自动做的摘要又是另一个模型写的、强制第三人称——经历被翻译成档案。

这个插件补两件事：**原文落盘**（重启可恢复、模型能主动翻查）和**压缩权交回模型自己**（梗概由它在对话里写，写进来什么样，注入出去就是什么样）。

## 功能

- `compact_context` — 模型自己写滚动梗概，覆盖更早的对话
- `read_context_log` — 主动翻查落盘的原文
- `context_status` — 看当前条数、上次压缩位置、压力等级
- 三级压力提示：攒到 `soft_after` 轻提示，`hard_after` 硬提示，占满 `hard_ratio` 直接算硬压力
- 重启后首次请求注入最近 `restore_n` 条原文，接得上话

## 工作机制

```
每条消息 → append 到 sessions/*.jsonl（带图片指纹，去重）
                ↓
        每轮注入：滚动梗概（模型自己写的） + 重启时的原文恢复
                ↓
        compact_context → 更新梗概，原文继续落盘不动
```

不检索、不向量化、不调用任何模型。压缩必须由模型在对话里写。

## 安装

### 方式一：插件市场（推荐）

WebUI 插件市场搜索「上下文窗口」。

### 方式二：Releases 下载

从 [Releases](../../releases) 下载 zip，WebUI → 插件 → 上传安装。

### 方式三：手动克隆

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_ctx_window.git
```

无额外 Python 依赖。

## 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 启用上下文窗口。关闭后不落盘、不注入、工具也不可用。 |
| `restore_n` | int | `30` | 重启后恢复多少条原文（仅在进程启动后的第一次请求注入一次）。 |
| `rolling_max_chars` | int | `400` | 滚动梗概的字数上限。她压缩时超过会被拒绝。 |
| `max_lines` | int | `400` | 每个会话最多保留多少条原文，超出自动丢弃最旧的。 |
| `inject_rolling` | bool | `true` | 每轮是否注入滚动梗概。关掉后她仍能压缩，但看不到。 |
| `max_read` | int | `60` | read_context_log 单次最多返回多少条。 |
| `soft_after` | int | `30` | 上次压缩后又攒了多少条开始轻提示。她看到提示会自己决定要不要收拾。 |
| `hard_after` | int | `60` | 攒到多少条升级为硬提示（语气更重，说明再不压就要丢最早的）。 |
| `hard_ratio` | float | `0.8` | 原文总量占到 max_lines 的这个比例时，直接算硬压力。 |

## 注意事项

- 图片按指纹（而非 URL）去重，同一张图换链接不会重复记录
- 梗概有字数上限（`rolling_max_chars`），超了写入会被拒绝
- 与 iris_memory 分工：那边管事实与检索，这边管经历与滚动

## License

MIT
