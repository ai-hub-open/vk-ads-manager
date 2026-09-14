#!/usr/bin/env python3
"""
install.py — установка зависимостей скилла vk-ads-manager.

Использование:
    python install.py

Что делает:
1. Проверяет версию Python (нужен 3.9+)
2. Ставит зависимости из requirements.txt
3. Создаёт ~/.vk-ads-manager/ (для credentials)
4. Делает быстрый smoke-test: импортируются ли модули скрипта
5. Печатает «всё ок» или конкретную проблему

Безопасно запускать несколько раз — pip install идемпотентен.
"""

import os
import subprocess
import sys
from pathlib import Path

# UTF-8 на консоль: в Windows она по умолчанию cp1251/cp866, и вывод ниже падает
# UnicodeEncodeError. Здесь повторяем логику scripts/_console.py, а не импортируем
# её: install.py должен работать до того, как скилл вообще распакован и настроен.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def fail(msg: str, exit_code: int = 1):
    print(f"\n❌ ERROR: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def ok(msg: str):
    print(f"✓ {msg}")


def info(msg: str):
    print(f"  {msg}")


def check_python_version():
    if sys.version_info < (3, 9):
        fail(f"Нужен Python 3.9 или новее. У вас: {sys.version}")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def install_requirements():
    skill_dir = Path(__file__).parent
    req = skill_dir / "requirements.txt"
    if not req.exists():
        fail(f"Не найден {req}")

    info(f"Устанавливаем из {req}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        fail("pip install не прошёл. Посмотрите ошибку выше.")
    ok("Зависимости установлены")


def create_credentials_dir():
    base = Path.home() / ".vk-ads-manager"
    base.mkdir(parents=True, exist_ok=True)
    creds = base / "credentials.json"
    if not creds.exists():
        creds.write_text("{}", encoding="utf-8")
        # Права 600 на Unix
        if os.name == "posix":
            import stat
            os.chmod(creds, stat.S_IRUSR | stat.S_IWUSR)
    ok(f"Папка credentials: {base}")
    if creds.exists():
        info(f"credentials.json существует ({creds})")


def smoke_test():
    """Проверяем что главные модули скилла импортируются."""
    skill_dir = Path(__file__).parent
    sys.path.insert(0, str(skill_dir))

    failed = []

    # 1. credentials
    try:
        from scripts.credentials import SERVICE_REGISTRY, load_api_key  # noqa
        ok(f"scripts.credentials — {len(SERVICE_REGISTRY)} сервисов в реестре")
    except Exception as e:
        failed.append(f"scripts.credentials: {e}")

    # 2. prompt_templates
    try:
        from scripts.prompt_templates import build_prompt, detect_creative_type  # noqa
        ok("scripts.prompt_templates")
    except Exception as e:
        failed.append(f"scripts.prompt_templates: {e}")

    # 3. openai SDK
    try:
        import openai  # noqa
        ok(f"openai SDK {openai.__version__}")
    except Exception as e:
        failed.append(f"openai: {e}")

    # 4. Pillow
    try:
        import PIL  # noqa
        ok(f"Pillow {PIL.__version__}")
    except Exception as e:
        failed.append(f"PIL: {e}")

    # 5. requests
    try:
        import requests  # noqa
        ok(f"requests {requests.__version__}")
    except Exception as e:
        failed.append(f"requests: {e}")

    # 6. openpyxl
    try:
        import openpyxl  # noqa
        ok(f"openpyxl {openpyxl.__version__}")
    except Exception as e:
        failed.append(f"openpyxl: {e}")

    # 7. python-docx
    try:
        import docx  # noqa
        ok("python-docx")
    except Exception as e:
        failed.append(f"python-docx: {e}")

    # 8. reportlab (для M3 PDF)
    try:
        import reportlab  # noqa
        ok(f"reportlab {reportlab.Version}")
    except Exception as e:
        failed.append(f"reportlab: {e}")

    # 9. FFmpeg (опционально — нужен только для склейки длинных видео)
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        ok(f"ffmpeg: {ffmpeg} (доступна склейка длинных видео и chain-segments)")
    else:
        info("ffmpeg не установлен (опционально). Без него — только 5-секундные видео без склейки.")
        info("  Windows: https://www.gyan.dev/ffmpeg/builds/")
        info("  macOS:   brew install ffmpeg")
        info("  Linux:   sudo apt install ffmpeg")

    if failed:
        print("\n⚠️  Некоторые модули не загрузились:", file=sys.stderr)
        for f in failed:
            print(f"  - {f}", file=sys.stderr)
        fail("Smoke-test не прошёл. Попробуйте: pip install --upgrade -r requirements.txt")


def main():
    print("=" * 60)
    print("vk-ads-manager — установка")
    print("=" * 60)
    print()

    check_python_version()
    install_requirements()
    create_credentials_dir()
    print()
    print("Smoke-test:")
    smoke_test()

    print()
    print("=" * 60)
    print("✅ Установка завершена.")
    print("=" * 60)
    print()
    print("Что дальше:")
    print("1. Откройте Cowork (Claude Desktop)")
    print("2. Скажите: «Хочу запустить VK Рекламу на ...»")
    print("3. Claude поведёт вас по воронке. Когда понадобится ключ OpenAI / VK Ads")
    print("   — просто скиньте его в чат, Claude сохранит безопасно.")
    print()
    print("Подробности: README.md в этой папке.")


if __name__ == "__main__":
    main()
