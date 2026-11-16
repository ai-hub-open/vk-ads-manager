"""Стандартные предупреждения VK выводит сам по `disc_id` — в текст объявления их не пишем.

Добавлено в 0.7.8 после сессии 06.11.2026: агент по правилу Шага 10 вписал
«Имеются противопоказания. Проконсультируйтесь со специалистом.» в text_2000 трёх
объявлений медклиники, и предупреждение задвоилось с тем, что выводит VK.
Живая проверка 16.11.2026: справочник /api/v2/banner_disclaimers.json (6 MEDICAL,
8 DRUGS, 9 DIETARY, 11 CREDITS…), у стоматологии все объявления с disc_id 6 и без
предупреждения в текстах.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SECTION = "Куда размещать"

# Инструкция вписать медпредупреждение в текст: «дисклеймер: «Имеются противопоказания…»»
# без запрета рядом. Таблицы закона (что требует закон) и запреты допустимы.
ADD_PHRASE = re.compile(r"[Дд]исклеймер[^|\n]{0,40}«(Имеются|Есть) противопоказания")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_canonical_section_lists_platform_disclaimers():
    text = _read("references/moderation-rules.md")
    start = text.index(f"### {SECTION}")
    body = text[start:text.find("\n## ", start)]
    assert "banner_disclaimers" in body and "disc_id" in body
    for disc_id, name in [("6", "MEDICAL"), ("8", "DRUGS"), ("9", "DIETARY"), ("11", "CREDITS")]:
        assert re.search(rf"\|\s*{disc_id}\s*\|\s*`{name}`", body), f"нет строки {disc_id} {name}"
    assert "Текст из справочника не пиши" in body
    assert "После создания проверь" in body


@pytest.mark.parametrize("rel", [
    "SKILL.md",
    "references/ad-copywriting.md",
    "references/vk-ads-specs.md",
    "references/ord-agency.md",
])
def test_route_docs_point_to_canonical_section(rel):
    text = _read(rel)
    assert "disc_id" in text, f"{rel}: нет упоминания disc_id"
    assert SECTION in text, f"{rel}: нет ссылки на «{SECTION}»"


def test_no_doc_tells_to_put_medical_warning_into_text():
    paths = [REPO_ROOT / "SKILL.md", *sorted((REPO_ROOT / "references").glob("*.md"))]
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("|"):
                continue  # таблица требований закона, а не инструкция для текста
            assert not ADD_PHRASE.search(line), f"{path.name}: велит вписать предупреждение: {line.strip()}"


def test_step11_check_reads_disc_id():
    text = _read("SKILL.md")
    step = text[text.index("6. **Проверка:** `vk_ads_ad_plans_get"):]
    step = step[:step.index("\n## ")]
    assert "disc_id" in step
