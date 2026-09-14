#!/usr/bin/env python3
"""
generate_lead_magnet_pdfs.py — генератор PDF lead magnets для eBook-креативов.

Workflow:
1. Claude в чате вместе с пользователем пишет контент каждого PDF — сохраняет
   как markdown в assets/pdf_content/<creative_name>.md
2. Этот скрипт читает markdown и собирает PDF через ReportLab
3. Опционально использует обложки из assets/images/<creative>_card1.png (от M1)
   и финальные CTA из assets/images/<creative>_card3.png

Использование (Claude сам запускает в Cowork):
    # Один PDF
    python -m scripts.generate_lead_magnet_pdfs --workspace <path> --only cr_ebook_p1

    # Все eBook-креативы
    python -m scripts.generate_lead_magnet_pdfs --workspace <path>

    # Список ожидаемых markdown-файлов (для подсказки Claude что нужно создать)
    python -m scripts.generate_lead_magnet_pdfs --workspace <path> --list-expected
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts._console import setup_console
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console

try:
    from scripts.pdf_builder import build_pdf
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.pdf_builder import build_pdf


def load_brand_config(workspace: Path) -> dict:
    local = workspace / "brand.json"
    if local.exists():
        with local.open(encoding="utf-8") as f:
            return json.load(f)
    defaults = Path(__file__).parent / "brand_defaults.json"
    if defaults.exists():
        with defaults.open(encoding="utf-8") as f:
            data = json.load(f)
            return {k: v for k, v in data.items() if not k.startswith("_")}
    return {"product_name": "Product", "primary_color": "#1E40AF", "accent_color": "#22C55E"}


def load_creatives(workspace: Path) -> list:
    path = workspace / "creatives.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("creatives", [])
    return data


def is_ebook_creative(creative: dict) -> bool:
    """eBook креатив = есть Lead form, формат carousel или name содержит 'ebook'."""
    name = (creative.get("name", "") or "").lower()
    fmt = (creative.get("format", "") or "").lower()
    cta = (creative.get("call_to_action", "") or "").lower()
    has_lead_form = bool(creative.get("lead_form"))
    return (
        "ebook" in name
        or has_lead_form
        or "download" in cta
        or "lead_form" in fmt
    )


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="M3: генератор PDF lead magnets")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--only", default=None, help="Только указанный креатив")
    parser.add_argument(
        "--list-expected",
        action="store_true",
        help="Показать какие markdown-файлы ожидаются для каких креативов",
    )
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие PDF")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    brand = load_brand_config(workspace)
    creatives = load_creatives(workspace)
    ebook_creatives = [c for c in creatives if is_ebook_creative(c)]

    if args.only:
        ebook_creatives = [c for c in ebook_creatives if c.get("name") == args.only]
        if not ebook_creatives:
            print(f"ERROR: креатив {args.only} не найден или не eBook", file=sys.stderr)
            return 1

    content_dir = workspace / "assets" / "pdf_content"
    pdfs_dir = workspace / "assets" / "pdfs"
    images_dir = workspace / "assets" / "images"

    if args.list_expected:
        print(f"Ожидаемые markdown-файлы в {content_dir}:")
        for cr in ebook_creatives:
            name = cr["name"]
            md_path = content_dir / f"{name}.md"
            status = "✓ есть" if md_path.exists() else "✗ нет — Claude напиши и сохрани"
            print(f"  {md_path.name} — {status}")
            print(f"    тема: {cr.get('title')}")
            print(f"    описание: {cr.get('description', '')[:120]}…")
        return 0

    print(f"Бренд: {brand.get('product_name')}")
    print(f"eBook-креативов: {len(ebook_creatives)}\n")

    results = []

    for cr in ebook_creatives:
        name = cr["name"]
        md_path = content_dir / f"{name}.md"
        pdf_path = pdfs_dir / f"{name}.pdf"

        print(f"[{name}]")

        if not md_path.exists():
            print(f"  ⚠ нет {md_path}")
            print(f"    Claude должен сначала написать markdown-контент в этот файл.")
            print(f"    Тема: {cr.get('title')}")
            results.append({"name": name, "status": "no_content"})
            continue

        if pdf_path.exists() and not args.force:
            print(f"  уже есть {pdf_path.name}, skip (--force чтобы перезаписать)")
            results.append({"name": name, "status": "exists"})
            continue

        md_content = md_path.read_text(encoding="utf-8")

        # Опциональные картинки от M1
        cover_image = images_dir / f"{name}_card1.png"
        cta_image = images_dir / f"{name}_card3.png"

        try:
            output = build_pdf(
                output_path=pdf_path,
                title=cr.get("title", "Lead magnet"),
                subtitle=cr.get("description", "")[:200],
                brand=brand,
                md_content=md_content,
                cover_image=cover_image if cover_image.exists() else None,
                cta_image=cta_image if cta_image.exists() else None,
                cta_url=cr.get("url"),
                company_info=cr.get("company_info"),
            )
            size_kb = output.stat().st_size / 1024
            print(f"  ✓ {output.name} ({size_kb:.0f} KB)")
            results.append({"name": name, "status": "generated", "path": str(output), "size_kb": size_kb})
        except Exception as e:
            print(f"  ✗ ERROR: {e}", file=sys.stderr)
            results.append({"name": name, "status": "failed", "error": str(e)})

    # Сводка
    print("\n=== Сводка ===")
    statuses = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")

    no_content = [r for r in results if r["status"] == "no_content"]
    if no_content:
        print(f"\n⚠ Нужны markdown-файлы для: {', '.join(r['name'] for r in no_content)}")
        print(f"   Claude должен написать содержимое и сохранить в {content_dir}/")

    return 0 if all(r["status"] in ("generated", "exists") for r in results) else 2


if __name__ == "__main__":
    main()
