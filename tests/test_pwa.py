import json
import struct

from app.main import static_version


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_service_worker_is_versioned_and_not_cached(anon):
    r = anon.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    assert "__VERSION__" not in r.text
    assert 'const VERSION = "' in r.text


def test_manifest_and_icons(anon):
    r = anon.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.text)
    assert m["start_url"] == "/" and m["display"] == "standalone"
    # имя приложения — на языке браузера, по умолчанию английское
    assert (m["name"], m["lang"]) == ("Task Board", "en")
    ru = anon.get("/manifest.webmanifest", headers={"Accept-Language": "ru-RU,ru;q=0.9"}).json()
    assert (ru["name"], ru["lang"]) == ("Доска задач", "ru")
    purposes = set()
    for icon in m["icons"]:
        img = anon.get(icon["src"])
        assert img.status_code == 200, icon["src"]
        if icon["type"] == "image/png":
            w, h = png_size(img.content)
            assert icon["sizes"] == f"{w}x{h}"
        purposes.add(icon.get("purpose", "any"))
    assert {"any", "maskable"} <= purposes


def test_static_version_follows_content(tmp_path):
    (tmp_path / "a.js").write_text("one")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.css").write_text("two")
    first = static_version(tmp_path)
    assert static_version(tmp_path) == first
    (tmp_path / "sub" / "b.css").write_text("three")
    assert static_version(tmp_path) != first
