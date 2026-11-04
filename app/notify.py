"""Наблюдатели задач и уведомления о том, что с задачей происходит."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape, unescape
from typing import Mapping, Optional, Sequence

from .i18n import points_word, tr
from .telegram import short_date, task_line


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
IMMEDIATE = frozenset({"comment", "assigned", "moved", "unblocked", "deleted", "digest"})
TAG = re.compile(r"<[^>]+>")


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Message:
    task_id: Optional[int]
    subject: str
    lines: list[str]  # HTML-разметка Telegram, всё содержимое экранировано
    lang: str = "en"

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
    if kind == "deleted":
        return tr("🗑 {actor} удаляет задачу {task}", lang, actor=actor, task=task)
    if kind == "digest":
        return "\n".join(p["lines"])
    return None


FIELD_LABELS = {"title": "Название", "description": "Описание", "priority": "Приоритет",
                "category": "Категория", "due_date": "Срок", "recurrence": "Повтор", "points": "Очки"}
PRIORITY_WORDS = {"low": "низкий", "normal": "обычный", "high": "высокий"}
RECURRENCE_WORDS = {"none": "не повторяется", "daily": "каждый день", "weekly": "каждую неделю",
                    "monthly": "каждый месяц"}
LINK_WORDS = {"blocks": "блокирует", "blocked_by": "ждёт", "relates": "связана с"}
CHECKLIST_LINES = {
    "added": "{actor}: + пункт {item}",
    "checked": "{actor}: ☑ {item}",
    "unchecked": "{actor}: ☐ {item}",
    "removed": "{actor}: − пункт {item}",
    "renamed": "{actor}: ✎ {old} → {item}",
}


def field_value(field: str, value, lang: str) -> str:
    if value is None or value == "":
        return "—"
    if field == "due_date":
        return short_date(value, lang)
    if field == "priority":
        return tr(PRIORITY_WORDS[value], lang)
    if field == "recurrence":
        return tr(RECURRENCE_WORDS[value], lang)
    return escape(str(value))


def change_line(kind: str, actor_name: str, p: dict, lang: str) -> str:
    """Одна строка о мелкой правке: кто и что поменял."""
    actor = escape(actor_name)
    if kind == "field":
        if p["field"] == "description":
            return tr("{actor}: описание изменено", lang, actor=actor)
        return tr("{actor}: {field}: {old} → {new}", lang, actor=actor, field=tr(FIELD_LABELS[p["field"]], lang),
                  old=field_value(p["field"], p["old"], lang), new=field_value(p["field"], p["new"], lang))
    if kind == "checklist":
        return tr(CHECKLIST_LINES[p["action"]], lang, actor=actor, item=quoted_title(p["text"]),
                  old=quoted_title(p.get("old_text") or ""))
    if kind == "link":
        template = "{actor}: новая связь — {phrase} {other}" if p["action"] == "added" else "{actor}: убрана связь — {phrase} {other}"
        return tr(template, lang, actor=actor, phrase=tr(LINK_WORDS[p["type"]], lang), other=quoted_title(p["other_title"]))
    template = "{actor}: новая подзадача {title}" if p["action"] == "added" else "{actor}: подзадача убрана {title}"
    return tr(template, lang, actor=actor, title=quoted_title(p["title"]))


def collapse_fields(items: list[tuple[sqlite3.Row, dict]]) -> list[tuple[sqlite3.Row, dict]]:
    """Поле, поменянное несколько раз, — одна строка «первое → последнее»; вернули как было — ни одной."""
    positions: dict[str, list[int]] = {}
    for index, (row, p) in enumerate(items):
        if row["kind"] == "field":
            positions.setdefault(p["field"], []).append(index)
    result = []
    for index, (row, p) in enumerate(items):
        if row["kind"] != "field":
            result.append((row, p))
            continue
        same = positions[p["field"]]
        first_old = items[same[0]][1]["old"]
        if index == same[-1] and first_old != p["new"]:
            result.append((row, {**p, "old": first_old}))
    return result


def render(rows: Sequence[sqlite3.Row], lang: str, user_id: int) -> Optional[Message]:
    """Склеивает строки одной группы (получатель, канал, задача) в одно сообщение."""
    items = collapse_fields([(r, json.loads(r["payload"])) for r in rows])
    last = rows[-1]
    edits = [change_line(r["kind"], r["actor_name"], p, lang) for r, p in items if r["kind"] not in IMMEDIATE]
    blocks = [b for b in (render_row(r, lang, user_id) for r, _ in items if r["kind"] in IMMEDIATE) if b]
    if edits:
        header = tr("✏️ Изменения в задаче {task}:", lang, task=quoted_title(last["task_title"]))
        blocks.insert(0, "\n".join([header, *edits]))
    if not blocks:
        return None
    return Message(last["task_id"], last["task_title"], "\n\n".join(blocks).split("\n"), lang)


def build_digest(conn: sqlite3.Connection, today: date) -> list[tuple[int, Message]]:
    """Утренняя сводка для каждого, у кого есть задачи на сегодня, на завтра или просроченные."""
    messages = []
    for u in conn.execute("SELECT * FROM users").fetchall():
        lang = u["ui_lang"]
        rows = conn.execute(
            """SELECT * FROM tasks WHERE assignee_id = ? AND status != 'done'
               AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date, position""",
            (u["id"], (today + timedelta(days=1)).isoformat()),
        ).fetchall()
        if not rows:
            continue
        overdue = [t for t in rows if t["due_date"] < today.isoformat()]
        due_today = [t for t in rows if t["due_date"] == today.isoformat()]
        tomorrow = [t for t in rows if t["due_date"] > today.isoformat()]
        parts = [tr("☀️ Доброе утро! Вот что на сегодня.", lang)]
        for title, group in (("Сегодня", due_today), ("Просрочено", overdue), ("Завтра", tomorrow)):
            if group:
                parts.append(f"\n<b>{tr(title, lang)}</b>")
                parts += [task_line(t, lang) for t in group]
        lines = "\n".join(parts).split("\n")
        messages.append((u["id"], Message(None, tr("Задачи на сегодня", lang), lines, lang)))
    return messages
