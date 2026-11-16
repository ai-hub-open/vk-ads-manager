"""Рекомендации агента — только подтверждённые: аудитории, LAL, шаги интерфейса, бюджет.

Добавлено в 0.7.9 после сессии 06.11.2026 (кабинет 455194): агент выдал шаги для
несуществующей настройки «Вовлечённые пользователи, 60 дней» и повторял их; предлагал
LAL от подписчиков сообщества (справочник рекомендовал его вопреки списку источников
LAL); выдал «2 × CPA» за обязательный бюджет и советовал 6 000 ₽/день при ограниченном
бюджете клиента.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(rel: str) -> str:
    """Текст с переносами строк, сжатыми в пробел: фразы в markdown переносятся."""
    return _flat((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _paragraphs(path: Path):
    return [_flat(p) for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8"))]


def _all_docs():
    return [REPO_ROOT / "SKILL.md", *sorted((REPO_ROOT / "references").glob("*.md"))]


def test_lal_sources_exclude_community_subscribers():
    text = _read("references/audiences-and-lookalike.md")
    assert "Допустимые источники LAL — только эти четыре" in text
    assert "Подписчиков сообщества в списке нет" in text
    assert "Размер аудитории не заменяет проверку типа источника" in text


def test_no_doc_recommends_lal_from_subscribers():
    pattern = re.compile(r"[Пп]одписчик\w*[^.\n]{0,60}→\s*LAL")
    for path in _all_docs():
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not pattern.search(line), f"{path.name}: LAL от подписчиков: {line.strip()}"


def test_no_engaged_community_audience_suggested():
    text = _read("references/audiences-and-lookalike.md")
    assert "«вовлечённые пользователи»" in text and "Не предлагай их" in text
    for path in _all_docs():
        for par in _paragraphs(path):
            if "вовлечённые пользователи" in par.lower():
                assert "не предлагай" in par.lower(), \
                    f"{path.name}: «вовлечённые пользователи» без запрета: {par[:120]}"


def test_ui_steps_only_confirmed_and_not_repeated():
    text = _read("SKILL.md")
    assert "Шаги интерфейса — только подтверждённые" in text
    assert "не повторяй инструкцию" in text


def test_budget_recommendation_respects_user_limit():
    runbook = _read("references/lifecycle-runbook.md")
    assert "Когда бюджет предлагаешь ты" in runbook
    assert "Ограничение пользователя — рамка" in runbook
    assert "**ориентир**" in runbook
    skill = _read("SKILL.md")
    assert "Бюджет — в рамке пользователя" in skill and "Когда бюджет предлагаешь ты" in skill
