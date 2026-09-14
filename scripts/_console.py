"""
_console.py — UTF-8 на stdout/stderr для всех CLI-скриптов скилла.

Зачем. Консоль Windows по умолчанию не UTF-8 (cp1251/cp866/cp1252), а весь вывод
скриптов и тексты argparse написаны кириллицей. Без этой настройки падает даже
`--help`:

    python -m scripts.forecast --help
    UnicodeEncodeError: 'charmap' codec can't encode characters in position 58-64

Как пользоваться. В начале `main()` (до `parse_args`, иначе упадёт вывод справки):

    from scripts._console import setup_console
    setup_console()

Вызов идемпотентен и ничего не делает там, где консоль уже UTF-8 — на macOS,
Linux и в Windows Terminal с `PYTHONIOENCODING=utf-8`.
"""
from __future__ import annotations

import sys

_done = False


def setup_console() -> None:
    """Переключает stdout/stderr на UTF-8. Безопасно при повторных вызовах."""
    global _done
    if _done:
        return
    _done = True
    for stream in (sys.stdout, sys.stderr):
        # AttributeError — поток подменён на объект без reconfigure (пайпы в тестах,
        # некоторые IDE). ValueError — поток уже закрыт. В обоих случаях делать нечего.
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
