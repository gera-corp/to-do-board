"""Полнота перевода интерфейса: у каждой русской строки должен быть английский вариант."""

import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parent.parent / "app" / "static"
CYRILLIC = re.compile("[а-яА-ЯёЁ]")
PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)


def english_dictionary() -> dict[str, str]:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = source[source.index("/*EN-BEGIN*/"):source.index("/*EN-END*/")]
    return dict(PAIR.findall(body))


def app_js_without_comments() -> str:
    lines = (STATIC / "app.js").read_text(encoding="utf-8").splitlines()
    return "\n".join(re.sub(r"^\s*//.*$|\s//\s.*$", "", line) for line in lines)


def html_texts() -> set[str]:
    found = set()

    class Parser(HTMLParser):
        skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip += 1
            for name, value in attrs:
                if name in ("placeholder", "aria-label", "title") and value and CYRILLIC.search(value):
                    found.add(value)

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self.skip -= 1

        def handle_data(self, data):
            text = re.sub(r"\s+", " ", data).strip()
            if not self.skip and CYRILLIC.search(text):
                found.add(text)

    Parser().feed((STATIC / "index.html").read_text(encoding="utf-8"))
    return found


# Подписи переключателя языков пишутся на своём языке и не переводятся.
UNTRANSLATED = {"Русский"}


def test_every_html_text_is_translated():
    en = english_dictionary()
    missing = sorted(t for t in html_texts() - UNTRANSLATED if t not in en)
    assert missing == []


def test_every_app_js_string_is_translated():
    en = english_dictionary()
    strings = set(re.findall(r'"((?:[^"\\]|\\.)*)"', app_js_without_comments()))
    missing = sorted(s for s in strings if CYRILLIC.search(s) and s not in en)
    assert missing == []


def test_no_untranslated_template_literals():
    templates = re.findall(r"`[^`]*`", app_js_without_comments())
    # Русский текст внутри t("…") переведён — остальной кириллицы в шаблонах быть не должно.
    leftovers = [re.sub(r'\bt\("(?:[^"\\]|\\.)*"', "", tpl) for tpl in templates]
    assert [tpl for tpl in leftovers if CYRILLIC.search(tpl)] == []


def test_placeholders_match():
    for ru, en in english_dictionary().items():
        assert set(re.findall(r"\{(\w+)\}", ru)) == set(re.findall(r"\{(\w+)\}", en)), ru
