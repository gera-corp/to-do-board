"""Наблюдатели задач и уведомления о том, что с задачей происходит."""

import sqlite3


def watch(conn: sqlite3.Connection, task_id: int, user_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO task_watchers (task_id, user_id) VALUES (?, ?)", (task_id, user_id))


def unwatch(conn: sqlite3.Connection, task_id: int, user_id: int) -> None:
    conn.execute("DELETE FROM task_watchers WHERE task_id = ? AND user_id = ?", (task_id, user_id))


def watcher_ids(conn: sqlite3.Connection, task_id: int) -> list[int]:
    rows = conn.execute("SELECT user_id FROM task_watchers WHERE task_id = ? ORDER BY user_id", (task_id,))
    return [r["user_id"] for r in rows]
