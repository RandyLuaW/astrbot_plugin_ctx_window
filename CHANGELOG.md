# 更新日志

所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-10-20

### 新增

- 首个公开版本。
- `compact_context` — 模型自己写滚动梗概，覆盖更早的对话
- `read_context_log` — 主动翻查落盘的原文
- `context_status` — 看当前条数、上次压缩位置、压力等级
- 三级压力提示：攒到 `soft_after` 轻提示，`hard_after` 硬提示，占满 `hard_ratio` 直接算硬压力
- 重启后首次请求注入最近 `restore_n` 条原文，接得上话

