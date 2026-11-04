"""Наблюдатели задач и уведомления о том, что с задачей происходит."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape, unescape
from typing import Mapping, Optional, Sequence

from .i18n import points_word, tr
from .telegram import short_date


def watch(conn: sqlite3.Connection, task_id: int, user_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO task_watchers (task_id, user_id) VALUES (?, ?)", (task_id, user_id))


def unwatch(conn: sqlite3.Connection, task_id: int, user_id: int) -> None:
    conn.execute("DELETE FROM task_watchers WHERE task_id = ? AND user_id = ?", (task_id, user_id))


def watcher_ids(conn: sqlite3.Connection, task_id: int) -> list[int]:
    rows = conn.execute("SELECT user_id FROM task_watchers WHERE task_id = ? ORDER BY user_id", (task_id,))
    return [r["user_id"] for r in rows]


# ---------- очередь уведомлений ----------

# Мелкие правки копятся, чтобы десять отметок в чек-листе пришли одним сообщением.
GROUP_SECONDS = 120
IMMEDIATE = frozenset({"comment", "assigned", "moved", "unblocked", "deleted"})
TAG = re.compile(r"<[^>]+>")


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Message:
    task_id: Optional[int]
    subject: str
    lines: list[str]  # HTML-разметка Telegram, всё содержимое экранировано

    @property
    def text_lines(self) -> list[str]:
        return [unescape(TAG.sub("", line)) for line in self.lines]


def record(conn: sqlite3.Connection, task: Mapping, actor: Mapping, change: dict,
           now: datetime, channels: Sequence) -> None:
    """Кладёт изменение в очередь каждому наблюдателю, кроме автора действия, по каждому его каналу."""
    kind = change["kind"]
    payload = json.dumps({k: v for k, v in change.items() if k != "kind"}, ensure_ascii=False)
    due = now if kind in IMMEDIATE else now + timedelta(seconds=GROUP_SECONDS)
    ids = [u for u in watcher_ids(conn, task["id"]) if u != actor["id"]]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    for user in conn.execute(f"SELECT * FROM users WHERE id IN ({marks})", ids).fetchall():
        for channel in channels:
            if channel.address(user) is None:
                continue
            conn.execute(
                """INSERT INTO outbox (user_id, channel, task_id, task_title, actor_name, kind, payload,
                                       created_at, due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user["id"], channel.name, task["id"], task["title"], actor["display_name"], kind, payload,
                 iso(now), iso(due)),
            )


def quoted_title(title: str) -> str:
    return f"«{escape(title)}»"


def render_row(row: sqlite3.Row, lang: str, user_id: int) -> Optional[str]:
    p = json.loads(row["payload"])
    actor, task = escape(row["actor_name"]), quoted_title(row["task_title"])
    kind = row["kind"]
    if kind == "comment":
        return tr("💬 {actor} о задаче {task}:", lang, actor=actor, task=task) + "\n" + escape(p["text"])
    if kind == "assigned":
        if p["to"] == user_id:
            text = tr("📌 {actor} поручает вам: {task}", lang, actor=actor, task=task)
            if p.get("due_date"):
                text += "\n" + tr("Срок: {date}", lang, date=short_date(p["due_date"], lang))
            return text
        if p["to"] is None:
            return tr("👤 {actor} снимает исполнителя: {task}", lang, actor=actor, task=task)
        return tr("👤 {actor} назначает {name}: {task}", lang, actor=actor, name=escape(p["to_name"]), task=task)
    if kind == "moved":
        role_changed = p["from_role"] != p["to_role"]
        if role_changed and p["to_role"] == "review":
            return tr("👀 {actor} просит проверить: {task}", lang, actor=actor, task=task)
        if role_changed and p["to_role"] == "done":
            text = tr("✅ Сделано: {task}", lang, task=task)
            if p.get("points"):
                text += tr(", +{n} {points}", lang, n=p["points"], points=points_word(p["points"], lang))
            return text + "\n" + tr("Отметил(а): {actor}", lang, actor=actor)
        return tr("➡️ {actor} переносит {task}: {old} → {new}", lang, actor=actor, task=task,
                  old=escape(p["from_title"]), new=escape(p["to_title"]))
    if kind == "unblocked":
        return (tr("🔓 Можно начинать: {task}", lang, task=task) + "\n"
                + tr("Готово: {task}", lang, task=quoted_title(p["done_title"])))
    return None


def render(rows: Sequence[sqlite3.Row], lang: str, user_id: int) -> Optional[Message]:
    """Склеивает строки одной группы (получатель, канал, задача) в одно сообщение."""
    blocks = [b for b in (render_row(r, lang, user_id) for r in rows) if b]
    if not blocks:
        return None
    last = rows[-1]
    return Message(last["task_id"], last["task_title"], "\n\n".join(blocks).split("\n"))
