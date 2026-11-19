"""Служебное — не в чат: на заливе пользователь видит статус и результат, а не отладку.

Добавлено в 0.7.10 (aihub-804) после сессии 09.11.2026: агент писал в чат «Исправляю опечатку
в JSON», имена полей API (`age_list`), вложенный JSON возраста и номера шаблонов 513–515.
Шаг 11 сам велел показывать пользователю текст ошибки VK.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _skill() -> str:
    return re.sub(r"\s+", " ", (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8"))


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


def test_style_has_no_tech_in_chat_rule():
    style = _section(_skill(), "# Стиль работы", "# Возобновление")
    assert "**Служебное — не в чат.**" in style
    for phrase in ("JSON", "имена полей", "статус словами", "launch_log.md", "только если пользователь сам попросит"):
        assert phrase in style, phrase


def test_step11_does_not_tell_to_show_error_text():
    skill = _skill()
    assert "покажи её текст пользователю" not in skill
    step11 = _section(skill, "# Шаг 11. Финальный залив в кабинет", "# Шаг 11 (опциональный)")
    assert "Исправление и повтор в чате не пересказывай" in step11
    assert "в чат — только если пользователь попросит" in step11


def test_step11_builds_payload_from_samples_first():
    step11 = _section(_skill(), "# Шаг 11. Финальный залив в кабинет", "# Шаг 11 (опциональный)")
    assert "До первого вызова создания собери payload по образцам этого шага" in step11


def test_step11_no_premature_confidence():
    """0.7.13 (aihub-858): агент обещал «остался один блокер», а VK возвращал новые отказы."""
    step11 = _section(_skill(), "# Шаг 11. Финальный залив в кабинет", "# Шаг 11 (опциональный)")
    assert "До ответа VK с ID созданной кампании не говори, что остальное уже проверено" in step11
    assert "VK возвращает отказы по одному" in step11
