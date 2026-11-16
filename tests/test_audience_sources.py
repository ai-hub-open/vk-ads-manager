"""Готовая аудитория сообщества: `relations[].object_id` — не ID сообщества.

Добавлено в 0.7.7 после сессии 06.11.2026: агент открыл `vk.com/club<object_id>`
источника `remarketing_vk_group`, получил «удалено» и дважды отклонил правильную
аудиторию. Живая проверка 16.11.2026: `object_id` — внутренний ID источника в VK
Рекламе (223 сообщества сегмента — 223 номера подряд), в сообщество его не перевести.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SECTION = "Готовая аудитория из кабинета"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, title: str) -> str:
    start = text.index(f"\n## {title}")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_reference_explains_object_id_and_rules():
    body = _section(_read("references/audiences-and-lookalike.md"), SECTION)
    assert "внутренний ID источника" in body
    assert "users_count" in body  # размер аудитории API не отдаёт
    assert "Не составляй адрес сообщества из `object_id`" in body
    assert "не отклоняй" in body
    assert "Опровергнутое не повторяй" in body
    assert "remarketing_vk_group" in body and "object_type" in body


@pytest.mark.parametrize("rel", ["SKILL.md", "references/lifecycle-runbook.md"])
def test_route_docs_point_to_rule(rel):
    text = _read(rel)
    assert SECTION in text, f"{rel}: нет ссылки на раздел «{SECTION}»"
    assert "не ID сообщества" in text or "а не ID сообщества" in text


def test_no_doc_builds_community_url_from_object_id():
    """Адрес `club<object_id>` допустим только в запрете, не как способ проверки."""
    pattern = re.compile(r"(club|public)<object_id>")
    for path in [REPO_ROOT / "SKILL.md", *sorted((REPO_ROOT / "references").glob("*.md"))]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if pattern.search(line):
                assert re.search(r"не составляй|Не составляй", line), (
                    f"{path.name}: адрес сообщества из object_id без запрета: {line.strip()}"
                )
