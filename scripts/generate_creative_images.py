#!/usr/bin/env python3
"""
generate_creative_images.py — генерирует картинки визуала для статичных креативов
через OpenAI Images API (gpt-image-1 или dall-e-3).

Читает creatives.json + brand.json (опционально), для каждого подходящего
креатива собирает промпт через prompt_templates.py, вызывает OpenAI,
постпроцессит размер через PIL, сохраняет в assets/images/.

Usage:
    # Dry-run: только промпты, без вызова API
    python -m scripts.generate_creative_images --workspace <path> --dry-run

    # Реальная генерация
    OPENAI_API_KEY=sk-... python -m scripts.generate_creative_images --workspace <path>

    # Только конкретный креатив
    python -m scripts.generate_creative_images --workspace <path> --only cr_pain_p2_static

    # Сменить модель
    python -m scripts.generate_creative_images --workspace <path> --model dall-e-3

Ключ OpenAI:
    Скрипт читает ключ через scripts.credentials.load_api_key("openai").
    Стратегия поиска:
    1. env OPENAI_API_KEY
    2. ~/.vk-ads-manager/credentials.json
    3. Если не найден — ошибка с инструкцией как сохранить через manage_credentials.py.

    Чтобы сохранить ключ один раз для всех проектов:
        python -m scripts.manage_credentials set openai
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

try:
    from scripts._console import setup_console
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console

# Импортируем шаблоны и credentials
try:
    from scripts.prompt_templates import (
        build_prompt,
        detect_creative_type,
        get_sizes,
    )
    from scripts.credentials import load_api_key, CredentialNotFound
except ImportError:
    # При запуске напрямую (не через -m)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.prompt_templates import (
        build_prompt,
        detect_creative_type,
        get_sizes,
    )
    from scripts.credentials import load_api_key, CredentialNotFound


def load_brand_config(workspace: Path) -> dict:
    """
    Читает brand.json в рабочей папке, если нет — fallback на scripts/brand_defaults.json.
    """
    local = workspace / "brand.json"
    if local.exists():
        with local.open(encoding="utf-8") as f:
            return json.load(f)

    defaults_path = Path(__file__).parent / "brand_defaults.json"
    if defaults_path.exists():
        with defaults_path.open(encoding="utf-8") as f:
            data = json.load(f)
            return {k: v for k, v in data.items() if not k.startswith("_")}

    return {"product_name": "Product", "primary_color": "#2D5BFF", "accent_color": "#22C55E"}


def load_creatives(workspace: Path) -> list:
    """Читает creatives.json. Поддерживает оба формата: [...] и {"creatives": [...]}."""
    path = workspace / "creatives.json"
    if not path.exists():
        raise FileNotFoundError(f"Не найден {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("creatives", [])
    return data


def save_prompt(prompt: str, path: Path, creative: dict, ctype: str):
    """Сохраняет промпт + метаданные креатива в текстовый файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Promптт для креатива {creative.get('name')}\n"
        f"# Тип: {ctype}\n"
        f"# Целевая аудитория: {creative.get('ad_plan_id')}\n"
        f"# Целевой URL: {creative.get('url', '—')}\n"
        f"# Размер: см. get_sizes('{ctype}')\n"
        f"\n"
        f"---\n\n"
        f"{prompt}\n"
    )
    path.write_text(body, encoding="utf-8")


