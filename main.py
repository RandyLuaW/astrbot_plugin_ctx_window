# -*- coding: utf-8 -*-
"""上下文窗口：把原始聊天记录落盘，并让 Agent 自己压缩自己。

三层分工
--------
    滚动摘要（她自己写的，每轮注入，覆盖"更早"）
        ↑ compact_context 更新
    原始日志（落盘，重启后可恢复，她也能主动翻）
        ↑ 每条消息 append
    iris L1（最近对话，插件自己管，本插件不重复注入）

为什么需要
----------
    iris 的 L1 是纯内存（`self._queues = {}`），进程一重启最近几十条就没了。
    iris 的总结又是后台另一个模型做的、且强制第三人称——她的经历被翻译成档案。
    所以这里补两件事：原文落盘 + 压缩权交回给她自己。

不做什么
--------
    不做检索、不做向量化、不调用任何模型。压缩摘要必须由她本人在对话里写，
    通过 compact_context 传进来。写进来什么样，注入出去就是什么样。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_ctx_window"

DEFAULT_CONFIG = {
    "enabled": True,
    "restore_n": 30,
    "rolling_max_chars": 400,
    "max_lines": 400,
    "inject_rolling": True,
    "max_read": 60,
    # 压力信号：上次压缩后又攒了多少条，越过这两条线就提醒她自己收拾
    "soft_after": 30,
    "hard_after": 60,
    # 原文总量占到 max_lines 的这个比例时也算硬压力
    "hard_ratio": 0.8,
}

_SAFE = re.compile(r"[^0-9A-Za-z_.-]+")


def _data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        base = Path(get_astrbot_data_path())
    except Exception:
        base = Path(__file__).resolve().parents[2]
    d = base / "plugin_data" / PLUGIN_NAME
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(umo: str) -> str:
    return _SAFE.sub("_", str(umo or "unknown"))[:120] or "unknown"


def _session_file(umo: str) -> Path:
    return _data_dir() / "sessions" / f"{_safe_name(umo)}.jsonl"


def _state_path() -> Path:
    return _data_dir() / "state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _read_lines(umo: str) -> list[dict]:
    path = _session_file(umo)
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _write_lines(umo: str, items: list[dict]) -> None:
    path = _session_file(umo)
    tmp = path.with_suffix(".tmp")
    body = "\n".join(json.dumps(x, ensure_ascii=False) for x in items)
    tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
    tmp.replace(path)


def _fmt(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def _fingerprint(raw: str) -> str:
    """给一张图算个短指纹。

    QQ 的 Image.file 本身就是文件名（同一张图重发还是那个名），所以优先用它；
    base64 / 本地路径这种一长串的，退回 md5。指纹只取 8 位，够分辨就行。
    """
    raw = (raw or "").strip()
    if not raw:
        return "00000000"
    base = raw.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    if re.fullmatch(r"[0-9A-Fa-f]{8,64}", stem):
        return stem[:8].lower()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


class CtxWindowPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = dict(DEFAULT_CONFIG)
        if isinstance(config, dict):
            self.config.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
        self._restored: set[str] = set()   # 本次进程已恢复过的会话
        self._lock_map: dict[str, Any] = {}

    async def initialize(self) -> None:
        logger.info(f"[ctx-window] 就绪，日志目录 {_data_dir() / 'sessions'}")

    async def terminate(self) -> None:
        pass

    def _cfg(self, key: str) -> Any:
        return self.config.get(key, DEFAULT_CONFIG.get(key))

    # ------------------------------------------------------------ 落盘

    def _append(self, umo: str, role: str, name: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if len(text) > 1200:
            text = text[:1200] + "…"
        items = _read_lines(umo)
        items.append(
            {"ts": round(time.time(), 1), "role": role, "name": name[:20], "text": text}
        )
        cap = int(self._cfg("max_lines"))
        if len(items) > cap:
            items = items[-cap:]
        _write_lines(umo, items)

    def _media_tag(self, event: AstrMessageEvent) -> str:
        """给消息里的图片打指纹，写成 `[图 8c30f1e5]` 这种前缀。

        为什么需要：她只看文字日志，两张不同的图在日志里长得一模一样。
        她真拿这个当过证据——判过"你一句话发了两遍"，其实是两张不同的图。
        指纹放在最前面，日志一眼能扫，截断也丢不了。
        """
        try:
            segs = list(event.get_messages())
        except Exception:
            return ""
        tags: list[str] = []
        for seg in segs:
            if "image" not in type(seg).__name__.lower():
                continue
            raw = (
                getattr(seg, "file", None)
                or getattr(seg, "url", None)
                or getattr(seg, "path", None)
                or ""
            )
            tags.append(_fingerprint(str(raw)))
        if not tags:
            return ""
        uniq: list[str] = []
        for t in tags:
            if t not in uniq:
                uniq.append(t)
        head = f"[图×{len(tags)} " if len(tags) > 1 else "[图 "
        return head + " ".join(uniq) + "]"

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def record_user(self, event: AstrMessageEvent) -> None:
        """记下用户消息。"""
        if not self._cfg("enabled"):
            return
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
            text = getattr(event, "message_str", "") or ""
            tag = self._media_tag(event)
            if tag:
                text = f"{tag} {text.strip()}".strip()
            if not text.strip():
                return
            name = ""
            try:
                name = event.get_sender_name() or ""
            except Exception:
                pass
            self._append(
                str(event.unified_msg_origin), "user", name or "对方", text
            )
        except Exception as exc:
            logger.warning(f"[ctx-window] 记录用户消息失败：{exc}")

    @filter.on_llm_response()
    async def record_assistant(self, event: AstrMessageEvent, resp: Any) -> None:
        """记下她自己的回复。"""
        if not self._cfg("enabled"):
            return
        try:
            text = getattr(resp, "completion_text", "") or ""
            if text.strip():
                self._append(str(event.unified_msg_origin), "assistant", "我", text)
        except Exception as exc:
            logger.warning(f"[ctx-window] 记录回复失败：{exc}")

    # ------------------------------------------------------------ 注入

    def _render_log(self, items: list[dict], verbatim: int) -> str:
        """最近几条原文，更早的压成一行。"""
        if not items:
            return ""
        head = items[:-verbatim] if verbatim > 0 else items
        tail = items[-verbatim:] if verbatim > 0 else []
        lines = []
        for it in head:
            who = it.get("name") or it.get("role")
            snippet = str(it.get("text", ""))[:40].replace("\n", " ")
            lines.append(f"- {_fmt(float(it.get('ts', 0)))} {who}：{snippet}…")
        for it in tail:
            who = it.get("name") or it.get("role")
            lines.append(f"[{_fmt(float(it.get('ts', 0)))}] {who}：{it.get('text', '')}")
        return "\n".join(lines)

    def _pressure(self, umo: str, sess: dict) -> tuple[str, str]:
        """算窗口重量。返回 (级别, 一句话说明)，级别是 none/soft/hard。

        模型看不到自己的 token 用量，所以由这里把"重量"翻译成它能读的字。
        只提示，绝不自动压缩——压什么、留什么，必须她自己定。
        """
        items = _read_lines(umo)
        total = len(items)
        kept = int(sess.get("kept", 0))
        since = max(0, total - kept)

        soft = int(self._cfg("soft_after") or 30)
        hard = int(self._cfg("hard_after") or 60)
        cap = int(self._cfg("max_lines") or 400)
        hard_ratio = float(self._cfg("hard_ratio") or 0.8)

        near_cap = total >= int(cap * hard_ratio)
        if since >= hard or near_cap:
            level = "hard"
        elif since >= soft:
            level = "soft"
        else:
            return "none", ""

        tail = (
            f"本会话原文已经 {total} 条，上次压缩后又攒了 {since} 条。"
            f"再不管就要开始丢最早的了。"
            if level == "hard"
            else f"本会话原文 {total} 条，上次压缩后又攒了 {since} 条。"
        )
        return level, tail

    @filter.on_llm_request(priority=-700)
    async def inject_window(self, event: AstrMessageEvent, req: Any) -> None:
        """注入滚动摘要 + 窗口压力；进程重启后第一次请求额外恢复原文。"""
        if not self._cfg("enabled"):
            return
        try:
            umo = str(event.unified_msg_origin)
            state = _load_state()
            sess = state.get(umo, {})
            blocks = []

            level, why = self._pressure(umo, sess)
            # 只有 future_task 真的在工具表里时才提"定闹钟"——tool_guard 会把它
            # 在非管理员私聊里摘掉，那时说了也是空话
            can_schedule = False
            try:
                tool_set = getattr(req, "func_tool", None)
                can_schedule = bool(
                    tool_set is not None and tool_set.get_tool("future_task") is not None
                )
            except Exception:
                can_schedule = False
            schedule_hint = (
                "有些话现在说不完，也可以给自己定个闹钟，留给明天的你。"
                if can_schedule
                else ""
            )

            if level == "soft":
                blocks.append(
                    "<ctx_pressure>\n"
                    f"（{why}有空的时候，用 read_context_log 翻一翻，"
                    "把该留的挑出来、该扔的压成一段话，用 compact_context 存掉。"
                    f"不着急，你看着办。{schedule_hint}）\n</ctx_pressure>"
                )
            elif level == "hard":
                blocks.append(
                    "<ctx_pressure>\n"
                    f"（{why}该收拾了：先 read_context_log 看一眼，"
                    "挑出你真正想留的，剩下的压成一段自己的话存进 compact_context。"
                    f"不压也行，但最早的那些会先没。{schedule_hint}）\n</ctx_pressure>"
                )

            rolling = str(sess.get("rolling_summary") or "").strip()
            if rolling and self._cfg("inject_rolling"):
                blocks.append(
                    "<ctx_rolling>\n"
                    "（这是你自己早先压缩过的对话梗概，不是用户说的话。"
                    "细节不必复述，知道大概发生过什么就行。）\n"
                    f"{rolling}\n</ctx_rolling>"
                )

            if umo not in self._restored:
                self._restored.add(umo)
                items = _read_lines(umo)
                n = int(self._cfg("restore_n") or 30)
                # 最后 2 条就是刚说的，不用重复；只取 restore_n 条
                if len(items) > 2 and n > 0:
                    window = items[-(n + 2) : -2]
                    body = self._render_log(window, verbatim=10)
                    if body:
                        blocks.append(
                            "<ctx_restored>\n"
                            "（这些是你重启前跟对方聊过的话，原文照录。"
                            "接得上就行，不用逐句提。）\n"
                            f"{body}\n</ctx_restored>"
                        )

            if not blocks:
                return
            from astrbot.core.agent.message import TextPart

            part = TextPart(text="\n\n".join(blocks))
            if hasattr(part, "mark_as_temp"):
                part = part.mark_as_temp()
            req.extra_user_content_parts.append(part)
        except Exception as exc:
            logger.warning(f"[ctx-window] 注入失败（不影响主流程）：{exc}")

    # ------------------------------------------------------------ 工具

    @filter.llm_tool(name="read_context_log")
    async def read_context_log(self, event: AstrMessageEvent, n: float = 30) -> str:
        """翻自己跟这个人最近的原始聊天记录，每条前面有编号。想回忆说过什么、或者准备压缩前挑东西时用。编号可以直接喂给 compact_context 的 keep_ids。行首带 [图 xxxxxxxx] 表示那条里有图：同一张图重发指纹相同，不同的图标着不同指纹——所以文字一样不代表是同一张图。

        Args:
            n(number): 看最近多少条，默认 30，上限 60
        """
        if not self._cfg("enabled"):
            return "上下文窗口现在是关的。"
        umo = str(event.unified_msg_origin)
        items = _read_lines(umo)
        if not items:
            return "这个会话还没有记录。"
        k = max(1, min(int(n or 30), int(self._cfg("max_read"))))
        lines = []
        for idx, it in enumerate(items[-k:], start=max(1, len(items) - k + 1)):
            who = it.get("name") or it.get("role")
            lines.append(
                f"#{idx} [{_fmt(float(it.get('ts', 0)))}] {who}：{it.get('text', '')}"
            )
        return (
            f"共 {len(items)} 条，最近 {len(lines)} 条（编号是绝对序号）：\n"
            + "\n".join(lines)
        )

    @filter.llm_tool(name="compact_context")
    async def compact_context(
        self,
        event: AstrMessageEvent,
        summary: str,
        keep_ids: str = "",
        keep_recent: float = 10,
    ) -> str:
        """把旧对话压成一段话存起来，之后每轮只带这段梗概，不再重复原文。用你自己的口吻写，像在日记里回顾，别写成会议纪要。压什么、留什么由你定：想原样留住的条目录编号放进 keep_ids，其余的收进梗概。

        Args:
            summary(string): 你自己写的梗概，第一人称，不超过 400 字。写发生了什么、你当时怎么想、有什么没说完的
            keep_ids(string): 可选。要原样保留的条目编号，逗号分隔，例如 "3,7,12"。这些不进梗概、留在原文里
            keep_recent(number): 另外保留最近多少条原文，默认 10
        """
        if not self._cfg("enabled"):
            return "上下文窗口现在是关的。"
        text = str(summary or "").strip()
        if not text:
            return "摘要是空的，没存。"
        limit = int(self._cfg("rolling_max_chars"))
        if len(text) > limit:
            return f"太长了（{len(text)} 字），压到 {limit} 字以内。"

        umo = str(event.unified_msg_origin)
        items = _read_lines(umo)
        total = len(items)
        if not total:
            return "这个会话还没有原文可压。"

        # 解析要原样保留的编号（1 基，绝对序号）
        pinned: set[int] = set()
        for part in str(keep_ids or "").replace("，", ",").split(","):
            part = part.strip().lstrip("#")
            if not part:
                continue
            try:
                idx = int(part)
            except ValueError:
                continue
            if 1 <= idx <= total:
                pinned.add(idx)

        keep = max(0, int(keep_recent or 0))
        keep_from = max(1, total - keep + 1) if keep else total + 1
        remain = [
            it
            for idx, it in enumerate(items, start=1)
            if idx in pinned or idx >= keep_from
        ]
        dropped = total - len(remain)
        if dropped <= 0:
            return (
                "这次一条都没压缩（保留的编号 + 最近几条已经覆盖全部原文）。"
                "调大 keep_recent 或换个更小的范围再试。"
            )

        state = _load_state()
        sess = state.setdefault(umo, {})
        sess["rolling_summary"] = text
        sess["compacted_at"] = time.time()
        sess["compacted_count"] = int(sess.get("compacted_count", 0)) + dropped
        sess["kept"] = len(remain)
        _save_state(state)

        _write_lines(umo, remain)
        kept_note = f"，其中按编号留了 {len(pinned)} 条" if pinned else ""
        return (
            f"压缩好了：{dropped} 条收进梗概，原文剩 {len(remain)} 条{kept_note}。"
            "\n之后每轮你都会看到这段梗概。"
        )

    @filter.llm_tool(name="context_status")
    async def context_status(self, event: AstrMessageEvent) -> str:
        """看上下文窗口现在的状态：存了多少条原文、有没有滚动梗概、上次压缩是什么时候。"""
        if not self._cfg("enabled"):
            return "上下文窗口现在是关的。"
        umo = str(event.unified_msg_origin)
        items = _read_lines(umo)
        sess = _load_state().get(umo, {})
        rolling = str(sess.get("rolling_summary") or "")
        parts = [f"原始记录：{len(items)} 条（上限 {self._cfg('max_lines')}）"]
        if items:
            parts.append(
                f"时间范围：{_fmt(float(items[0].get('ts', 0)))} ~ "
                f"{_fmt(float(items[-1].get('ts', 0)))}"
            )
        parts.append(
            f"滚动梗概：{len(rolling)} 字" if rolling else "滚动梗概：还没有"
        )
        if sess.get("compacted_at"):
            parts.append(
                f"上次压缩：{_fmt(float(sess['compacted_at']))}，"
                f"累计收进 {sess.get('compacted_count', 0)} 条"
            )
        level, why = self._pressure(str(event.unified_msg_origin), sess)
        parts.append(
            {
                "none": "窗口压力：还轻，不用管",
                "soft": f"窗口压力：该收拾了 —— {why}",
                "hard": f"窗口压力：偏重 —— {why}",
            }.get(level, "")
        )
        return "\n".join(parts)
