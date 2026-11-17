"""В текстах репозитория нет абсолютных путей к чьей-то домашней папке.

Добавлено в 0.7.11: с 0.5.0 примеры в references/visual-generation.md содержали путь
с именем пользователя Windows автора, и он уходил во все архивы скилла.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".md", ".py", ".json", ".txt", ".sh", ".bat", ".yml", ".yaml", ".toml", ".ini"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist"}
# Имя пользователя после домашней папки; плейсхолдер в угловых скобках (<user>) — допустим.
# /home/ и /Users/ — только в начале пути, не внутри URL (…/d/home/… в ссылке на документацию).
HOME_PATH = re.compile(
    r"(?i)(?:(?<!\w)[a-z]:[\\/]+users[\\/]+|(?<![\w.:/-])(?:/users/|/home/))(?!<)([a-z0-9._-]+)"
)
GENERIC = {"public", "default", "shared", "runner", "user", "username", "you", "me", "name"}


def _files():
    for p in REPO_ROOT.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and not SKIP_DIRS & set(p.parts):
            yield p


def test_no_personal_home_paths():
    found = []
    for p in _files():
        if p.name == Path(__file__).name:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for m in HOME_PATH.finditer(line):
                if m.group(1).lower() not in GENERIC:
                    found.append(f"{p.relative_to(REPO_ROOT)}:{n}: {m.group(0)}")
    assert not found, "личные пути в репозитории:\n" + "\n".join(found)
