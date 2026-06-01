"""Stream history storage and stream-json parsing."""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Callable, Optional


def extract_stream_session_id(raw_line: str) -> Optional[str]:
    stripped = raw_line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        ev = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if ev.get("type") == "system" and ev.get("subtype") == "init":
        session_id = str(ev.get("session_id") or "").strip()
        return session_id or None
    return None


class StreamManager:
    def __init__(
        self,
        db: sqlite3.Connection,
        logger: logging.Logger,
        persist_session_id: Callable[[str, str], None],
        record_credits: Callable[[str, dict], None],
    ) -> None:
        self.db = db
        self.logger = logger
        self.persist_session_id = persist_session_id
        self.record_credits = record_credits
        self.logs_by_task: dict[str, list[str]] = {}
        self.order_by_task: dict[str, list[str]] = {}
        self.items_by_task: dict[str, dict[str, dict]] = {}
        self.version_by_task: dict[str, int] = {}
        self.parser_state_by_task: dict[str, dict] = {}

    def load_history(self, task_id: str) -> tuple[list[dict], list[str]]:
        rows = self.db.execute(
            """SELECT item_id, kind, role, content, meta_json, final, version, created_at
               FROM stream_items WHERE task_id = ? ORDER BY version""",
            (task_id,),
        ).fetchall()
        items = []
        order = []
        for r in rows:
            item = {
                "id": r[0],
                "kind": r[1],
                "role": r[2],
                "content": r[3],
                "meta": json.loads(r[4]) if r[4] and r[4] != "{}" else None,
                "final": bool(r[5]),
                "v": r[6],
                "timestamp": r[7],
            }
            items.append(item)
            order.append(r[0])
        return items, order

    def delete_history(self, task_id: str) -> None:
        self.db.execute("DELETE FROM stream_items WHERE task_id = ?", (task_id,))

    def append_log(self, task_id: str, line: str) -> None:
        if task_id not in self.logs_by_task:
            self.logs_by_task[task_id] = []
        buf = self.logs_by_task[task_id]
        buf.append(line)
        if len(buf) > 2000:
            self.logs_by_task[task_id] = buf[-2000:]

    def logs(self, task_id: str) -> list[str]:
        return self.logs_by_task.get(task_id, [])

    def log_and_stream(self, task_id: str, line: str) -> None:
        self.append_log(task_id, line)
        self.feed_line(task_id, line)

    def reset_task_stream(self, task_id: str) -> None:
        self.logs_by_task[task_id] = []
        self.items_by_task[task_id] = {}
        self.order_by_task[task_id] = []
        self.version_by_task[task_id] = self._stream_history_max_version(task_id)
        self.parser_state_by_task[task_id] = self._new_parser_state()

    def flush_items(self, task_id: str, last_item_versions: dict[str, int]) -> list[dict]:
        items = self.items_by_task.get(task_id, {})
        order = self.order_by_task.get(task_id, [])
        payloads: list[dict] = []
        for iid in order:
            item = items.get(iid)
            if item is None:
                continue
            v = int(item.get("v", 0))
            if v > last_item_versions.get(iid, 0):
                last_item_versions[iid] = v
                payloads.append(item)
        return payloads

    def feed_line(self, task_id: str, raw_line: str) -> None:
        stripped = raw_line.strip()
        if not stripped:
            return

        if stripped.startswith("[SYSTEM]"):
            item_id = self._stream_item_id(task_id, f"sys-{self._next_item_version(task_id)}")
            self._stream_upsert(task_id, item_id, {
                "kind": "system_note",
                "role": "system",
                "content": stripped[len("[SYSTEM]"):].strip(),
                "final": True,
            })
            return

        if not (stripped.startswith("{") and stripped.endswith("}")):
            return

        try:
            ev = json.loads(stripped)
        except json.JSONDecodeError:
            return

        session_id = extract_stream_session_id(stripped)
        if session_id:
            self.persist_session_id(task_id, session_id)

        state = self._parser_state(task_id)
        t = ev.get("type")
        sub = ev.get("subtype")

        if t == "system" and sub == "init":
            sid = ev.get("session_id", "")
            item_id = self._stream_item_id(task_id, "sys-init")
            self._stream_upsert(task_id, item_id, {
                "kind": "system_note",
                "role": "system",
                "content": f"Session started — {sid[:12]}…",
                "final": True,
            })
            return

        if t == "stream_event":
            self._feed_stream_event(task_id, ev, state)
            return

        if t == "user":
            self._feed_user_tool_results(task_id, ev)
            return

        if t == "system" and sub == "task_progress":
            self._feed_task_progress(task_id, ev, state)
            return

        if t == "system" and sub in ("task_updated", "task_notification"):
            self._feed_task_status_update(task_id, ev, state)
            return

        if t == "result":
            self._feed_result(task_id, ev)

    def _feed_stream_event(self, task_id: str, ev: dict, state: dict) -> None:
        event = ev.get("event") or {}
        et = event.get("type")

        if et == "message_start":
            msg = event.get("message") or {}
            state["current_msg_id"] = msg.get("id")
            return

        if et == "content_block_start":
            block = event.get("content_block") or {}
            idx = event.get("index")
            bt = block.get("type")
            if bt == "text":
                item_id = self._stream_item_id(task_id, f"{state.get('current_msg_id') or 'msg'}-text-{idx}")
                state["current_text_id"] = item_id
                self._stream_upsert(task_id, item_id, {
                    "kind": "assistant_text",
                    "role": "assistant",
                    "content": "",
                    "final": False,
                })
            elif bt == "tool_use":
                raw_tool_id = block.get("id") or f"tu-{idx}"
                tool_id = self._stream_item_id(task_id, raw_tool_id)
                tool_name = block.get("name") or "Tool"
                state["current_tool"] = {"id": tool_id, "name": tool_name, "input_buf": ""}
                self._stream_upsert(task_id, tool_id, {
                    "kind": "tool_call",
                    "role": "tool",
                    "content": "",
                    "meta": {
                        "tool_name": tool_name,
                        "status": "starting",
                        "input_summary": "",
                        "subagent_activities": [],
                    },
                    "final": False,
                })
            elif bt == "thinking":
                item_id = self._stream_item_id(task_id, f"{state.get('current_msg_id') or 'msg'}-think-{idx}")
                state["current_text_id"] = item_id
                self._stream_upsert(task_id, item_id, {
                    "kind": "thinking",
                    "role": "assistant",
                    "content": "",
                    "final": False,
                })
            return

        if et == "content_block_delta":
            delta = event.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                tid = state.get("current_text_id")
                if tid:
                    self._stream_upsert(task_id, tid, {"append": delta.get("text", "")})
            elif dt == "thinking_delta":
                tid = state.get("current_text_id")
                if tid:
                    self._stream_upsert(task_id, tid, {"append": delta.get("thinking", "")})
            elif dt == "input_json_delta":
                tool = state.get("current_tool")
                if tool:
                    tool["input_buf"] += delta.get("partial_json", "")
            return

        if et == "content_block_stop":
            if state.get("current_text_id"):
                self._stream_upsert(task_id, state["current_text_id"], {"final": True})
                state["current_text_id"] = None
            elif state.get("current_tool"):
                tool = state["current_tool"]
                parsed_input: dict = {}
                if tool["input_buf"]:
                    try:
                        parsed_input = json.loads(tool["input_buf"])
                    except json.JSONDecodeError:
                        parsed_input = {"_raw": tool["input_buf"][:500]}
                summary = self._tool_input_summary(tool["name"], parsed_input)
                is_subagent = tool["name"] in ("Task", "Agent")
                if is_subagent:
                    state["last_subagent_tool_id"] = tool["id"]
                self._stream_upsert(task_id, tool["id"], {
                    "meta": {
                        "status": "running",
                        "input_summary": summary,
                        "input_full": parsed_input if len(str(parsed_input)) < 2000 else {"_truncated": True},
                        "is_subagent": is_subagent,
                    },
                })
                state["current_tool"] = None

    def _feed_user_tool_results(self, task_id: str, ev: dict) -> None:
        msg = ev.get("message") or {}
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if not tool_use_id:
                    continue
                tool_use_id = self._stream_item_id(task_id, tool_use_id)
                is_err = bool(block.get("is_error"))
                summary = self._tool_result_summary(block.get("content"))
                items = self.items_by_task.get(task_id, {})
                if tool_use_id in items:
                    self._stream_upsert(task_id, tool_use_id, {
                        "meta": {
                            "status": "error" if is_err else "done",
                            "result_summary": summary,
                        },
                        "final": True,
                    })

    def _feed_task_progress(self, task_id: str, ev: dict, state: dict) -> None:
        raw_tool_use_id = ev.get("tool_use_id")
        tool_use_id = self._stream_item_id(task_id, raw_tool_use_id) if raw_tool_use_id else state.get("last_subagent_tool_id")
        desc = ev.get("description") or ""
        usage = ev.get("usage") or {}
        duration_ms = usage.get("duration_ms", 0)
        if tool_use_id:
            existing = self.items_by_task.get(task_id, {}).get(tool_use_id)
            if existing is not None:
                activities = list((existing.get("meta") or {}).get("subagent_activities") or [])
                activities.append({
                    "description": desc[:200],
                    "duration_ms": duration_ms,
                    "tool_uses": usage.get("tool_uses"),
                })
                if len(activities) > 50:
                    activities = activities[-50:]
                self._stream_upsert(task_id, tool_use_id, {
                    "meta": {
                        "status": "running",
                        "subagent_activities": activities,
                        "last_activity": desc[:200],
                        "last_duration_ms": duration_ms,
                    },
                })

    def _feed_task_status_update(self, task_id: str, ev: dict, state: dict) -> None:
        raw_tool_use_id = ev.get("tool_use_id")
        tool_use_id = self._stream_item_id(task_id, raw_tool_use_id) if raw_tool_use_id else state.get("last_subagent_tool_id")
        patch = ev.get("patch") or {}
        new_status = patch.get("status") or ev.get("status")
        if tool_use_id and new_status:
            self._stream_upsert(task_id, tool_use_id, {
                "meta": {"status": new_status},
            })

    def _feed_result(self, task_id: str, ev: dict) -> None:
        is_err = bool(ev.get("is_error"))
        result_text = ev.get("result") or ""
        display_content = result_text if is_err else ""
        item_id = self._stream_item_id(task_id, "final-result")
        self._stream_upsert(task_id, item_id, {
            "kind": "final_result",
            "role": "assistant",
            "content": display_content,
            "meta": {
                "is_error": is_err,
                "duration_ms": ev.get("duration_ms"),
                "total_cost_usd": ev.get("total_cost_usd"),
                "num_turns": ev.get("num_turns"),
            },
            "final": True,
        })
        self.record_credits(task_id, ev)

    def _next_item_version(self, task_id: str) -> int:
        v = self.version_by_task.get(task_id, 0) + 1
        self.version_by_task[task_id] = v
        return v

    def _stream_history_max_version(self, task_id: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM stream_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0] or 0)

    def _new_parser_state(self, stream_run_id: Optional[str] = None) -> dict:
        return {
            "current_msg_id": None,
            "current_text_id": None,
            "current_tool": None,
            "last_subagent_tool_id": None,
            "stream_run_id": stream_run_id or f"run-{uuid.uuid4().hex}",
        }

    def _parser_state(self, task_id: str) -> dict:
        return self.parser_state_by_task.setdefault(task_id, self._new_parser_state())

    def _stream_item_id(self, task_id: str, raw_item_id: str) -> str:
        state = self._parser_state(task_id)
        return f"{state['stream_run_id']}:{raw_item_id}"

    def _stream_upsert(self, task_id: str, item_id: str, patch: dict) -> dict:
        items = self.items_by_task.setdefault(task_id, {})
        order = self.order_by_task.setdefault(task_id, [])
        existing = items.get(item_id)
        if existing is None:
            item = {"id": item_id, "v": self._next_item_version(task_id), **patch}
            items[item_id] = item
            order.append(item_id)
        else:
            if "append" in patch:
                existing["content"] = (existing.get("content") or "") + patch.pop("append")
            if "meta" in patch and isinstance(existing.get("meta"), dict):
                existing["meta"] = {**existing["meta"], **patch.pop("meta")}
            existing.update(patch)
            existing["v"] = self._next_item_version(task_id)
            item = existing

        self.db.execute(
            """INSERT INTO stream_items (task_id, item_id, kind, role, content, meta_json, final, version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id, item_id) DO UPDATE SET
                   kind=excluded.kind, role=excluded.role, content=excluded.content,
                   meta_json=excluded.meta_json, final=excluded.final, version=excluded.version
            """,
            (
                task_id,
                item_id,
                item.get("kind", ""),
                item.get("role", ""),
                item.get("content", ""),
                json.dumps(item.get("meta"), ensure_ascii=False) if item.get("meta") else "{}",
                1 if item.get("final") else 0,
                item["v"],
                datetime.utcnow().isoformat(),
            ),
        )
        self.db.commit()
        return item

    def _tool_input_summary(self, tool_name: str, tool_input: dict) -> str:
        if not isinstance(tool_input, dict):
            return str(tool_input)[:200]
        if tool_name == "Bash":
            return str(tool_input.get("command", ""))[:200]
        if tool_name in ("Read", "Glob", "Grep"):
            return str(tool_input.get("path") or tool_input.get("file_path") or tool_input.get("pattern") or "")[:200]
        if tool_name in ("Write", "Edit", "MultiEdit"):
            return str(tool_input.get("file_path") or tool_input.get("path") or "")[:200]
        if tool_name in ("Task", "Agent"):
            return str(tool_input.get("subagent_type") or tool_input.get("description") or "")[:200]
        try:
            return json.dumps(tool_input, ensure_ascii=False)[:200]
        except TypeError:
            return str(tool_input)[:200]

    def _tool_result_summary(self, content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip().splitlines()[0][:300] if content.strip() else ""
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", ""))[:300])
                    elif "content" in block:
                        parts.append(str(block["content"])[:300])
                else:
                    parts.append(str(block)[:300])
            joined = " | ".join(p for p in parts if p)
            return joined[:300]
        return str(content)[:300]
