#!/usr/bin/env bash
# Запуск: ./run.sh  Настройки — в .env (шаблон: .env.example) или в переменных окружения.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m app
