"""Запуск сервера: python -m app (настройки берутся из окружения и .env)."""

import os

import uvicorn

from .config import load_env_file

load_env_file()
uvicorn.run(
    "app.main:create_app",
    factory=True,
    host=os.environ.get("HOST") or "0.0.0.0",
    port=int(os.environ.get("PORT") or 8000),
)
