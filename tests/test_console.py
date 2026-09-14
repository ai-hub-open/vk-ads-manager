"""UTF-8 на консоли (`scripts/_console.py`) и его подключение во всех точках входа.

Регрессия на блокер 0.6.6: консоль Windows по умолчанию cp1251/cp866/cp1252,
и 13 из 14 CLI-скриптов падали `UnicodeEncodeError` на любом выводе кириллицей —
не работал даже `--help`. Здесь проверяем и сам хелпер, и то, что его никто
не забыл позвать.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from scripts import _console

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent


class FakeStream:
    """Поток, считающий вызовы reconfigure."""

    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


class NoReconfigureStream:
    """Поток без reconfigure — так выглядят подменённые потоки в IDE и пайпах."""


@pytest.fixture(autouse=True)
def _reset_done(monkeypatch):
    """setup_console идемпотентен через модульный флаг — сбрасываем его на каждый тест."""
    monkeypatch.setattr(_console, "_done", False)


# ---------- сам хелпер ----------

def test_switches_both_streams_to_utf8(monkeypatch):
    out, err = FakeStream(), FakeStream()
    monkeypatch.setattr(_console.sys, "stdout", out)
    monkeypatch.setattr(_console.sys, "stderr", err)

    _console.setup_console()

    assert out.calls == [{"encoding": "utf-8"}]
    assert err.calls == [{"encoding": "utf-8"}]


def test_second_call_is_noop(monkeypatch):
    out = FakeStream()
    monkeypatch.setattr(_console.sys, "stdout", out)
    monkeypatch.setattr(_console.sys, "stderr", FakeStream())

    _console.setup_console()
    _console.setup_console()

    assert len(out.calls) == 1


def test_survives_stream_without_reconfigure(monkeypatch):
    monkeypatch.setattr(_console.sys, "stdout", NoReconfigureStream())
    monkeypatch.setattr(_console.sys, "stderr", NoReconfigureStream())

    _console.setup_console()  # не должно бросить


def test_survives_closed_stream(monkeypatch):
    class Closed:
        def reconfigure(self, **_kw):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(_console.sys, "stdout", Closed())
    monkeypatch.setattr(_console.sys, "stderr", Closed())

    _console.setup_console()  # не должно бросить


def test_stderr_fixed_even_if_stdout_broken(monkeypatch):
    """Сломанный stdout не должен оставить stderr в старой кодировке."""
    class Broken:
        def reconfigure(self, **_kw):
            raise AttributeError("нет reconfigure")

    err = FakeStream()
    monkeypatch.setattr(_console.sys, "stdout", Broken())
    monkeypatch.setattr(_console.sys, "stderr", err)

    _console.setup_console()

    assert err.calls == [{"encoding": "utf-8"}]


# ---------- подключение во всех точках входа ----------

def _entry_points() -> list[Path]:
    """Скрипты с `if __name__ == "__main__"` — их запускает пользователь напрямую."""
    return sorted(p for p in SCRIPTS_DIR.glob("*.py")
                  if "__main__" in p.read_text(encoding="utf-8"))


def test_entry_points_found():
    """Страховка от тихого «0 файлов» в тестах ниже."""
    assert len(_entry_points()) >= 16


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_entry_point_calls_setup_console(path):
    """main() каждого CLI-скрипта зовёт setup_console() до разбора аргументов.

    До parse_args — иначе падает вывод `--help`, который argparse печатает сам.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main is not None, f"{path.name}: нет функции main()"

    def is_setup_call(node):
        return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "setup_console")

    first = main.body[0]
    assert is_setup_call(first), (
        f"{path.name}: первой строкой main() должен быть setup_console(), "
        f"иначе кириллица в выводе и в --help уронит скрипт на консоли Windows"
    )


def test_install_py_fixes_encoding_without_importing_skill():
    """install.py работает до распаковки скилла — импортировать scripts._console нельзя."""
    src = (REPO_ROOT / "install.py").read_text(encoding="utf-8")
    assert "reconfigure(encoding=\"utf-8\")" in src
    assert "from scripts._console" not in src
    # reconfigure должен стоять до первого print, иначе смысла нет
    assert src.index("reconfigure") < src.index("print(")


def test_no_stray_reconfigure_left_in_scripts():
    """В scripts/ настройка кодировки живёт только в _console.py — дублей быть не должно."""
    offenders = [p.name for p in SCRIPTS_DIR.rglob("*.py")
                 if p.name != "_console.py"
                 and re.search(r"\.reconfigure\(\s*encoding", p.read_text(encoding="utf-8"))]
    assert offenders == []
