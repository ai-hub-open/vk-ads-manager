"""
video_concat.py — склейка и пост-обработка видео через FFmpeg.

Используется для:
- Склейки нескольких 5s сегментов в одно 15-30s видео
- Извлечения последнего кадра сегмента (для image-to-video chaining)
- Добавления overlay-текста (subtitles, заголовки)
- Конвертации в нужный формат для VK Ads

Требует FFmpeg установленный в системе:
- Windows: https://www.gyan.dev/ffmpeg/builds/ или choco install ffmpeg
- macOS: brew install ffmpeg
- Linux: apt install ffmpeg / yum install ffmpeg

Использование:
    # Склеить несколько сегментов в один
    python -m scripts.video_concat concat --input seg1.mp4 seg2.mp4 seg3.mp4 --output final.mp4

    # Извлечь последний кадр (для seed-image следующего сегмента)
    python -m scripts.video_concat last-frame --input video.mp4 --output last.png

    # Добавить overlay-текст
    python -m scripts.video_concat overlay-text --input video.mp4 --output result.mp4 \
        --text "5 готовых процессов" --position bottom

    # Проверить установлен ли ffmpeg
    python -m scripts.video_concat check
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class FFmpegError(Exception):
    pass


def check_ffmpeg() -> Optional[str]:
    """Возвращает путь к ffmpeg или None если не установлен."""
    return shutil.which("ffmpeg")


def require_ffmpeg() -> str:
    """Бросает понятную ошибку если ffmpeg не найден."""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise FFmpegError(
            "FFmpeg не установлен. Установи:\n"
            "  Windows: https://www.gyan.dev/ffmpeg/builds/ (распаковать и добавить в PATH)\n"
            "           или: choco install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )
    return ffmpeg


def _run(cmd: list, timeout: int = 300) -> str:
    """Запускает ffmpeg, возвращает stderr (там вся диагностика)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise FFmpegError(f"FFmpeg failed (code {proc.returncode}):\n{proc.stderr[:1000]}")
        return proc.stderr
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"FFmpeg timeout after {timeout}s")


def concat_videos(segments: List[Path], output: Path, method: str = "concat") -> Path:
    """
    Склейка видео-сегментов в один файл.

    Args:
        segments: список путей к .mp4 файлам
        output: куда сохранить результат
        method: "concat" (быстрая склейка через demuxer, без перекодирования)
                "reencode" (через filter_complex, медленнее но надёжнее если кодеки разные)
    """
    ffmpeg = require_ffmpeg()
    segments = [Path(s).resolve() for s in segments]

    for s in segments:
        if not s.exists():
            raise FFmpegError(f"Не найден сегмент: {s}")

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if method == "concat":
        # Метод 1: concat demuxer — быстрый, без перекодирования
        # Требует одинаковых кодеков, контейнера, разрешения
        list_file = output.parent / f".concat_{output.stem}.txt"
        try:
            with list_file.open("w", encoding="utf-8") as f:
                for s in segments:
                    # FFmpeg на Windows требует прямых слэшей или экранирования
                    f.write(f"file '{s.as_posix()}'\n")

            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy", str(output),
            ]
            _run(cmd)
        finally:
            list_file.unlink(missing_ok=True)
    else:
        # Метод 2: filter_complex с перекодированием
        # Надёжнее но медленнее в 5-10 раз
        inputs = []
        for s in segments:
            inputs.extend(["-i", str(s)])
        filter_str = "".join([f"[{i}:v][{i}:a]" for i in range(len(segments))])
        filter_str += f"concat=n={len(segments)}:v=1:a=1[outv][outa]"
        cmd = [
            ffmpeg, "-y", *inputs,
            "-filter_complex", filter_str,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast",
            str(output),
        ]
        _run(cmd, timeout=600)

    return output


