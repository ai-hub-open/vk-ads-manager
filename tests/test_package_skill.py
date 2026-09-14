"""Упаковщик `.skill`: состав пакета и проверка имён артефактов воронки.

Проверка имён добавлена в 0.6.6 после того, как в SKILL.md нашлись три
устаревших имени (`04_competitors_deep.md`, `09_landings.md`,
`vk_ads_pixel_check.html`). Агент писал пометку о пропуске шага в файл, который
генераторы документов не читают, — шаг молча исчезал из медиаплана и отчёта.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts import package_skill as ps

REPO_ROOT = Path(__file__).resolve().parent.parent

FRONTMATTER = "---\nname: vk-ads-manager\ndescription: тестовый скилл\n---\n\n"


def _make_skill(root: Path, *, skill_body: str = "", readers: dict | None = None) -> Path:
    """Минимальная папка скилла: SKILL.md, README.md и генераторы документов."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(FRONTMATTER + skill_body, encoding="utf-8")
    (root / "README.md").write_text("readme", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ps.ARTIFACT_READERS:
        (scripts / name).write_text((readers or {}).get(name, ""), encoding="utf-8")
    return root


# ---------- check_artifact_names ----------

def test_matching_names_pass(tmp_path):
    root = _make_skill(
        tmp_path / "skill",
        skill_body="**Артефакт:** `09_landing.md`",
        readers={"generate_media_plan.py": '("09_landing.md", "9. Посадочные")'},
    )
    assert ps.check_artifact_names(root, (root / "SKILL.md").read_text(encoding="utf-8")) == set()


def test_typo_is_caught(tmp_path):
    """Ровно тот дефект, ради которого проверка написана."""
    root = _make_skill(
        tmp_path / "skill",
        skill_body="Пометка в `09_landings.md`: пропущено",
        readers={"generate_media_plan.py": '("09_landing.md", "9. Посадочные")'},
    )
    drift = ps.check_artifact_names(root, (root / "SKILL.md").read_text(encoding="utf-8"))
    assert drift == {"09_landings.md"}


def test_lifecycle_artifact_allowed(tmp_path):
    """11_optimization.md появляется после запуска и в медиаплан не идёт."""
    root = _make_skill(tmp_path / "skill", skill_body="**Артефакт:** `11_optimization.md`")
    assert ps.check_artifact_names(root, (root / "SKILL.md").read_text(encoding="utf-8")) == set()


def test_name_in_second_reader_counts(tmp_path):
    """Достаточно, чтобы имя читал хотя бы один генератор."""
    root = _make_skill(
        tmp_path / "skill",
        skill_body="`04_competitor_analysis.md`",
        readers={"generate_strategy_report.py": '"04_competitor_analysis.md": "_competitors.md"'},
    )
    assert ps.check_artifact_names(root, (root / "SKILL.md").read_text(encoding="utf-8")) == set()


def test_missing_reader_file_does_not_crash(tmp_path):
    """Генератора нет на диске — проверка не падает, а честно сообщает о расхождении."""
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(FRONTMATTER + "`09_landing.md`", encoding="utf-8")
    assert ps.check_artifact_names(root, (root / "SKILL.md").read_text(encoding="utf-8")) == {"09_landing.md"}


def test_validation_fails_on_drift(tmp_path):
    root = _make_skill(
        tmp_path / "skill",
        skill_body="`09_landings.md`",
        readers={"generate_media_plan.py": '"09_landing.md"'},
    )
    valid, msg = ps.validate_skill(root)
    assert valid is False
    assert "09_landings.md" in msg


def test_packaging_aborts_on_drift(tmp_path, capsys):
    root = _make_skill(
        tmp_path / "skill",
        skill_body="`09_landings.md`",
        readers={"generate_media_plan.py": '"09_landing.md"'},
    )
    out_dir = tmp_path / "out"
    assert ps.package_skill(root, output_dir=out_dir) is None
    assert "Валидация не прошла" in capsys.readouterr().err
    assert not list(out_dir.glob("*.skill")) if out_dir.exists() else True


# ---------- состав пакета ----------

def test_build_tools_excluded_license_kept(tmp_path):
    root = _make_skill(tmp_path / "skill")
    for name in ("package.sh", "package.bat", "CHANGELOG.md", ".gitignore", "LICENSE"):
        (root / name).write_text("x", encoding="utf-8")

    result = ps.package_skill(root, output_dir=tmp_path / "out")

    assert result is not None
    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(result).namelist()}
    assert "LICENSE" in names, "Apache-2.0 требует распространять текст лицензии"
    for excluded in ("package.sh", "package.bat", "CHANGELOG.md", ".gitignore"):
        assert excluded not in names


