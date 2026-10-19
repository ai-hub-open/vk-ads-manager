"""
_image_prep.py — подготовка картинок к загрузке в VK Рекламу.

Зачем
-----
Роли картинки в объявлении требуют точного размера. Роль `image_600x600`
принимает квадрат шириной **ровно 600** и отклоняет 1080×1080 (наблюдение вживую,
10.2026); роли под квадрат 1080 нет (banner_patterns, 19.10.2026 — image_1080x1080
не существует). Поэтому:

* квадрат больше 600 → 600×600;
* 16:9 (в пределах допуска) шириной от 1080 → ровно 1080×607 (роль `image_1080x607`);
* 4:5 (в пределах допуска) шириной от 1080 → ровно 1080×1350 (роль `image_1080x1350`);
* меньше нужного размера — не растягиваем, помечаем `too_small`;
* соотношение не подходит ни под одну роль — не обрезаем молча, роль = None.

Роли `image_1080x607` / `image_1080x1350` есть в banner_patterns (шаблоны 513 / 515),
но объявление с ними вживую не создавалось; точный размер 1080×607 / 1080×1350 —
по описанию инструментов хостового MCP-сервера vk-ads.

Логотип (роль `icon_256x256`) ужимается до 256×256 (`fit_square(side=256)`).

Исходник на диске не меняется: работаем с байтами в памяти, наружу уходит копия.
Без Pillow ничего не ужимается — печатаем явное предупреждение (VK отклонит
квадрат больше 600); ставится через `python install.py`.

Используют: `scripts/upload_creatives_to_storage.py` (путь A, KeepImage) и
`scripts/vk_ads_api.py` → `MediaAPI.upload_image` (путь B, REST).
"""

from __future__ import annotations

import io
import sys
from typing import Optional

SQUARE_SIDE = 600   # роль image_600x600
LOGO_SIDE = 256     # роль icon_256x256

ROLE_SQUARE = "image_600x600"
ROLE_HORIZONTAL = "image_1080x607"    # есть в banner_patterns (513); объявление вживую не создавалось
ROLE_VERTICAL = "image_1080x1350"     # есть в banner_patterns (515); объявление вживую не создавалось
UNVERIFIED_ROLES = {ROLE_HORIZONTAL, ROLE_VERTICAL}

# Точные размеры не-квадратных ролей.
ROLE_SIZES = {ROLE_HORIZONTAL: (1080, 607), ROLE_VERTICAL: (1080, 1350)}
RATIO_TOLERANCE = 0.02  # относительное отклонение соотношения сторон

# Форматы, которые умеем пересохранить (GIF не трогаем — анимация).
_RESAVE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

PILLOW_MISSING_WARNING = (
    "⚠️ Pillow не установлен — картинки не ужимаются. Квадрат больше 600×600 VK отклонит "
    "в роли image_600x600, а 16:9 / 4:5 должны быть ровно 1080×607 / 1080×1350. "
    "Поставьте зависимости: python install.py (или pip install pillow)."
)
_warned_no_pillow = False


def _load_pil():
    """Модуль PIL.Image или None (Pillow не установлен)."""
    try:
        from PIL import Image
        return Image
    except ImportError:
        return None


def _warn_no_pillow(info: dict) -> None:
    global _warned_no_pillow
    info["pillow_missing"] = True
    if not _warned_no_pillow:
        print(PILLOW_MISSING_WARNING, file=sys.stderr)
        _warned_no_pillow = True


def _empty_info() -> dict:
    return {"width": None, "height": None, "resized": False, "original": None,
            "mime": None, "role": None, "too_small": False, "pillow_missing": False}


def _open(blob: bytes, info: dict):
    Image = _load_pil()
    if Image is None:
        _warn_no_pillow(info)
        return None, None
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
    except Exception:
        return Image, None  # не картинка (или битая) — решать не нам
    return Image, img


def _resave(Image, img, size: tuple, fmt: str) -> bytes:
    resized = img.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    if fmt == "JPEG":
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")
        resized.save(out, format="JPEG", quality=95)
    else:
        resized.save(out, format=fmt)
    return out.getvalue()


def image_role(width: Optional[int], height: Optional[int]) -> Optional[str]:
    """Роль картинки по соотношению сторон: квадрат / 16:9 / 4:5. Не подошло — None."""
    if not width or not height:
        return None
    if width == height:
        return ROLE_SQUARE
    ratio = width / height
    for role, (tw, th) in ROLE_SIZES.items():
        if abs(ratio / (tw / th) - 1) <= RATIO_TOLERANCE:
            return role
    return None


def role_size(role: str) -> Optional[tuple]:
    """Точный размер роли: квадрат 600×600, 1080×607, 1080×1350."""
    if role == ROLE_SQUARE:
        return (SQUARE_SIDE, SQUARE_SIDE)
    return ROLE_SIZES.get(role)


def fit_square(blob: bytes, side: int = SQUARE_SIDE) -> tuple[bytes, dict]:
    """Ужимает квадрат больше `side` до side×side. Остальное отдаёт как есть.

    info = {width, height, resized, original, mime, ...}: width/height — того, что
    уйдёт в загрузку (None, если не картинка); original — "1080x1080", если ужали.
    """
    info = _empty_info()
    Image, img = _open(blob, info)
    if img is None:
        return blob, info
    w, h = img.size
    info["width"], info["height"] = w, h
    fmt = (img.format or "").upper()
    if w != h or w <= side or fmt not in _RESAVE_FORMATS:
        return blob, info
    out = _resave(Image, img, (side, side), fmt)
    info.update(width=side, height=side, resized=True, original=f"{w}x{h}",
                mime=_RESAVE_FORMATS[fmt])
    return out, info


def prepare_image(blob: bytes) -> tuple[bytes, dict]:
    """Приводит картинку объявления к точному размеру её роли.

    Квадрат > 600 → 600×600; 16:9 → 1080×607; 4:5 → 1080×1350 (уменьшение или
    подгонка в пределах допуска). Меньше нужного — не растягиваем (too_small).
    Соотношение не подходит ни под одну роль — role=None, байты как есть.
    """
    info = _empty_info()
    Image, img = _open(blob, info)
    if img is None:
        return blob, info
    w, h = img.size
    info["width"], info["height"] = w, h
    role = image_role(w, h)
    info["role"] = role
    if role is None:
        return blob, info
    target = role_size(role)
    if (w, h) == target:
        return blob, info
    if w < target[0]:
        info["too_small"] = True
        return blob, info
    fmt = (img.format or "").upper()
    if fmt not in _RESAVE_FORMATS:
        return blob, info
    out = _resave(Image, img, target, fmt)
    info.update(width=target[0], height=target[1], resized=True, original=f"{w}x{h}",
                mime=_RESAVE_FORMATS[fmt])
    return out, info
