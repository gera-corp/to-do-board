import os

import pytest

from app.config import load_env_file, read_env_file


def test_read_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# комментарий\n"
        "\n"
        "TELEGRAM_BOT_TOKEN=123:ABC-def\n"
        'TODO_PUBLIC_URL="http://192.168.1.10:8000"\n'
        "export PORT=9000  # порт\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    assert read_env_file(path) == {
        "TELEGRAM_BOT_TOKEN": "123:ABC-def",
        "TODO_PUBLIC_URL": "http://192.168.1.10:8000",
        "PORT": "9000",
        "EMPTY": "",
    }
    assert read_env_file(tmp_path / "missing") == {}


def test_read_env_file_reports_bad_line(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OK=1\nэто не настройка\n", encoding="utf-8")
    with pytest.raises(ValueError, match="строка 2"):
        read_env_file(path)


def test_environment_wins_over_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TODO_TEST_A=from_file\nTODO_TEST_B=from_file\nTODO_TEST_C=\n", encoding="utf-8")
    monkeypatch.setenv("TODO_TEST_A", "from_env")
    monkeypatch.delenv("TODO_TEST_B", raising=False)
    monkeypatch.delenv("TODO_TEST_C", raising=False)
    try:
        load_env_file(path)
        assert os.environ["TODO_TEST_A"] == "from_env"
        assert os.environ["TODO_TEST_B"] == "from_file"
        assert "TODO_TEST_C" not in os.environ  # пустые значения пропускаются
    finally:
        os.environ.pop("TODO_TEST_B", None)