def test_work_dirs_and_caches_excluded(tmp_path):
    root = _make_skill(tmp_path / "skill")
    (root / "scripts" / "__pycache__").mkdir()
    (root / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"x")
    (root / ".pytest_cache" / "v").mkdir(parents=True)
    (root / ".pytest_cache" / "v" / "lastfailed").write_text("{}", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "pic.png").write_bytes(b"x")
    (root / "vk-campaign-acme").mkdir()
    (root / "vk-campaign-acme" / "01_brief.md").write_text("бриф", encoding="utf-8")

    result = ps.package_skill(root, output_dir=tmp_path / "out")

    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(result).namelist()}
    assert not any(n.startswith(("assets/", "vk-campaign-")) for n in names)
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert not any(n.startswith(".pytest_cache") for n in names)


@pytest.mark.parametrize("missing, expected", [
    ("SKILL.md", "Не найден SKILL.md"),
    (None, None),
])
def test_validation_requires_skill_md(tmp_path, missing, expected):
    root = _make_skill(tmp_path / "skill")
    if missing:
        (root / missing).unlink()
    valid, msg = ps.validate_skill(root)
    assert valid is (expected is None)
    if expected:
        assert expected in msg


def test_validation_requires_frontmatter_fields(tmp_path):
    root = _make_skill(tmp_path / "skill")
    (root / "SKILL.md").write_text("---\ndescription: без имени\n---\n", encoding="utf-8")
    valid, msg = ps.validate_skill(root)
    assert valid is False
    assert "name:" in msg


# ---------- настоящий репозиторий ----------

def test_real_repo_passes_validation():
    """Рабочая копия скилла должна упаковываться без правок."""
    valid, msg = ps.validate_skill(REPO_ROOT)
    assert valid, msg


def test_dev_harness_excluded_from_package(tmp_path):
    """Тесты и их конфиги — инструмент разработчика, в .skill им делать нечего."""
    root = _make_skill(tmp_path / "skill")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x(): pass", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("pytest>=8.0", encoding="utf-8")
    (root / "evals").mkdir()
    (root / "evals" / "prompt.md").write_text("промпт", encoding="utf-8")

    result = ps.package_skill(root, output_dir=tmp_path / "out")

    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(result).namelist()}
    assert not any(n.startswith(("tests/", "evals/")) for n in names)
    assert "pytest.ini" not in names
    assert "requirements-dev.txt" not in names
    # рантайм-зависимости остаться должны
    assert "SKILL.md" in names


def test_real_repo_package_has_no_dev_files(tmp_path):
    """Контрольная сборка настоящего репозитория: харнес наружу не уезжает."""
    result = ps.package_skill(REPO_ROOT, output_dir=tmp_path / "out")
    assert result is not None
    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(result).namelist()}
    leaked = [n for n in names
              if n.startswith(("tests/", "evals/", "assets/", ".pytest_cache"))
              or n in ("pytest.ini", "requirements-dev.txt", "package.sh",
                       "package.bat", "CHANGELOG.md", ".gitignore")]
    assert leaked == []
    assert {"SKILL.md", "README.md", "LICENSE", "install.py", "requirements.txt"} <= names
