import sqlite3
from pathlib import Path

# Каждая миграция выполняется один раз; номер последней хранится в PRAGMA user_version.
MIGRATIONS = [
    # 1: пользователи, сессии, задачи
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name  TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        color         TEXT NOT NULL DEFAULT '#2F5BD3',
        is_admin      INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id           INTEGER PRIMARY KEY,
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'todo'
                     CHECK (status IN ('todo', 'in_progress', 'review', 'done')),
        priority     TEXT NOT NULL DEFAULT 'normal'
                     CHECK (priority IN ('low', 'normal', 'high')),
        category     TEXT NOT NULL DEFAULT '',
        assignee_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        creator_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        due_date     TEXT,
        recurrence   TEXT NOT NULL DEFAULT 'none'
                     CHECK (recurrence IN ('none', 'daily', 'weekly', 'monthly')),
        position     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, position);
    CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
    """,
    # 2: Telegram, очки, чек-листы, история и комментарии
    """
    ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER;
    ALTER TABLE users ADD COLUMN telegram_link_code TEXT;
    ALTER TABLE tasks ADD COLUMN points INTEGER NOT NULL DEFAULT 1;

    CREATE TABLE checklist_items (
        id       INTEGER PRIMARY KEY,
        task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        text     TEXT NOT NULL,
        done     INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_checklist_task ON checklist_items(task_id, position);

    CREATE TABLE task_events (
        id         INTEGER PRIMARY KEY,
        task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        kind       TEXT NOT NULL,
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_events_task ON task_events(task_id, id);

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 3: тема оформления пользователя
    """
    ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'auto';
    """,
    # 4: вход через Google. Почту назначает администратор; при первом входе
    # запоминается постоянный идентификатор аккаунта (sub).
    """
    ALTER TABLE users ADD COLUMN google_email TEXT;
    ALTER TABLE users ADD COLUMN google_sub TEXT;
    CREATE UNIQUE INDEX idx_users_google_email ON users(google_email) WHERE google_email IS NOT NULL;
    CREATE UNIQUE INDEX idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;

    CREATE TABLE oauth_states (
        state      TEXT PRIMARY KEY,
        nonce      TEXT NOT NULL,
        verifier   TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    # 5: подзадачи (один уровень) и связи между задачами
    """
    ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;
    CREATE INDEX idx_tasks_parent ON tasks(parent_id);

    -- blocks: from_id блокирует to_id (to_id ждёт from_id).
    -- relates: просто связь; хранится с from_id < to_id, чтобы не было дублей.
    CREATE TABLE task_links (
        id         INTEGER PRIMARY KEY,
        kind       TEXT NOT NULL CHECK (kind IN ('blocks', 'relates')),
        from_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        to_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK (from_id != to_id),
        UNIQUE (kind, from_id, to_id)
    );
    CREATE INDEX idx_links_to ON task_links(to_id);
    """,
    # 6: язык интерфейса. lang — выбор в профиле (auto — язык браузера);
    # ui_lang — язык, на котором человек фактически пользуется доской (для уведомлений).
    # Кто пользовался доской до этой версии, видел её на русском.
    """
    ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT 'auto';
    ALTER TABLE users ADD COLUMN ui_lang TEXT NOT NULL DEFAULT 'en';
    UPDATE users SET ui_lang = 'ru';
    """,
    # 7: доски и свои колонки. tasks.status остаётся и хранит роль колонки: на ней держится
    # логика очков, повторов и уведомлений. Пересобирать tasks нельзя: DROP TABLE при
    # включённых внешних ключах каскадно удалил бы чек-листы, историю и связи.
    """
    CREATE TABLE boards (
        id             INTEGER PRIMARY KEY,
        title          TEXT NOT NULL DEFAULT '',
        points_enabled INTEGER NOT NULL DEFAULT 1,
        position       INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE board_columns (
        id       INTEGER PRIMARY KEY,
        board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
        title    TEXT NOT NULL,
        role     TEXT NOT NULL CHECK (role IN ('todo', 'in_progress', 'review', 'done')),
        position INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_columns_board ON board_columns(board_id, position);

    INSERT INTO boards (id, title)
    SELECT 1, CASE WHEN EXISTS (SELECT 1 FROM users) THEN 'Дела по дому' ELSE '' END;
    INSERT INTO board_columns (board_id, title, role, position) VALUES
        (1, 'To Do', 'todo', 0), (1, 'In Progress', 'in_progress', 1),
        (1, 'Review', 'review', 2), (1, 'Done', 'done', 3);

    ALTER TABLE tasks ADD COLUMN board_id INTEGER REFERENCES boards(id);
    ALTER TABLE tasks ADD COLUMN column_id INTEGER REFERENCES board_columns(id);
    UPDATE tasks SET board_id = 1,
                     column_id = (SELECT c.id FROM board_columns c WHERE c.role = tasks.status);
    CREATE INDEX idx_tasks_board ON tasks(board_id);
    CREATE INDEX idx_tasks_column ON tasks(column_id, position);

    -- Установка, которой уже пользуются, сохраняет своё название.
    INSERT INTO settings (key, value)
    SELECT 'site_title', 'Наш дом' WHERE EXISTS (SELECT 1 FROM users);
    """,
]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# WAL быстрее, но требует общей памяти и не работает на сетевых ФС (NFS) — там нужен DELETE.
JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE"}


def init_db(db_path: str, journal_mode: str = "WAL") -> None:
    journal_mode = journal_mode.upper()
    if journal_mode not in JOURNAL_MODES:
        raise ValueError(f"journal_mode должен быть одним из {sorted(JOURNAL_MODES)}")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute(f"PRAGMA journal_mode = {journal_mode}")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    finally:
        conn.close()
