#!/usr/bin/env python3
"""
generate_creative_videos.py — генерация видео для статичных креативов через AI.

Использование (Claude сам запускает в Cowork):
    # Dry-run: только промпты в assets/video_prompts/
    python -m scripts.generate_creative_videos --workspace <path> --dry-run

    # С реальной генерацией через Runway (дефолт)
    python -m scripts.generate_creative_videos --workspace <path>

    # Сменить провайдер
    python -m scripts.generate_creative_videos --workspace <path> --provider kling

    # Только конкретный креатив
    python -m scripts.generate_creative_videos --workspace <path> --only cr_pain_p1_video

    # Использовать сгенерированную картинку как seed (image-to-video)
    python -m scripts.generate_creative_videos --workspace <path> --use-seed-images

Ключи провайдеров — через scripts.credentials (см. credentials.md).
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from scripts._console import setup_console
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console

# Локальные импорты
try:
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.video_providers import get_provider, PROVIDER_REGISTRY
    from scripts.video_prompt_templates import build_video_prompt, detect_video_type
    from scripts.video_concat import concat_videos, extract_last_frame, check_ffmpeg, FFmpegError
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.video_providers import get_provider, PROVIDER_REGISTRY
    from scripts.video_prompt_templates import build_video_prompt, detect_video_type
    from scripts.video_concat import concat_videos, extract_last_frame, check_ffmpeg, FFmpegError


def load_brand_config(workspace: Path) -> dict:
    local = workspace / "brand.json"
    if local.exists():
        with local.open(encoding="utf-8") as f:
            return json.load(f)
    defaults_path = Path(__file__).parent / "brand_defaults.json"
    if defaults_path.exists():
        with defaults_path.open(encoding="utf-8") as f:
            data = json.load(f)
            return {k: v for k, v in data.items() if not k.startswith("_")}
    return {"product_name": "Product", "primary_color": "#1E40AF", "accent_color": "#22C55E"}


def load_creatives(workspace: Path) -> list:
    path = workspace / "creatives.json"
    if not path.exists():
        raise FileNotFoundError(f"Не найден {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("creatives", [])
    return data


def is_video_creative(creative: dict) -> bool:
    """Определяет является ли креатив видео-форматом."""
    fmt = (creative.get("format", "") or "").lower()
    image_desc = (creative.get("image_or_video", "") or "").lower()
    return "video" in fmt or "video" in image_desc


def parse_duration(image_desc: str, default: int = 5) -> int:
    """Парсит длительность из описания: 'video 9:16, 15 sec, ...' → 15"""
    import re
    m = re.search(r"(\d+)\s*sec", image_desc.lower())
    if m:
        return int(m.group(1))
    return default


def parse_aspect_ratio(image_desc: str, default: str = "9:16") -> str:
    """Парсит aspect ratio из описания: '9:16' / '1:1' / '16:9'."""
    import re
    m = re.search(r"(\d+):(\d+)", image_desc)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return default


def save_prompt(prompt: str, path: Path, creative: dict, vtype: str, duration: int, ratio: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Видео-промпт для {creative.get('name')}\n"
        f"# Тип: {vtype}\n"
        f"# Длительность: {duration}s\n"
        f"# Aspect ratio: {ratio}\n"
        f"# Целевая аудитория: {creative.get('ad_plan_id')}\n"
        f"\n---\n\n{prompt}\n"
    )
    path.write_text(body, encoding="utf-8")


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="Генератор видео для VK Реклама креативов")
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--provider",
        default="runway",
        choices=list(PROVIDER_REGISTRY.keys()),
        help="Какой провайдер видео-генерации использовать",
    )
    parser.add_argument("--only", default=None, help="Только указанный креатив")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--use-seed-images",
        action="store_true",
        help="Использовать assets/images/<name>.png как стартовый кадр (image-to-video)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--max-duration",
        type=int,
        default=5,
        help="Лимит длительности одного сегмента (большинство провайдеров умеют 5-10s).",
    )
    parser.add_argument(
        "--target-duration",
        type=int,
        default=None,
        help="Целевая длительность финального видео (например 15 для 15-секундного ролика). "
        "Если больше --max-duration, скрипт генерит несколько сегментов и склеивает через FFmpeg. "
        "По умолчанию равно requested_duration из creatives.json (или 5s если меньше).",
    )
    parser.add_argument(
        "--chain-segments",
        action="store_true",
        help="При генерации длинных видео — использовать последний кадр предыдущего сегмента "
        "как seed для следующего (более плавные переходы, требует FFmpeg).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="ID модели для Replicate (например 'kwaivgi/kling-v1.6-standard' или version hash). "
        "См. references/replicate-models.md для списка популярных моделей.",
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=None,
        help="Сгенерировать N вариантов ПЕРВОГО сегмента для A/B-выбора. "
        "Сохраняет как <name>_seg1_var1.mp4, _var2.mp4, etc. "
        "Остальные сегменты НЕ генерятся — это первый этап (variants generation).",
    )
    parser.add_argument(
        "--chosen-variant",
        type=int,
        default=None,
        help="Финализация после --variants: выбранный вариант становится seg1, "
        "генерятся остальные сегменты (с chain-segments если нужно), склейка в финал. "
        "Используется на втором этапе workflow.",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    brand = load_brand_config(workspace)
    creatives = load_creatives(workspace)

    if args.only:
        creatives = [c for c in creatives if c.get("name") == args.only]
        if not creatives:
            print(f"ERROR: {args.only} не найден", file=sys.stderr)
            return 1

    # Только видео-креативы
    video_creatives = [c for c in creatives if is_video_creative(c)]
    print(f"Бренд: {brand.get('product_name')}")
    print(f"Видео-креативов: {len(video_creatives)}")
    print(f"Провайдер: {args.provider}")
    print(f"Dry-run: {args.dry_run}\n")

    if not video_creatives:
        print("Нет видео-креативов в creatives.json. Возможно все image-only.")
        return 0

    # Загружаем провайдер (для dry-run пропускаем загрузку ключа)
    provider = None
    if not args.dry_run:
        try:
            provider = get_provider(args.provider, model_id=args.model)
            model_info = f" (model={args.model})" if args.model else ""
            print(f"✓ {args.provider} provider загружен{model_info}")
        except CredentialNotFound as e:
            print(str(e), file=sys.stderr)
            return 1
        except Exception as e:
            print(f"ERROR: не удалось загрузить провайдер {args.provider}: {e}", file=sys.stderr)
            return 1

    # Replicate требует --model
    if args.provider == "replicate" and not args.model and not args.dry_run:
        print(
            "ERROR: для провайдера 'replicate' нужен --model <slug>.\n"
            "Например: --model kwaivgi/kling-v1.6-standard\n"
            "См. references/replicate-models.md для списка моделей.",
            file=sys.stderr,
        )
        return 1

    # Variants / chosen-variant — взаимоисключающие
    if args.variants and args.chosen_variant:
        print("ERROR: --variants и --chosen-variant нельзя одновременно", file=sys.stderr)
        return 1

    # --variants и --chosen-variant работают с одним креативом
    if (args.variants or args.chosen_variant) and not args.only:
        print(
            "ERROR: --variants / --chosen-variant требуют --only <creative_name> "
            "(работаем с одним креативом)",
            file=sys.stderr,
        )
        return 1

    videos_dir = workspace / "assets" / "videos"
    prompts_dir = workspace / "assets" / "video_prompts"
    images_dir = workspace / "assets" / "images"
    log_path = workspace / "assets" / "video_generation_log.json"
    log = []
    total_cost = 0.0

    # Проверка FFmpeg если нужна склейка
    needs_ffmpeg = bool(args.target_duration) or args.chain_segments or args.chosen_variant
    if needs_ffmpeg and not args.dry_run:
        if not check_ffmpeg():
            print(
                "WARN: ffmpeg не найден — склейка длинных видео и chain-segments отключены.\n"
                "Установи: brew install ffmpeg (mac) / apt install ffmpeg (linux) / https://www.gyan.dev/ffmpeg/builds/ (win)",
                file=sys.stderr,
            )
            needs_ffmpeg = False

    # ============ VARIANTS WORKFLOW ============
    # Этап 1: --variants N → генерим N вариантов первого сегмента
    if args.variants:
        creative = video_creatives[0]
        name = creative["name"]
        vtype = detect_video_type(creative)
        image_desc = creative.get("image_or_video", "") or ""
        aspect_ratio = parse_aspect_ratio(image_desc, default="9:16")
        segment_duration = min(args.max_duration, parse_duration(image_desc, default=5))
        prompt = build_video_prompt(creative, brand, vtype)

        prompt_path = prompts_dir / f"{name}.txt"
        save_prompt(prompt, prompt_path, creative, vtype, segment_duration, aspect_ratio)

        print(f"\n=== Этап 1: генерация {args.variants} вариантов первого сегмента ===")
        print(f"  Креатив: {name}, длительность: {segment_duration}s, формат: {aspect_ratio}")

        if args.dry_run:
            for i in range(1, args.variants + 1):
                print(f"  [DRY-RUN] would generate {name}_seg1_var{i}.mp4")
            return 0

        # Seed image (один и тот же для всех вариантов — даёт consistency)
        seed_image = None
        if args.use_seed_images:
            candidate = images_dir / f"{name}.png"
            if candidate.exists():
                seed_image = candidate
                print(f"  using seed image: {candidate.name}")

        for var_idx in range(1, args.variants + 1):
            var_path = videos_dir / f"{name}_seg1_var{var_idx}.mp4"
            print(f"\n  Variant {var_idx}/{args.variants} → {var_path.name}")
            try:
                provider.generate(
                    prompt=prompt,
                    duration_sec=segment_duration,
                    aspect_ratio=aspect_ratio,
                    output_path=var_path,
                    seed_image_path=seed_image,
                )
                cost = provider.estimate_cost(segment_duration) or 0.0
                total_cost += cost
                print(f"    ✓ {var_path.name} (est. ${cost:.2f})")
                log.append({
                    "name": f"{name}_seg1_var{var_idx}",
                    "status": "variant_generated",
                    "path": str(var_path),
                    "model": args.model,
                    "cost_usd": cost,
                })
            except Exception as e:
                print(f"    ✗ ERROR: {e}", file=sys.stderr)
                log.append({"name": f"{name}_seg1_var{var_idx}", "status": "failed", "error": str(e)})
            time.sleep(2)

        # Сохраняем лог и выходим — пользователь выбирает вариант
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as f:
            json.dump({"total_estimated_cost_usd": total_cost, "entries": log, "phase": "variants"}, f, ensure_ascii=False, indent=2)

        print(f"\n=== Готово ===")
        print(f"  Создано {args.variants} вариантов в {videos_dir}")
        print(f"  Общая стоимость: ${total_cost:.2f}")
        print(f"\n  Следующий шаг: выбери лучший вариант и запусти финализацию:")
        print(f"    python -m scripts.generate_creative_videos \\")
        print(f"      --workspace {workspace} \\")
        print(f"      --only {name} \\")
        print(f"      --chosen-variant <N>  # N от 1 до {args.variants}")
        if args.target_duration:
            print(f"      --target-duration {args.target_duration}")
        return 0

    # Этап 2: --chosen-variant K → копируем выбранный как seg1, генерим остальные, склейка
    if args.chosen_variant:
        creative = video_creatives[0]
        name = creative["name"]
        vtype = detect_video_type(creative)
        image_desc = creative.get("image_or_video", "") or ""
        aspect_ratio = parse_aspect_ratio(image_desc, default="9:16")
        requested_duration = parse_duration(image_desc, default=5)
        target_duration = args.target_duration or requested_duration
        segment_duration = min(args.max_duration, target_duration)
        num_segments = max(1, (target_duration + segment_duration - 1) // segment_duration)

        # Проверяем что вариант существует
        chosen_path = videos_dir / f"{name}_seg1_var{args.chosen_variant}.mp4"
        if not chosen_path.exists():
            print(f"ERROR: не найден {chosen_path}. Сначала запусти --variants N.", file=sys.stderr)
            return 1

        print(f"\n=== Этап 2: финализация — вариант {args.chosen_variant} как seg1 ===")
        print(f"  Креатив: {name}, target: {target_duration}s, {num_segments} сегментов по {segment_duration}s")

        # Копируем выбранный вариант как seg1
        seg1_path = videos_dir / f"{name}_seg1.mp4"
        import shutil
        shutil.copy(chosen_path, seg1_path)
        print(f"  ✓ {chosen_path.name} → {seg1_path.name}")

        segments_paths = [seg1_path]

        # Извлекаем последний кадр первого сегмента для chain
        prev_last_frame = None
        if args.chain_segments and num_segments > 1 and needs_ffmpeg:
            try:
                last_frame_path = videos_dir / f"{name}_seg1_last.png"
                prev_last_frame = extract_last_frame(seg1_path, last_frame_path)
                print(f"  ✓ last frame seg1 → {last_frame_path.name} (seed for seg2)")
            except FFmpegError as e:
                print(f"  WARN: не извлёк last frame: {e}", file=sys.stderr)

        # Генерим остальные сегменты (2..N)
        if num_segments > 1 and not args.dry_run:
            prompt = build_video_prompt(creative, brand, vtype)
            for seg_idx in range(2, num_segments + 1):
                seg_path = videos_dir / f"{name}_seg{seg_idx}.mp4"
                print(f"\n  Segment {seg_idx}/{num_segments} → {seg_path.name}")
                try:
                    provider.generate(
                        prompt=prompt,
                        duration_sec=segment_duration,
                        aspect_ratio=aspect_ratio,
                        output_path=seg_path,
                        seed_image_path=prev_last_frame,
                    )
                    cost = provider.estimate_cost(segment_duration) or 0.0
                    total_cost += cost
                    print(f"    ✓ {seg_path.name} (est. ${cost:.2f})")
                    segments_paths.append(seg_path)
                    log.append({
                        "name": f"{name}_seg{seg_idx}",
                        "status": "generated",
                        "path": str(seg_path),
                        "cost_usd": cost,
                    })

                    # Извлекаем кадр для следующего
                    if args.chain_segments and seg_idx < num_segments and needs_ffmpeg:
                        try:
                            last_frame_path = videos_dir / f"{name}_seg{seg_idx}_last.png"
                            prev_last_frame = extract_last_frame(seg_path, last_frame_path)
                        except FFmpegError:
                            prev_last_frame = None
                except Exception as e:
                    print(f"    ✗ ERROR: {e}", file=sys.stderr)
                    log.append({"name": f"{name}_seg{seg_idx}", "status": "failed", "error": str(e)})
                time.sleep(2)

        # Склейка
        final_path = videos_dir / f"{name}.mp4"
        if len(segments_paths) > 1 and needs_ffmpeg and not args.dry_run:
            try:
                concat_videos(segments_paths, final_path, method="concat")
                print(f"\n  ✓ Склейка готова: {final_path.name}")
            except FFmpegError as e:
                print(f"  WARN: concat не сработал ({e}), пробую reencode...")
                try:
                    concat_videos(segments_paths, final_path, method="reencode")
                    print(f"  ✓ Склейка (reencode): {final_path.name}")
                except FFmpegError as e2:
                    print(f"  ✗ ERROR склейка: {e2}", file=sys.stderr)
        elif len(segments_paths) == 1 and not args.dry_run:
            shutil.copy(seg1_path, final_path)
            print(f"\n  ✓ {final_path.name} (1 сегмент, без склейки)")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as f:
            json.dump({"total_estimated_cost_usd": total_cost, "entries": log, "phase": "finalize", "chosen_variant": args.chosen_variant}, f, ensure_ascii=False, indent=2)

        print(f"\n=== Финал готов ===")
        print(f"  Файл: {final_path}")
        print(f"  Доп. стоимость: ${total_cost:.2f}")
        return 0

    # ============ СТАНДАРТНЫЙ WORKFLOW (без variants) ============

    for creative in video_creatives:
        name = creative.get("name", "unnamed")
        vtype = detect_video_type(creative)
        image_desc = creative.get("image_or_video", "") or ""

        # Парсим длительность и aspect ratio
        requested_duration = parse_duration(image_desc, default=5)
        aspect_ratio = parse_aspect_ratio(image_desc, default="9:16")

        # Целевая длительность финала
        target_duration = args.target_duration or requested_duration
        # Длительность одного сегмента
        segment_duration = min(args.max_duration, target_duration)

        # Сколько сегментов нужно
        num_segments = max(1, (target_duration + segment_duration - 1) // segment_duration)
        if num_segments > 1 and not needs_ffmpeg and not args.dry_run:
            print(f"[{name}] WARN: для {target_duration}s нужно {num_segments} сегментов и склейка, но ffmpeg недоступен → 1 сегмент {segment_duration}s")
            num_segments = 1
            target_duration = segment_duration

        print(f"[{name}] type={vtype}, target={target_duration}s, {num_segments} × {segment_duration}s сегментов, {aspect_ratio}")

        # Промпт
        prompt = build_video_prompt(creative, brand, vtype)
        prompt_path = prompts_dir / f"{name}.txt"
        save_prompt(prompt, prompt_path, creative, vtype, segment_duration, aspect_ratio)

        if args.dry_run:
            print(f"  prompt → {prompt_path.name}")
            log.append({
                "name": name, "type": vtype, "status": "dry_run", "prompt": str(prompt_path),
                "num_segments": num_segments, "segment_duration": segment_duration, "target_duration": target_duration,
            })
            continue

        video_path = videos_dir / f"{name}.mp4"
        if video_path.exists() and not args.force:
            print(f"  уже есть {video_path.name}, skip")
            log.append({"name": name, "type": vtype, "status": "exists"})
            continue

        # Начальный seed image (если есть)
        initial_seed = None
        if args.use_seed_images:
            candidate = images_dir / f"{name}.png"
            if candidate.exists():
                initial_seed = candidate
                print(f"  using seed image: {candidate.name}")

        # Оценка стоимости (для всех сегментов)
        cost = provider.estimate_cost(segment_duration * num_segments) if provider.PRICE_PER_SECOND_USD else 0.0
        total_cost += cost
        print(f"  generating {num_segments} segment(s)... (est. ${cost:.2f})")

        # Генерим сегменты
        segments_paths = []
        prev_last_frame = initial_seed
        gen_failed = False

        for seg_idx in range(num_segments):
            seg_path = videos_dir / f"{name}_seg{seg_idx + 1}.mp4"
            try:
                provider.generate(
                    prompt=prompt,
                    duration_sec=segment_duration,
                    aspect_ratio=aspect_ratio,
                    output_path=seg_path,
                    seed_image_path=prev_last_frame,
                )
                segments_paths.append(seg_path)
                print(f"    ✓ segment {seg_idx + 1}/{num_segments}: {seg_path.name}")
            except Exception as e:
                print(f"    ✗ segment {seg_idx + 1}: {e}", file=sys.stderr)
                gen_failed = True
                break

            # Извлекаем последний кадр для chain-segments
            if args.chain_segments and seg_idx + 1 < num_segments and needs_ffmpeg:
                try:
                    last_frame_path = videos_dir / f"{name}_seg{seg_idx + 1}_last.png"
                    prev_last_frame = extract_last_frame(seg_path, last_frame_path)
                    print(f"      last frame → {last_frame_path.name} (seed for next)")
                except FFmpegError as e:
                    print(f"      WARN: не извлёк last frame: {e}", file=sys.stderr)
                    prev_last_frame = None

            time.sleep(2)  # пауза между API запросами

        if gen_failed:
            log.append({"name": name, "type": vtype, "status": "failed", "segments_done": len(segments_paths)})
            continue

        # Склейка (если больше 1 сегмента)
        if len(segments_paths) > 1:
            try:
                # Пробуем быструю склейку через concat demuxer
                concat_videos(segments_paths, video_path, method="concat")
                print(f"    ✓ склеено: {video_path.name}")
            except FFmpegError as e:
                # Fallback на reencode если concat не справился (разные кодеки)
                print(f"    WARN: concat не сработал ({e}), пробую reencode...")
                try:
                    concat_videos(segments_paths, video_path, method="reencode")
                    print(f"    ✓ склеено (reencode): {video_path.name}")
                except FFmpegError as e2:
                    print(f"    ✗ ERROR склейка: {e2}", file=sys.stderr)
                    log.append({"name": name, "type": vtype, "status": "concat_failed"})
                    continue
        else:
            # Один сегмент — просто переименовываем
            segments_paths[0].rename(video_path)

        log.append({
            "name": name,
            "type": vtype,
            "status": "generated",
            "path": str(video_path),
            "target_duration_sec": target_duration,
            "num_segments": num_segments,
            "segment_duration_sec": segment_duration,
            "aspect_ratio": aspect_ratio,
            "estimated_cost_usd": cost,
        })

    # Лог
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        json.dump({"total_estimated_cost_usd": total_cost, "entries": log}, f, ensure_ascii=False, indent=2)

    print("\n=== Сводка ===")
    statuses = {}
    for entry in log:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    if not args.dry_run:
        print(f"  Оценка стоимости: ${total_cost:.2f}")
    print(f"\nЛог: {log_path}")
    if not args.dry_run:
        print(f"Видео: {videos_dir}")
    print(f"Промпты: {prompts_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
