"""
_workspace.py — рабочая папка кампании всегда вне папки скилла.

Зачем. Скрипты запускаются как `python -m scripts.<имя>`, а это работает только из
папки скилла. Относительный `--workspace vk-campaign-acme` тогда ляжет внутрь скилла.
Скилл, установленный плагином Claude Code, при обновлении заменяется целиком —
бриф, креативы и логи клиента пропадут вместе со старой версией.

Как пользоваться. Вместо `Path(args.workspace)`:

    from scripts._workspace import resolve_workspace
    workspace = resolve_workspace(args.workspace)

Путь приводится к абсолютному; если он внутри скилла — скрипт останавливается
с объяснением, что передать.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def is_inside_skill(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(SKILL_ROOT)
    except ValueError:
        return False
    return True


def resolve_workspace(raw, what: str = "Рабочая папка") -> Path:
    """Абсолютный путь к рабочей папке; внутри скилла — остановка с ошибкой."""
    path = Path(raw).expanduser().resolve()
    if is_inside_skill(path):
        sys.exit(
            f"{what} {path} лежит внутри папки скилла ({SKILL_ROOT}).\n"
            "При обновлении плагина папка скилла заменяется целиком — данные клиента пропадут.\n"
            "Передай абсолютный путь вне скилла, например в папке пользователя: "
            "<папка пользователя>/vk-campaign-<slug>"
        )
    return path
