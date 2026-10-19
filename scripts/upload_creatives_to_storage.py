#!/usr/bin/env python3
"""
upload_creatives_to_storage.py — публикация локальных креативов во временном
файловом хранилище KeepImage (aihub) и получение публичных ссылок для заливки
картинок в VK Рекламу через `vk_ads_content_upload_image(source_path_or_url=...)`.

Зачем это нужно
---------------
Хостовый MCP VK Ads (aihub.click.ru) не читает локальные файлы — `vk_ads_content_upload_image`
и `vk_ads_content_upload_video` принимают только публичный http(s)-URL (чтение с диска
сервера выключено, приватные сети режутся SSRF-защитой). KeepImage превращает локальный
файл из `assets/images/` в такую ссылку: PUT файла → публичный `/f/<id>/<имя>` без
авторизации, живёт до 2 часов. Ссылка нужна живой только на момент вызова загрузки:
VK скачивает картинку в свой контент и возвращает `id`, дальше ссылка не нужна.

Только картинки. KeepImage принимает PNG/JPEG/GIF/WebP до 20 МБ. Видео он не хостит —
видеокреативы заливай публичной ссылкой или через путь B (`scripts/deploy_campaign.py`
работает с локальными файлами напрямую).

Почему скрипт, а не MCP `storage_publish_image(data_base64)`
-----------------------------------------------------------
Креатив бывает в несколько МБ. Через MCP `data_base64` весь файл пришлось бы гнать
строкой в вызове модели — картинка 1080×1080 это сотни тысяч символов, на практике модель
так не передаёт. Скрипт постит байты напрямую по HTTP. `data_base64` годится разве что
для крошечного файла (≤ ~50 КБ, логотип 256×256). Нет запуска кода или токена — путь B
(`deploy_campaign.py` читает локальные файлы), путь C или прямые публичные ссылки.

Авторизация
-----------
Токен click.ru — тот же, что у хостового MCP VK (сервис `clickru` в реестре ключей или
env `CLICK_RU_TOKEN`). Мастер-аккаунту click.ru нужен ещё `X-Auth-UserId`
(сервис `clickru_user_id` / env `CLICK_RU_USER_ID`). Токен НИКОГДА не пишется в артефакты
рабочей папки.

Что делает
----------
1. Собирает локальные картинки: по умолчанию — всё из `assets/images/*.{png,jpg,jpeg,gif,webp}`
   (туда пишет модуль M1 и оттуда читает `deploy_campaign.py`); либо `--files a.png b.jpg`.
2. Приводит картинку к точному размеру роли (`_image_prep.prepare_image`): квадрат больше
   600 → 600×600 (роль `image_600x600` отклоняет 1080), 16:9 → 1080×607, 4:5 → 1080×1350.
   Исходник не меняется — в хранилище уходит копия из памяти. Соотношение не подходит ни
   под одну роль или картинка меньше нужного — предупреждение в манифесте, не обрезаем.
   Логотип (`--logo`, иначе `assets/brand/logo.*` или `assets/images/logo.*`) выкладывается
   отдельно, ужимается до 256×256 (роль `icon_256x256`) и помечается в манифесте
   `"kind": "logo"` — его ссылку передавай в `vk_ads_content_upload_image`, а `id` —
   в `content.icon_256x256`.
3. Каждый файл заливает в KeepImage (PUT /v1/objects/<имя>, сырое тело) → публичная ссылка.
4. Пишет манифест `assets/storage_manifest.json`: локальный путь → ссылка (+ id, срок, размер).
   Оркестратор читает его на Шаге 11 (путь A) и передаёт `url` в `vk_ads_content_upload_image`.

Публичные ссылки в манифесте временные (TTL ≤ 2 ч) — это не артефакт для git, а рабочий
проброс на время заливки.

Usage
-----
    python -m scripts.upload_creatives_to_storage --check
    python -m scripts.upload_creatives_to_storage --workspace <dir> --dry-run
    python -m scripts.upload_creatives_to_storage --workspace <dir>
    python -m scripts.upload_creatives_to_storage --files a.png b.png --logo brand/logo.png
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console
    from _workspace import resolve_workspace

try:
    from scripts.credentials import CredentialNotFound, load_api_key
    from scripts._image_prep import fit_square, prepare_image, LOGO_SIDE
except ImportError:  # запуск напрямую, не как модуль пакета
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import CredentialNotFound, load_api_key
    from scripts._image_prep import fit_square, prepare_image, LOGO_SIDE

DEFAULT_BASE_URL = "https://storage.aihub.click.ru"
DEFAULT_TTL = 7200  # максимум хранилища
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
REQUEST_TIMEOUT = 120


def base_url() -> str:
    """Боевой домен KeepImage; можно переопределить env KEEPIMAGE_BASE_URL."""
    import os

    return os.environ.get("KEEPIMAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _token() -> str:
    """Токен click.ru: env CLICK_RU_TOKEN → реестр ключей (сервис clickru)."""
    return load_api_key("clickru")


def _user_id() -> str | None:
    """ID пользователя click.ru для мастер-аккаунта (опционально)."""
    try:
        return load_api_key("clickru_user_id")
    except CredentialNotFound:
        return None


def _headers(token: str, content_type: str | None, ttl: int) -> dict:
    h = {"X-Auth-Token": token, "X-Ttl-Seconds": str(ttl)}
    if content_type:
        h["Content-Type"] = content_type
    uid = _user_id()
    if uid:
        h["X-Auth-UserId"] = uid
    return h


def collect_images(ws: Path, explicit: list[str] | None) -> list[Path]:
    """Возвращает список локальных картинок к заливке (уникальные)."""
    if explicit:
        paths = [Path(f) if Path(f).is_absolute() else ws / f for f in explicit]
    else:
        img_dir = ws / "assets" / "images"
        paths = sorted(p for p in img_dir.glob("*.*") if p.suffix.lower() in ALLOWED_SUFFIXES) if img_dir.exists() else []

    seen: dict[str, Path] = {}
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen[key] = p
    return list(seen.values())


LOGO_CANDIDATES = ("assets/brand/logo.png", "assets/brand/logo.jpg", "assets/brand/logo.jpeg",
                   "assets/brand/logo.webp", "assets/images/logo.png", "assets/images/logo.jpg",
                   "assets/images/logo.jpeg", "assets/images/logo.webp")


def find_logo(ws: Path | None, explicit: str | None) -> Path | None:
    """Логотип: --logo → assets/brand/logo.* → assets/images/logo.*. Нет — None."""
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() or ws is None else ws / p
    if ws is None:
        return None
    for rel in LOGO_CANDIDATES:
        if (ws / rel).exists():
            return ws / rel
    return None


def _prepare(path: Path, kind: str) -> tuple[bytes, dict, dict]:
    """Байты к заливке + сведения для манифеста. Исходник на диске не трогаем."""
    extra: dict = {"kind": kind}
    if kind == "logo":
        blob, prep = fit_square(path.read_bytes(), side=LOGO_SIDE)
        extra["role"] = "icon_256x256"
        if prep["width"] and (prep["width"], prep["height"]) != (LOGO_SIDE, LOGO_SIDE):
            extra["warning"] = (f"логотип {prep['width']}×{prep['height']} — роль icon_256x256 ждёт "
                                f"квадрат 256×256 (меньше не растягиваем, не квадрат — не обрезаем)")
    else:
        blob, prep = prepare_image(path.read_bytes())
        if prep["role"]:
            extra["role"] = prep["role"]
        if prep["width"] and not prep["role"]:
            extra["warning"] = (f"{prep['width']}×{prep['height']} не подходит ни под одну роль "
                                "(квадрат 600×600, 16:9 1080×607, 4:5 1080×1350) — не обрезаем молча")
        elif prep["too_small"]:
            extra["warning"] = (f"{prep['width']}×{prep['height']} меньше размера роли {prep['role']} — "
                                "VK не примет, нужен исходник крупнее")
    if prep["resized"]:
        extra["resized_from"] = prep["original"]
    if prep["width"]:
        extra["width"], extra["height"] = prep["width"], prep["height"]
    return blob, prep, extra


def upload_one(path: Path, token: str, ttl: int, kind: str = "image") -> dict:
    """Заливает один файл в KeepImage. Возвращает запись манифеста.

    kind="image" — картинка объявления (приводится к размеру роли), "logo" — логотип 256×256.
    """
    path = Path(path)
    rec: dict = {"local": str(path)}
    if not path.exists():
        return {**rec, "kind": kind, "status": "fail", "error": "файл не найден"}
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return {**rec, "kind": kind, "status": "fail", "error": f"формат {path.suffix} не поддерживается (PNG/JPEG/GIF/WebP; видео — путь B)"}

    blob, prep, extra = _prepare(path, kind)
    rec.update(extra)

    content_type = prep["mime"] or mimetypes.guess_type(path.name)[0]
    url = f"{base_url()}/v1/objects/{quote(path.name)}"
    try:
        resp = requests.put(url, data=blob, headers=_headers(token, content_type, ttl), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        return {**rec, "status": "fail", "error": f"сеть: {e}"}

    if resp.status_code >= 400:
        detail = resp.text[:300] if resp.text else ""
        return {**rec, "status": "fail", "error": f"HTTP {resp.status_code}: {detail}"}

    try:
        body = resp.json()
    except ValueError:
        return {**rec, "status": "fail", "error": "ответ не JSON"}

    if not body.get("url"):
        return {**rec, "status": "fail", "error": f"нет url в ответе: {body}"}

    return {
        **rec,
        "status": "ok",
        "url": body["url"],
        "id": body.get("id"),
        "content_type": body.get("content_type"),
        "size": body.get("size"),
        "expires_at": body.get("expires_at"),
    }


def run_check() -> int:
    """Проверяет наличие токена и доступность хранилища."""
    try:
        _token()
    except CredentialNotFound as e:
        print(f"✗ токен click.ru не найден.\n{e}", file=sys.stderr)
        return 1
    try:
        resp = requests.get(f"{base_url()}/healthz", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"✗ хранилище недоступно: {e}", file=sys.stderr)
        return 1
    ok = resp.status_code < 400
    print(f"{'✓' if ok else '✗'} KeepImage {base_url()} → HTTP {resp.status_code}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Заливка локальных креативов в KeepImage → публичные ссылки для vk_ads_content_upload_image")
    parser.add_argument("--workspace", help="Рабочая папка кампании (где лежит assets/images/)")
    parser.add_argument("--files", nargs="+", help="Явный список файлов (перекрывает --workspace как источник)")
    parser.add_argument("--logo", help="Файл логотипа (по умолчанию assets/brand/logo.* или assets/images/logo.*)")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL, help=f"Срок жизни ссылок, сек (по умолч. {DEFAULT_TTL}, максимум хранилища)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать план, без сети и без записи манифеста")
    parser.add_argument("--check", action="store_true", help="Проверить токен и доступность хранилища")
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    if not args.workspace and not args.files:
        print("ERROR: нужен --workspace или --files.", file=sys.stderr)
        return 2

    ws = resolve_workspace(args.workspace or Path.cwd())
    images = collect_images(ws, args.files)
    logo = find_logo(ws if (args.workspace or args.logo) else None, args.logo)
    if logo is not None:
        # логотип не ужимаем как квадрат 600 — он идёт отдельно, со стороной 256
        images = [p for p in images if p.resolve() != logo.resolve()]
    jobs = [(p, "image") for p in images] + ([(logo, "logo")] if logo is not None else [])

    if not jobs:
        print("Картинок для заливки не найдено (проверь assets/images/ или --files).")
        return 0

    if args.dry_run:
        print(f"[dry-run] залил бы {len(jobs)} файл(ов) в {base_url()} (TTL {args.ttl} c):")
        for p, kind in jobs:
            mark = "" if p.exists() else "  ⚠️ НЕ НАЙДЕН"
            if p.exists():
                _, _, extra = _prepare(p, kind)
                if extra.get("resized_from"):
                    mark = f"  ({extra['resized_from']} → ужму до {extra['width']}x{extra['height']})"
                if extra.get("warning"):
                    mark += f"  ⚠️ {extra['warning']}"
            label = " [логотип]" if kind == "logo" else ""
            print(f"  {p}{label}{mark}")
        if logo is None:
            print("  ⚠️ логотип не найден (assets/brand/logo.png или --logo) — он нужен для icon_256x256")
        return 0

    try:
        token = _token()
    except CredentialNotFound as e:
        print(f"ERROR: токен click.ru не найден.\n{e}", file=sys.stderr)
        print("Сохрани: python -m scripts.manage_credentials set clickru", file=sys.stderr)
        return 1

    manifest = [upload_one(p, token, args.ttl, kind=kind) for p, kind in jobs]

    out = ws / "assets" / "storage_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    fails = [r for r in manifest if r["status"] != "ok"]
    oks = [r for r in manifest if r["status"] == "ok"]
    print(f"Залито: {len(oks)}/{len(manifest)}. Манифест: {out}")
    for r in oks:
        note = (f" (ужато {r['resized_from']} → {r['width']}x{r['height']} для роли {r.get('role')})"
                if r.get("resized_from") else "")
        label = " [логотип → icon_256x256]" if r.get("kind") == "logo" else ""
        print(f"  ✓ {Path(r['local']).name}{label} → {r['url']}{note}")
        if r.get("warning"):
            print(f"    ⚠️ {r['warning']}")
    if logo is None:
        print("  ⚠️ логотип не найден (assets/brand/logo.png или --logo) — он нужен для icon_256x256")
    for r in fails:
        print(f"  ✗ {Path(r['local']).name}: {r['error']}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