def extract_last_frame(video_path: Path, output_path: Path) -> Path:
    """
    Извлекает последний кадр видео как PNG.
    Используется для image-to-video chaining (последний кадр сегмента N
    становится seed для сегмента N+1).
    """
    ffmpeg = require_ffmpeg()
    video_path = Path(video_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Получаем длительность видео для seek
    probe_cmd = [
        ffmpeg, "-i", str(video_path),
        "-f", "null", "-",
    ]
    try:
        stderr = _run(probe_cmd, timeout=30)
    except FFmpegError as e:
        # ffmpeg выводит инфо в stderr с ненулевым кодом — это нормально для probe
        stderr = str(e)

    # Парсим длительность из stderr (Duration: HH:MM:SS.XX)
    import re
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", stderr)
    if m:
        h, mi, s = m.groups()
        duration = int(h) * 3600 + int(mi) * 60 + float(s)
        seek = max(0, duration - 0.1)
    else:
        seek = 4.9  # fallback для 5-секундных видео

    cmd = [
        ffmpeg, "-y", "-ss", str(seek), "-i", str(video_path),
        "-vframes", "1", "-q:v", "2",
        str(output_path),
    ]
    _run(cmd, timeout=60)
    return output_path


def overlay_text(
    video_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom",
    font_color: str = "white",
    box_color: str = "black@0.5",
    font_size: int = 48,
) -> Path:
    """
    Добавляет overlay-текст на видео.

    Args:
        video_path: исходное видео
        output_path: куда сохранить
        text: текст overlay
        position: "top", "bottom", "center"
        font_color: цвет шрифта
        box_color: цвет подложки (с alpha)
        font_size: размер шрифта в px
    """
    ffmpeg = require_ffmpeg()
    video_path = Path(video_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Позиция в filter_complex
    if position == "top":
        y_expr = "h*0.08"
    elif position == "center":
        y_expr = "(h-text_h)/2"
    else:
        y_expr = "h*0.85"

    # Экранируем кавычки и спецсимволы
    safe_text = text.replace("'", "\\'").replace(":", "\\:")

    drawtext = (
        f"drawtext=text='{safe_text}'"
        f":fontcolor={font_color}"
        f":fontsize={font_size}"
        f":box=1:boxcolor={box_color}:boxborderw=10"
        f":x=(w-text_w)/2:y={y_expr}"
    )

    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "copy",
        str(output_path),
    ]
    _run(cmd, timeout=300)
    return output_path


# ============ CLI ============

def cmd_check(args):
    ffmpeg = check_ffmpeg()
    if ffmpeg:
        print(f"✓ FFmpeg найден: {ffmpeg}")
        # Покажем версию
        try:
            r = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
            print(r.stdout.split("\n")[0])
        except Exception:
            pass
        return 0
    else:
        print("✗ FFmpeg не установлен.", file=sys.stderr)
        print("\nУстановить:", file=sys.stderr)
        print("  Windows: https://www.gyan.dev/ffmpeg/builds/", file=sys.stderr)
        print("  macOS:   brew install ffmpeg", file=sys.stderr)
        print("  Linux:   sudo apt install ffmpeg", file=sys.stderr)
        return 1


def cmd_concat(args):
    try:
        out = concat_videos([Path(p) for p in args.input], Path(args.output), method=args.method)
        print(f"✓ {out} (склеено {len(args.input)} сегментов)")
        return 0
    except FFmpegError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_last_frame(args):
    try:
        out = extract_last_frame(Path(args.input), Path(args.output))
        print(f"✓ Последний кадр сохранён: {out}")
        return 0
    except FFmpegError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_overlay(args):
    try:
        out = overlay_text(
            Path(args.input), Path(args.output),
            text=args.text, position=args.position,
            font_size=args.font_size,
        )
        print(f"✓ {out}")
        return 0
    except FFmpegError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Видео-склейка и пост-обработка через FFmpeg")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Проверить установлен ли FFmpeg")
    p_check.set_defaults(func=cmd_check)

    p_concat = sub.add_parser("concat", help="Склеить несколько видео в одно")
    p_concat.add_argument("--input", nargs="+", required=True, help="Список MP4 файлов")
    p_concat.add_argument("--output", required=True)
    p_concat.add_argument(
        "--method", choices=["concat", "reencode"], default="concat",
        help="concat = быстрая склейка без перекодирования (нужны одинаковые кодеки); "
        "reencode = надёжнее, медленнее",
    )
    p_concat.set_defaults(func=cmd_concat)

    p_lf = sub.add_parser("last-frame", help="Извлечь последний кадр видео")
    p_lf.add_argument("--input", required=True)
    p_lf.add_argument("--output", required=True)
    p_lf.set_defaults(func=cmd_last_frame)

    p_ov = sub.add_parser("overlay-text", help="Добавить overlay-текст на видео")
    p_ov.add_argument("--input", required=True)
    p_ov.add_argument("--output", required=True)
    p_ov.add_argument("--text", required=True)
    p_ov.add_argument("--position", choices=["top", "bottom", "center"], default="bottom")
    p_ov.add_argument("--font-size", type=int, default=48)
    p_ov.set_defaults(func=cmd_overlay)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
