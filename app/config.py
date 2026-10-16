"""Загрузка настроек из файла .env в переменные окружения.

Формат: строки КЛЮЧ=значение, комментарии с #, значения можно брать в кавычки.
Переменные, уже заданные в окружении, файл не перезаписывает.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_env_file(path: str | Path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            raise ValueError(f"{path}, строка {number}: ожидается КЛЮЧ=значение")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(path: str | Path | None = None) -> None:
    path = path or os.environ.get("TODO_ENV_FILE") or PROJECT_ROOT / ".env"
    for key, value in read_env_file(path).items():
        if value:
            os.environ.setdefault(key, value)
