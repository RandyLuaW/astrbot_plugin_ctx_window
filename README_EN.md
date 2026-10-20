# Context Window: persist raw chat logs, let the model write its own rolling summary

> AstrBot plugin · Raw messages on disk + a rolling summary the model writes itself

## The problem

Short-term context usually lives in memory only — restart the process and the last few dozen messages are gone.

Background summarization is done by another model in third person, turning experience into an archive.

This plugin adds two things: **raw logs on disk** (restorable, and the model can read them on demand) and **compression handed back to the model itself**.

## Features

- `compact_context` — the model writes its own rolling summary
- `read_context_log` — read persisted raw messages
- `context_status` — counts, last compaction point, pressure level
- Three pressure levels: soft at `soft_after`, hard at `hard_after`, and hard once `hard_ratio` of `max_lines` is reached
- Restores the last `restore_n` messages on the first request after a restart

## Install

### Option 1: Plugin marketplace (recommended)

Search "上下文窗口" in the AstrBot WebUI plugin marketplace.

### Option 2: Download from Releases

Grab the zip from [Releases](../../releases), then WebUI → Plugins → Upload.

### Option 3: Clone manually

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_ctx_window.git
```

No extra Python dependencies.

## Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | See Chinese README |
| `restore_n` | int | `30` | See Chinese README |
| `rolling_max_chars` | int | `400` | See Chinese README |
| `max_lines` | int | `400` | See Chinese README |
| `inject_rolling` | bool | `true` | See Chinese README |
| `max_read` | int | `60` | See Chinese README |
| `soft_after` | int | `30` | See Chinese README |
| `hard_after` | int | `60` | See Chinese README |
| `hard_ratio` | float | `0.8` | See Chinese README |

## Notes

- Images deduplicated by fingerprint, not URL
- Summary length capped by `rolling_max_chars`
- Division of labour with iris_memory: facts vs. experience

## License

MIT
