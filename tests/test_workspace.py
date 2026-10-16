"""Рабочая папка кампании и проектные конфиги MCP — всегда вне папки скилла.

Скилл, установленный плагином Claude Code, при обновлении заменяется целиком.
Скрипты запускают `python -m scripts.<имя>` из папки скилла, поэтому относительный
`--workspace` раньше ложился внутрь плагина — вместе с данными клиента.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import package_skill, setup_vk_ads_mcp
from scripts._workspace import SKILL_ROOT, is_inside_skill, resolve_workspace

SCRIPTS_DIR = SKILL_ROOT / "scripts"


# ---------- сам хелпер ----------

def test_skill_root_is_repo_root():
    assert (SKILL_ROOT / "SKILL.md").is_file()


def test_outside_path_returned_absolute(tmp_path):
    ws = tmp_path / "vk-campaign-acme"
    assert resolve_workspace(ws) == ws.resolve()


def test_relative_path_resolved_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = resolve_workspace("vk-campaign-acme")
    assert got.is_absolute()
    assert got == (tmp_path / "vk-campaign-acme").resolve()


def test_relative_path_from_skill_dir_rejected(monkeypatch):
    """Ровно тот случай, ради которого всё затевалось: `python -m` из папки скилла."""
    monkeypatch.chdir(SKILL_ROOT)
    with pytest.raises(SystemExit) as exc:
        resolve_workspace("vk-campaign-acme")
    assert "внутри папки скилла" in str(exc.value.code)


def test_skill_root_itself_rejected():
    with pytest.raises(SystemExit):
        resolve_workspace(SKILL_ROOT)


def test_dotdot_escape_is_resolved(tmp_path):
    """`<скилл>/../x` — снаружи, `<скилл>/scripts/..` — внутри."""
    assert not is_inside_skill(SKILL_ROOT / ".." / "vk-campaign-acme")
    assert is_inside_skill(SKILL_ROOT / "scripts" / "..")


def test_sibling_with_common_prefix_is_outside():
    """`vk-ads-manager-data` рядом со скиллом — не внутри него (сравнение по частям пути, не по строке)."""
    sibling = SKILL_ROOT.parent / (SKILL_ROOT.name + "-data")
    assert not is_inside_skill(sibling)


def test_custom_label_in_message(monkeypatch):
    monkeypatch.chdir(SKILL_ROOT)
    with pytest.raises(SystemExit) as exc:
        resolve_workspace("out.mp4", what="Файл результата")
    assert str(exc.value.code).startswith("Файл результата")


# ---------- подключение во всех скриптах ----------

def _workspace_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.py")
                  if '"--workspace"' in p.read_text(encoding="utf-8"))


def test_workspace_scripts_found():
    """Страховка от тихого «0 файлов» в тесте ниже."""
    assert len(_workspace_scripts()) >= 11


@pytest.mark.parametrize("path", _workspace_scripts(), ids=lambda p: p.name)
def test_workspace_goes_through_guard(path):
    """Рабочая папка открывается через resolve_workspace(), а не голым Path(args.workspace)."""
    src = path.read_text(encoding="utf-8")
    assert "Path(args.workspace)" not in src, f"{path.name}: рабочая папка в обход resolve_workspace()"
    assert "resolve_workspace(args.workspace" in src, f"{path.name}: нет resolve_workspace(args.workspace…)"


def test_video_concat_output_guarded():
    src = (SCRIPTS_DIR / "video_concat.py").read_text(encoding="utf-8")
    assert "Path(args.output)" not in src
    assert src.count("resolve_workspace(args.output") == 3


# ---------- установщик MCP ----------

def test_setup_project_target_refused_inside_skill(monkeypatch):
    """`.mcp.json` с токеном не должен лечь в папку скилла."""
    monkeypatch.chdir(SKILL_ROOT)
    target = SKILL_ROOT / ".mcp.json"
    existed = target.exists()
    try:
        rc = setup_vk_ads_mcp.main(
            ["--token", "tok-123", "--vk-account-id", "42", "--target", "claude-code"]
        )
        assert rc == 2
        assert target.exists() == existed
    finally:
        if target.exists() and not existed:
            target.unlink()


def test_setup_all_skips_project_target_inside_skill(monkeypatch, capsys):
    """Документированный `--target all` из папки скилла работает, но без `.mcp.json` в скилле."""
    monkeypatch.chdir(SKILL_ROOT)
    rc = setup_vk_ads_mcp.main(
        ["--token", "tok-123", "--vk-account-id", "42", "--target", "all", "--dry-run"]
    )
    assert rc == 0
    assert "claude-code пропущен" in capsys.readouterr().out


def test_setup_all_keeps_project_target_outside_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = setup_vk_ads_mcp.main(
        ["--token", "tok-123", "--vk-account-id", "42", "--target", "all", "--dry-run"]
    )
    assert rc == 0
    assert "claude-code пропущен" not in capsys.readouterr().out


def test_setup_user_target_allowed_inside_skill(monkeypatch):
    """Глобальный конфиг Cursor пишется в HOME — из папки скилла запускать можно."""
    monkeypatch.chdir(SKILL_ROOT)
    rc = setup_vk_ads_mcp.main(
        ["--token", "tok-123", "--vk-account-id", "42", "--target", "cursor", "--dry-run"]
    )
    assert rc == 0


# ---------- секреты не попадают в архив ----------

@pytest.mark.parametrize("rel", [
    "vk-ads-manager/.mcp.json",
    "vk-ads-manager/.env",
    "vk-ads-manager/scripts/.env",
    "vk-ads-manager/.cursor/mcp.json",
])
def test_secret_files_excluded_from_package(rel):
    assert package_skill.should_exclude(Path(rel))