def generate_via_openai(prompt: str, size: str, model: str, output_path: Path, api_key: str = None) -> bool:
    """
    Вызывает OpenAI Images API. Возвращает True при успехе.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai SDK не установлен. pip install openai>=1.40", file=sys.stderr)
        return False

    # Если ключ передан явно — используем его, иначе OpenAI SDK сам прочитает env
    if api_key:
        client = OpenAI(api_key=api_key)
    else:
        client = OpenAI()

    try:
        if model == "gpt-image-1":
            response = client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=size,
                quality="high",
                n=1,
            )
            # gpt-image-1 возвращает base64 по умолчанию
            b64 = response.data[0].b64_json
            if b64:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(b64))
                return True
            # Иначе пробуем URL
            url = getattr(response.data[0], "url", None)
            if url:
                return _download(url, output_path)
        else:  # dall-e-3
            response = client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality="hd",
                n=1,
            )
            url = response.data[0].url
            return _download(url, output_path)
    except Exception as e:
        print(f"  ERROR OpenAI: {e}", file=sys.stderr)
        return False


def _download(url: str, output_path: Path) -> bool:
    """Скачивает картинку по URL и сохраняет."""
    try:
        import requests
    except ImportError:
        import urllib.request

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, output_path)
            return True
        except Exception as e:
            print(f"  ERROR urllib download: {e}", file=sys.stderr)
            return False
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"  ERROR requests download: {e}", file=sys.stderr)
        return False


def postprocess(image_path: Path, target_size: tuple):
    """
    Ресайз/crop картинки до целевого размера VK через PIL.
    Если PIL не установлен — пропускает (картинка остаётся в исходном размере).
    """
    try:
        from PIL import Image
    except ImportError:
        print(f"  WARN: PIL не установлен — картинка останется в исходном размере", file=sys.stderr)
        return

    img = Image.open(image_path)
    target_w, target_h = target_size

    # Ресайз с сохранением пропорций, потом центральный crop до target
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if abs(src_ratio - target_ratio) < 0.01:
        # Пропорции совпадают — простой ресайз
        img = img.resize(target_size, Image.LANCZOS)
    elif src_ratio > target_ratio:
        # Источник шире — ресайзим по высоте, crop по ширине
        new_h = target_h
        new_w = int(new_h * src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        img = img.crop((left, 0, left + target_w, target_h))
    else:
        # Источник выше — ресайзим по ширине, crop по высоте
        new_w = target_w
        new_h = int(new_w / src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        top = (new_h - target_h) // 2
        img = img.crop((0, top, target_w, top + target_h))

    img.save(image_path, "PNG", optimize=True)


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="Генератор картинок для VK Реклама креативов")
    parser.add_argument("--workspace", required=True, help="Папка vk-campaign-<slug>/")
    parser.add_argument(
        "--model",
        default="gpt-image-1",
        choices=["gpt-image-1", "dall-e-3"],
        help="OpenAI модель (gpt-image-1 рекомендуется для брендинга и текста)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Сгенерировать только указанный creative по name (например cr_pain_p2_static)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только промпты, без вызова API. Сохраняет промпты в assets/prompts/.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Пропускать креативы, для которых картинка уже есть",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перегенерировать даже если картинка уже есть",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    # Загружаем ключ OpenAI (только если не dry-run)
    openai_key = None
    if not args.dry_run:
        try:
            openai_key = load_api_key("openai")
            print(f"✓ OpenAI key загружен (из env или credentials.json)")
        except CredentialNotFound as e:
            print(str(e), file=sys.stderr)
            return 1

    brand = load_brand_config(workspace)
    creatives = load_creatives(workspace)

    if args.only:
        creatives = [c for c in creatives if c.get("name") == args.only]
        if not creatives:
            print(f"ERROR: креатив {args.only} не найден", file=sys.stderr)
            return 1

    print(f"Бренд: {brand.get('product_name')} (primary={brand.get('primary_color')})")
    print(f"Креативов всего: {len(creatives)}")
    print(f"Модель: {args.model}")
    print(f"Dry-run: {args.dry_run}\n")

    images_dir = workspace / "assets" / "images"
    prompts_dir = workspace / "assets" / "prompts"
    log_path = workspace / "assets" / "generation_log.json"
    log = []

    for creative in creatives:
        name = creative.get("name", "unnamed")
        ctype = detect_creative_type(creative)

        print(f"[{name}] type={ctype}")

        # Skip video creatives — обрабатываются модулем M2
        if ctype == "video_skip":
            print(f"  → SKIP (video creative, see M2)")
            log.append({"name": name, "type": ctype, "status": "skipped_video"})
            continue

        # Карусель — генерим N картинок (один промпт на каждую карточку)
        fmt = creative.get("format", "")
        is_carousel = "carousel" in fmt

        if is_carousel:
            # Парсим количество карточек из image_or_video или дефолт = 3
            image_desc = creative.get("image_or_video", "") or ""
            n_cards = 3
            import re

            m = re.search(r"(\d+)\s*card", image_desc.lower())
            if m:
                n_cards = int(m.group(1))

            for i in range(1, n_cards + 1):
                prompt = build_prompt(creative, brand, ctype, card_index=i, card_total=n_cards)
                openai_size, vk_size = get_sizes(ctype)
                image_path = images_dir / f"{name}_card{i}.png"
                prompt_path = prompts_dir / f"{name}_card{i}.txt"

                save_prompt(prompt, prompt_path, creative, ctype)

                if args.dry_run:
                    print(f"  card {i}/{n_cards}: prompt → {prompt_path.name}")
                    log.append(
                        {"name": f"{name}_card{i}", "type": ctype, "status": "dry_run", "prompt": prompt_path.name}
                    )
                    continue

                # UI mockup carousel — генерируем только placeholders, не вызываем API
                if ctype == "ui_mockup":
                    print(f"  card {i}/{n_cards}: ⚠ UI mockup placeholder — запросите реальный скриншот")
                    log.append({"name": f"{name}_card{i}", "type": ctype, "status": "needs_real_screenshot", "prompt": prompt_path.name})
                    continue

                if image_path.exists() and not args.force:
                    print(f"  card {i}/{n_cards}: уже есть, skip")
                    log.append({"name": f"{name}_card{i}", "type": ctype, "status": "exists"})
                    continue

                print(f"  card {i}/{n_cards}: генерим...")
                ok = generate_via_openai(prompt, openai_size, args.model, image_path, api_key=openai_key)
                if ok:
                    postprocess(image_path, vk_size)
                    print(f"    ✓ {image_path.name} ({vk_size[0]}×{vk_size[1]})")
                    log.append(
                        {"name": f"{name}_card{i}", "type": ctype, "status": "generated", "path": str(image_path)}
                    )
                else:
                    log.append({"name": f"{name}_card{i}", "type": ctype, "status": "failed"})
                # Лёгкая пауза чтобы не упереться в rate limit
                time.sleep(1.5)

        else:
            # Один креатив = одна картинка
            prompt = build_prompt(creative, brand, ctype)
            openai_size, vk_size = get_sizes(ctype)
            image_path = images_dir / f"{name}.png"
            prompt_path = prompts_dir / f"{name}.txt"

            save_prompt(prompt, prompt_path, creative, ctype)

            if args.dry_run:
                print(f"  prompt → {prompt_path.name}")
                log.append({"name": name, "type": ctype, "status": "dry_run", "prompt": prompt_path.name})
                continue

            if ctype == "ui_mockup":
                print(f"  ⚠ UI mockup — AI генерация ненадёжна. Промпт сохранён, но запросите реальный скриншот от клиента.")
                log.append({"name": name, "type": ctype, "status": "needs_real_screenshot", "prompt": prompt_path.name})
                continue

            if image_path.exists() and not args.force:
                print(f"  уже есть, skip")
                log.append({"name": name, "type": ctype, "status": "exists"})
                continue

            print(f"  генерим...")
            ok = generate_via_openai(prompt, openai_size, args.model, image_path)
            if ok:
                postprocess(image_path, vk_size)
                print(f"    ✓ {image_path.name} ({vk_size[0]}×{vk_size[1]})")
                log.append({"name": name, "type": ctype, "status": "generated", "path": str(image_path)})
            else:
                log.append({"name": name, "type": ctype, "status": "failed"})
            time.sleep(1.5)

    # Сохраняем лог
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    # Сводка
    print("\n=== Сводка ===")
    statuses = {}
    for entry in log:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    print(f"\nЛог: {log_path}")
    if not args.dry_run:
        print(f"Картинки: {images_dir}")
    print(f"Промпты: {prompts_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
