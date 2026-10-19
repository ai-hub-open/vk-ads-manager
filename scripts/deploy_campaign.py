#!/usr/bin/env python3
"""
deploy_campaign.py — финальный модуль M4. Читает рабочую папку кампании и
заливает всё в кабинет VK Реклама через API в ПРАВИЛЬНОЙ иерархии:

    media → ad_plan (кампания) → campaigns (группы) → banners (объявления)

Группа получает ad_plan_id автоматически, поэтому orphan-групп не возникает, а
объявления уходят вложенным массивом внутри группы: отдельного создания
объявления в API нет (`POST /banners.json` → 405). Все объекты создаются в
status=blocked; активация НИКОГДА не выполняется здесь (только вручную после
чек-листа + ОРД).

Использование:
    python -m scripts.deploy_campaign --workspace <path> --dry-run   # только план
    python -m scripts.deploy_campaign --workspace <path>             # реальный залив
    python -m scripts.deploy_campaign --workspace <path> --skip-media-upload

Что читает:
- creatives.json — список баннеров (+ опц. creatives.json[campaign] — конфиг кампании)
- audiences.json — список аудиторий (каждая → отдельная группа)
- _state.json — бюджет, гео, цель, названия
- assets/images/*.png, assets/videos/*.mp4 — медиа

Что создаёт в VK:
- 1 ad_plan (Кампания, status=blocked) — цель + общий бюджет
- N campaigns (Группы, по числу аудиторий, status=blocked) — таргетинги/плейсменты/дневной бюджет
- M banners (Объявления, status=blocked) — создаются вместе со своей группой

Форма объявления (0.7.2). VK отвергает старые поля format/title/description/url/
images как неизвестные. Объявление собирается по ролям:

    {"name": ..., "status": "blocked",
     "urls": {"primary": {"id": <id из POST /urls.json>}},
     "textblocks": {"title_40_vkads": {"text": ...}, "text_90": {"text": ...},
                    "cta_sites_full": {"text": "visitSite"}},
     "content": {"icon_256x256": {"id": <логотип>}, "image_600x600": {"id": <картинка>}}}

Эта форма подтверждена вживую через MCP (одиночная квадратная картинка) и сверена со
справочником banner_patterns (19.10.2026: шаблоны 513–515, icon_256x256 обязателен).
Путь B целиком (REST, кейс TC-03) вживую НЕ проверен. Роли image_1080x607 /
image_1080x1350 есть в banner_patterns, но объявление с ними вживую не создавалось.
about_company_115 — необязательная роль, по умолчанию не шлём (ОРД — из кабинета).
Видео и карусель уходят в старой форме (videos / images): форма по ролям для них не
реализована (роли видео — banner_patterns 321–324: icon_256x256 + video_*_30s;
слайды карусели не разобраны) — НЕ ПРОВЕРЕНО.

Что НЕ делает:
- Не активирует (status=active никогда не ставится тут)
- Не создаёт CRM-list / lookalike (нужен source)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console
    from _workspace import resolve_workspace

try:
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.vk_ads_api import (VKAdsClient, VKAdsError, BudgetGuardError, budget_rub,
                                    check_budget_guard, check_kopeck_units, fmt_rub,
                                    OrdDataRequiredError, ORD_DATA_REQUIRED_MESSAGE, only_children)
    from scripts._image_prep import image_role, role_size, ROLE_SQUARE, UNVERIFIED_ROLES
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.vk_ads_api import (VKAdsClient, VKAdsError, BudgetGuardError, budget_rub,
                                    check_budget_guard, check_kopeck_units, fmt_rub,
                                    OrdDataRequiredError, ORD_DATA_REQUIRED_MESSAGE, only_children)
    from scripts._image_prep import image_role, role_size, ROLE_SQUARE, UNVERIFIED_ROLES


# Лимиты ролей текста в объявлении «на сайт». Не режем молча — отказываем с понятной ошибкой.
TITLE_ROLE, TITLE_MAX = "title_40_vkads", 40
TEXT_ROLE, TEXT_MAX = "text_90", 90
CTA_ROLE, CTA_DEFAULT = "cta_sites_full", "visitSite"
ABOUT_ROLE, ABOUT_MAX = "about_company_115", 115   # необязательная роль banner_patterns; по умолчанию не шлём
LOGO_ROLE = "icon_256x256"

LOGO_CANDIDATES = ("assets/brand/logo.png", "assets/brand/logo.jpg", "assets/brand/logo.jpeg",
                   "assets/images/logo.png", "assets/images/logo.jpg", "assets/images/logo.jpeg")


# ============ Helpers ============

def _utc_now() -> datetime:
    """Текущее время UTC с указанием зоны.

    Не `datetime.utcnow()`: он возвращает naive-время и с Python 3.12 объявлен
    устаревшим (намечен к удалению). Пометка зоны заодно делает `started_at`
    в `deploy_plan.json` однозначным — раньше по строке нельзя было понять,
    UTC там или локальное время машины.
    """
    return datetime.now(timezone.utc)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_creatives(workspace: Path) -> list:
    data = load_json(workspace / "creatives.json", default=[])
    if isinstance(data, dict):
        return data.get("creatives", [])
    return data


def load_audiences(workspace: Path) -> list:
    data = load_json(workspace / "audiences.json", default=[])
    if isinstance(data, dict):
        return data.get("audiences", [])
    return data


def load_brand(workspace: Path) -> dict:
    data = load_json(workspace / "brand.json", default={})
    return data if isinstance(data, dict) else {}


def load_ad_plan_config(workspace: Path) -> dict:
    """Конфиг КАМПАНИИ (ad_plan): цель + общий бюджет. Из creatives.json[campaign] или _state.json."""
    creatives_data = load_json(workspace / "creatives.json", default={})
    if isinstance(creatives_data, dict) and "campaign" in creatives_data:
        cfg = dict(creatives_data["campaign"])
        cfg.setdefault("status", "blocked")
        for key in ("budget_limit", "budget_limit_day"):  # рубли, как принимает API
            if cfg.get(key) is not None:
                cfg[key] = budget_rub(cfg[key])
        return cfg
    state = load_json(workspace / "_state.json", default={})
    return {
        "name": f"{state.get('brand_name_ru', 'Campaign')} — VK тест",
        "objective": state.get("objective", "conversions"),
        "status": "blocked",
        "budget_limit": budget_rub(state.get("budget_rub", 50000)),
        "budget_limit_day": budget_rub(state.get("daily_budget_rub", 3500)),
        "bid_strategy": state.get("bid_strategy", "minimal_price"),
    }


# ============ Audience → targeting mapping ============

def audience_to_targeting(audience: dict) -> dict:
    cfg = audience.get("config", {})
    age = cfg.get("age", [25, 45])
    targeting = {"age": age, "sex": [1, 2]}
    targeting["geo"] = _geo_to_regions(cfg.get("geo", ""))
    if "community_ids_or_urls" in cfg:
        comm_ids = [_extract_community_id(x) for x in cfg["community_ids_or_urls"]]
        targeting["communities"] = [c for c in comm_ids if c]
    if "interests" in cfg:
        targeting["interests"] = interest_ids(cfg["interests"])
    if "additional_interests" in cfg:
        targeting["interest_categories"] = interest_ids(cfg["additional_interests"])
    if "position_filter" in cfg:
        targeting["positions"] = cfg["position_filter"]
    return targeting


def interest_ids(items) -> list:
    """ID интересов для API: {"id", "name"} (из targetings_tree, Шаг 6.5) или число.

    Интерес только словами (без ID) в API не отправить — пропускаем с предупреждением:
    ID подбирается в кабинете или в дереве targetings_tree.
    """
    ids = []
    for it in items or []:
        if isinstance(it, dict) and it.get("id") is not None:
            ids.append(it["id"])
        elif isinstance(it, int) or (isinstance(it, str) and it.isdigit()):
            ids.append(int(it))
        else:
            name = it.get("name") if isinstance(it, dict) else it
            print(f"  ⚠ интерес «{name}» без ID — в API не уйдёт; ID — из targetings_tree или в кабинете")
    return ids


def _geo_to_regions(geo: str) -> dict:
    if geo == "RF_million_cities":
        return {"regions": [1, 2, 54, 65, 43, 47, 56, 51, 172, 39, 35, 0, 50, 0]}
    if geo == "MSK":
        return {"regions": [1]}
    if geo == "SPB":
        return {"regions": [2, 10174]}
    if geo == "RF":
        return {"countries": ["ru"]}
    return {"countries": ["ru"]}


def _extract_community_id(url_or_id) -> Optional[int]:
    if isinstance(url_or_id, int):
        return url_or_id
    s = str(url_or_id)
    if s.isdigit():
        return int(s)
    return None


# ============ Media upload ============

def _describe(source) -> str:
    """Короткая подпись источника для лога: имя файла или сама ссылка."""
    s = str(source)
    return s if s.startswith("http") else Path(s).name


def _image_source(creative: dict, local: Path):
    """Источник картинки: публичная ссылка от маркетолога или локальный файл.

    Ссылка имеет приоритет: если маркетолог прислал готовый креатив ссылкой,
    локальный одноимённый файл — это, скорее всего, наша генерация, которую
    его картинка заменяет.
    """
    url = creative.get("image_url")
    if url:
        return url
    return local if local.exists() else None


def _video_source(creative: dict, local: Path):
    url = creative.get("video_url")
    if url:
        return url
    return local if local.exists() else None


def _carousel_sources(creative: dict, images_dir: Path, name: str) -> list:
    """Источники карточек карусели: список ссылок либо файлы `<name>_cardN.png`."""
    urls = creative.get("image_urls") or ([creative["image_url"]] if creative.get("image_url") else [])
    if urls:
        return list(urls)
    cards = sorted(images_dir.glob(f"{name}_card*.png"))
    if cards:
        return cards
    single = images_dir / f"{name}.png"
    return [single] if single.exists() else []


def upload_media(client: VKAdsClient, creatives: list, workspace: Path, skip_media: bool, plan: dict) -> dict:
    """Возвращает media_id_for_creative: name → {image_id|image_ids|video_id}."""
    images_dir = workspace / "assets" / "images"
    videos_dir = workspace / "assets" / "videos"
    media_id_for_creative = {}

    if skip_media:
        print("--- Шаг 1/4: пропущен (skip-media-upload) ---")
        for cr in creatives:
            entry = {"creative": cr["name"]}
            if "vk_image_id" in cr:
                entry["image_id"] = cr["vk_image_id"]
            if "vk_video_id" in cr:
                entry["video_id"] = cr["vk_video_id"]
            media_id_for_creative[cr["name"]] = entry
        return media_id_for_creative

    print("\n--- Шаг 1/4: загрузка медиа ---")
    try:
        _upload_media_loop(client, creatives, images_dir, videos_dir, media_id_for_creative, plan)
    finally:
        # id загруженного контента не теряем даже при сбое: API не отдаёт список загруженного
        plan["media_uploaded"] = [{"creative": k, **v} for k, v in media_id_for_creative.items()]
    return media_id_for_creative


def _upload_media_loop(client, creatives, images_dir, videos_dir, media_id_for_creative, plan):
    for cr in creatives:
        name = cr["name"]
        entry = {"creative": name, "image_id": None, "video_id": None}
        fmt = cr.get("format", "") or ""

        if "video" in fmt or "video" in (cr.get("image_or_video", "") or ""):
            source = _video_source(cr, videos_dir / f"{name}.mp4")
            if source is not None:
                try:
                    resp = client.media.upload_video(source)
                    entry["video_id"] = resp.get("id")
                    print(f"  ✓ video {name} ({_describe(source)}) → id={entry['video_id']}")
                except VKAdsError as e:
                    print(f"  ✗ video {name}: {e}", file=sys.stderr)
                    plan["errors"].append(f"video upload {name}: {e}")
            else:
                print(f"  ⏭ video {name}: нет ни файла {videos_dir / f'{name}.mp4'}, ни video_url — skip")
                plan["errors"].append(f"video source missing: {name}")
            media_id_for_creative[name] = entry
            continue

        if "carousel" in fmt:
            sources = _carousel_sources(cr, images_dir, name)
            if not sources:
                print(f"  ⏭ carousel {name}: нет ни картинок в {images_dir}, ни image_urls — skip")
                plan["errors"].append(f"carousel sources missing: {name}")
                media_id_for_creative[name] = entry
                continue
            ids = []
            for src in sources:
                try:
                    resp = client.media.upload_image(src)
                    ids.append(resp.get("id"))
                    print(f"  ✓ {_describe(src)} → id={resp.get('id')}")
                except VKAdsError as e:
                    print(f"  ✗ {_describe(src)}: {e}", file=sys.stderr)
                    plan["errors"].append(f"image upload {_describe(src)}: {e}")
            entry["image_ids"] = ids
            media_id_for_creative[name] = entry
            continue

        source = _image_source(cr, images_dir / f"{name}.png")
        if source is not None:
            try:
                resp = client.media.upload_image(source)
                entry["image_id"] = resp.get("id")
                entry["width"], entry["height"] = resp.get("width"), resp.get("height")
                if resp.get("role"):
                    entry["role"] = resp["role"]
                if resp.get("too_small"):
                    entry["too_small"] = True
                note = (f", ужато {resp['resized_from']} → {entry['width']}x{entry['height']}"
                        if resp.get("resized_from") else "")
                print(f"  ✓ image {name} ({_describe(source)}{note}) → id={entry['image_id']}")
            except VKAdsError as e:
                print(f"  ✗ image {name}: {e}", file=sys.stderr)
                plan["errors"].append(f"image upload {name}: {e}")
        else:
            print(f"  ⏭ image {name}: нет ни файла {images_dir / f'{name}.png'}, ни image_url — skip")
            plan["errors"].append(f"image source missing: {name}")
        media_id_for_creative[name] = entry


# ============ Логотип (роль icon_256x256) ============

LOGO_MISSING_MESSAGE = (
    "Нет логотипа для объявлений (роль icon_256x256): он нужен в каждом объявлении на сайт. "
    "Положите квадратный логотип от 256×256 в assets/brand/logo.png (или assets/images/logo.png) "
    "либо дайте прямую публичную ссылку в brand.json → logo_url. "
    "Логотип уже загружен в VK — его id в brand.json → vk_logo_id (для --skip-media-upload)."
)


def find_logo_source(workspace: Path, brand: dict):
    """Источник логотипа: brand.json logo_url / logo_path → стандартные файлы. Нет — None."""
    if brand.get("logo_url"):
        return brand["logo_url"]
    if brand.get("logo_path"):
        p = Path(brand["logo_path"])
        p = p if p.is_absolute() else workspace / p
        if p.exists():
            return p
    for rel in LOGO_CANDIDATES:
        p = workspace / rel
        if p.exists():
            return p
    return None


def resolve_logo(workspace: Path, skip_media: bool) -> dict:
    """Проверка логотипа ДО любых загрузок: {"vk_logo_id": …} или {"source": …}.

    Нет логотипа — DeployPlanError сразу, пока в VK ещё ничего не ушло.
    """
    brand = load_brand(workspace)
    if skip_media:
        if brand.get("vk_logo_id") is None:
            raise DeployPlanError(LOGO_MISSING_MESSAGE)
        return {"vk_logo_id": brand["vk_logo_id"]}
    source = find_logo_source(workspace, brand)
    if source is None:
        raise DeployPlanError(LOGO_MISSING_MESSAGE)
    return {"source": source}


def upload_logo(client: VKAdsClient, logo: dict, plan: dict):
    """id логотипа в VK. Ошибка загрузки — DeployPlanError (объявления без него не собрать)."""
    if logo.get("vk_logo_id") is not None:
        plan["logo_id"] = logo["vk_logo_id"]
        return logo["vk_logo_id"]
    source = logo["source"]
    try:
        resp = client.media.upload_logo(source)
    except VKAdsError as e:
        raise DeployPlanError(f"Не удалось загрузить логотип ({_describe(source)}): {e}")
    logo_id = resp.get("id")
    if client.dry_run and not logo_id:
        logo_id = "DRY_LOGO"
    plan["logo_id"] = logo_id
    print(f"  ✓ логотип ({_describe(source)}) → id={logo_id}")
    return logo_id


# ============ Объявление: форма по ролям ============

class BannerPayloadError(ValueError):
    """Креатив не собирается в объявление (лимит текста, нет ссылки, неподходящий размер)."""


def _banner_text(cr: dict) -> str:
    """Короткий текст для роли text_90: явное поле text_90 → description."""
    return (cr.get("text_90") or cr.get("description") or "").strip()


def _image_role_for(cr: dict, media: dict) -> str:
    """Роль картинки: явная image_role в креативе → по размеру загруженного → квадрат.

    Размер должен совпадать с ролью точно (600×600 / 1080×607 / 1080×1350): скрипт
    ужимает до него сам, а меньше нужного не растягивает — тогда ошибка.
    """
    if cr.get("image_role"):
        return cr["image_role"]
    w, h = media.get("width"), media.get("height")
    if not w or not h:
        return ROLE_SQUARE  # размер неизвестен (dry-run по ссылке, --skip-media-upload)
    role = media.get("role") or image_role(w, h)
    if role is None:
        raise BannerPayloadError(
            f"{cr.get('name')}: картинка {w}×{h} не подходит ни под одну роль — нужен квадрат 600×600, "
            "16:9 (1080×607) или 4:5 (1080×1350). Обрезать молча не стали: подготовьте картинку "
            "с нужным соотношением сторон."
        )
    tw, th = role_size(role)
    if (w, h) != (tw, th):
        raise BannerPayloadError(
            f"{cr.get('name')}: картинка {w}×{h} — роль {role} принимает ровно {tw}×{th}. "
            f"Меньше нужного не растягиваем: нужен исходник от {tw}×{th}."
        )
    return role


def build_banner_payload(cr: dict, media: dict, url_id=None, logo_id=None) -> Optional[dict]:
    """Собирает объявление по ролям (без campaign_id — его проставит create_campaign_tree).

    Нет медиа — None (объявление пропускается). Текст длиннее лимита роли, нет адреса
    сайта или логотипа — BannerPayloadError с объяснением, что поправить. Ничего не режем.
    """
    name = cr.get("name", "?")
    has_media = (media.get("video_id") is not None or bool(media.get("image_ids"))
                 or media.get("image_id") is not None)
    if not has_media:
        return None

    title = (cr.get("title") or "").strip()
    if not title:
        raise BannerPayloadError(f"{name}: пустой заголовок (роль {TITLE_ROLE}).")
    if len(title) > TITLE_MAX:
        raise BannerPayloadError(
            f"{name}: заголовок {len(title)} знаков, лимит роли {TITLE_ROLE} — {TITLE_MAX}. "
            "Сократите title в creatives.json (для сайта модерация советует до 25)."
        )
    text = _banner_text(cr)
    if not text:
        raise BannerPayloadError(f"{name}: пустой текст (роль {TEXT_ROLE}) — заполните description или text_90.")
    if len(text) > TEXT_MAX:
        raise BannerPayloadError(
            f"{name}: текст {len(text)} знаков, лимит роли {TEXT_ROLE} — {TEXT_MAX}. "
            "Добавьте креативу короткий текст полем text_90 (до 90 знаков) или сократите description."
        )
    if url_id is None:
        raise BannerPayloadError(f"{name}: нет адреса посадочной (url) — объявлению нужна ссылка.")
    if logo_id is None:
        raise BannerPayloadError(f"{name}: {LOGO_MISSING_MESSAGE}")

    textblocks = {
        TITLE_ROLE: {"text": title},
        TEXT_ROLE: {"text": text},
        CTA_ROLE: {"text": cr.get("cta_sites_full") or CTA_DEFAULT},
    }
    # «О компании» (about_company_115) — необязательная роль шаблонов сайта; по умолчанию НЕ шлём:
    # данные ОРД VK берёт из настроек кабинета. Только по явному полю в креативе.
    about = (cr.get(ABOUT_ROLE) or "").strip()
    if about:
        if len(about) > ABOUT_MAX:
            raise BannerPayloadError(
                f"{name}: «О компании» {len(about)} знаков, лимит роли {ABOUT_ROLE} — {ABOUT_MAX}.")
        textblocks[ABOUT_ROLE] = {"text": about}

    payload = {
        "name": name,
        "urls": {"primary": {"id": url_id}},
        "textblocks": textblocks,
        "content": {LOGO_ROLE: {"id": logo_id}},
    }
    if media.get("video_id") is not None:
        # НЕ ПРОВЕРЕНО: роль видео в content не подтверждена — оставлена старая форма.
        payload["videos"] = [{"id": media["video_id"]}]
    elif media.get("image_ids"):
        # НЕ ПРОВЕРЕНО: роли карточек карусели не подтверждены — оставлена старая форма.
        payload["images"] = [{"id": i} for i in media["image_ids"]]
    else:
        payload["content"][_image_role_for(cr, media)] = {"id": media["image_id"]}
    return payload


def verify_banners(client: VKAdsClient, group_ids: list) -> list:
    """Проверка после залива: в каждой группе есть объявления.

    Смотрим список объявлений группы (GET /banners.json?campaign_id=<id группы>),
    а не поле `banners` у группы: у группы на паузе оно всегда пустое, и issue
    NO_BANNERS_WITH_ACTIVE_STATUS у неё тоже ожидаем. Провал — только пустой список.
    Возвращает проблемы словами.
    """
    problems = []
    for gid in group_ids:
        try:
            items = client.banners.list(campaign_id=gid, fields="id,name,status,campaign_id")
        except VKAdsError as e:
            problems.append(f"группа {gid}: не смог прочитать объявления ({e})")
            continue
        # Защита: если VK проигнорировал фильтр _campaign_id, чужие объявления не считаем.
        own = only_children(items, "campaign_id", gid)
        if not own:
            extra = f" (пришло {len(items)} чужих объявлений — фильтр не сработал)" if items else ""
            problems.append(f"группа {gid}: объявлений нет — залив креативов провалился{extra}")
    return problems


# ============ Main deploy flow ============

class DeployPlanError(Exception):
    pass


def deploy(workspace: Path, dry_run: bool, skip_media: bool,
           confirm_large_budget: bool = False) -> dict:
    creatives = load_creatives(workspace)
    audiences = load_audiences(workspace)
    ad_plan_cfg = load_ad_plan_config(workspace)

    if not creatives:
        raise DeployPlanError(f"Не найден creatives.json или он пустой: {workspace}")
    if not audiences:
        raise DeployPlanError(f"Не найден audiences.json или он пустой: {workspace}")

    # Единицы: бюджет в копейках из 0.7.0 ловим по _state.json и по группам (они всегда в рублях)
    group_daily = [a.get("daily_budget_rub", 500) for a in audiences]
    try:
        check_kopeck_units(ad_plan_cfg, load_json(workspace / "_state.json", default={}) or {},
                           group_daily)
    except BudgetGuardError as e:
        raise DeployPlanError(str(e))

    # Защита от ошибки единиц: большие суммы — только с явным флагом (и в dry-run тоже)
    for what, daily, total in (
        [("кампания", ad_plan_cfg.get("budget_limit_day"), ad_plan_cfg.get("budget_limit"))]
        + [(f"группа {a.get('id', '?')}", a.get("daily_budget_rub", 500), None) for a in audiences]
    ):
        try:
            check_budget_guard(daily_rub=daily, total_rub=total, confirmed=confirm_large_budget)
        except BudgetGuardError as e:
            raise DeployPlanError(f"{what}: {e}")

    # Бюджеты в рублях — для «бюджет X — ОК?» (dry-run и боевой запуск)
    groups_sum = sum(budget_rub(g) for g in group_daily)
    print("Бюджет кампании (в рублях): "
          f"общий {fmt_rub(ad_plan_cfg['budget_limit']) if ad_plan_cfg.get('budget_limit') is not None else 'не задан'}, "
          f"дневной {fmt_rub(ad_plan_cfg['budget_limit_day']) if ad_plan_cfg.get('budget_limit_day') is not None else 'не задан'}; "
          f"дневные бюджеты групп: {fmt_rub(groups_sum)} ({len(group_daily)} гр.)")

    # Проверки до любых вызовов VK: логотип и пакет размещения.
    logo = resolve_logo(workspace, skip_media)
    state = load_json(workspace / "_state.json", default={}) or {}
    package_id = ad_plan_cfg.pop("package_id", None) or state.get("package_id")
    if package_id is None and not all(a.get("package_id") for a in audiences):
        msg = ("нет package_id: без пакета размещения VK не создаст группу. Выберите пакет "
               "(SKILL.md, Шаг 11, путь B: `python -m scripts.vk_ads_api packages --objective <цель>`; "
               "без пикселя — оплата за клики, priced_event_type 1) и впишите package_id "
               "в creatives.json[campaign]")
        if not dry_run:
            raise DeployPlanError(msg)
        print(f"⚠️ {msg}")

    if dry_run:
        client = VKAdsClient(access_token="DRY_RUN_TOKEN", dry_run=True)
        print("=== DRY-RUN ===")
    else:
        try:
            client = VKAdsClient.from_credentials(dry_run=False)
        except CredentialNotFound as e:
            raise DeployPlanError(f"Нужен ключ VK Ads:\n{e}")

    plan = {
        "workspace": str(workspace),
        "started_at": _utc_now().isoformat(),
        "dry_run": dry_run,
        "media_uploaded": [],
        "logo_id": None,
        "ad_plan": None,
        "groups_created": [],
        "banners_created": [],
        "errors": [],
        "warnings": [],
        "kabinet_url": None,
    }
    try:
        _deploy_steps(workspace, plan, client, creatives, audiences, ad_plan_cfg, package_id,
                      logo, dry_run, skip_media)
    except DeployPlanError as e:
        plan["errors"].append(f"ОСТАНОВЛЕНО: {e}")
        raise
    finally:
        # Сохраняем всегда: id загруженного контента и созданной кампании нельзя терять.
        _save_plan(workspace, plan, dry_run)
    return plan


def _deploy_steps(workspace, plan, client, creatives, audiences, ad_plan_cfg, package_id,
                  logo, dry_run, skip_media):
    print("⚠️ ОРД: в кабинете должны быть заполнены данные рекламодателя (ИНН и название), "
          "иначе VK отклонит объявления с ord_data_required.")
    if package_id is None and not all(a.get("package_id") for a in audiences):
        plan["warnings"].append("нет package_id — в боевом запуске залив остановится до создания кампании")

    # Pre-flight подключения + live-сверка схемы
    if not dry_run:
        try:
            existing = client.ad_plans.list(limit=1)
            print(f"✓ VK Ads API отвечает (ad_plans доступны: {len(existing)} в выборке)")
        except VKAdsError as e:
            raise DeployPlanError(f"Не могу подключиться к VK Ads API: {e}")
        try:
            schema = client.probe_schema()
            print("  Live-схема полей:", json.dumps(schema, ensure_ascii=False))
            plan["probed_schema"] = schema
        except VKAdsError as e:
            print(f"  (probe-schema пропущен: {e})")

    # Шаг 1: медиа + логотип (наличие логотипа проверено до загрузок)
    media_id_for_creative = upload_media(client, creatives, workspace, skip_media, plan)
    logo_id = upload_logo(client, logo, plan)

    # Шаг 2: собрать группы (campaigns) из аудиторий + объявления в каждую
    print("\n--- Шаг 2/4: сборка групп и объявлений ---")
    groups = []
    for aud in audiences:
        aud_id = aud["id"]
        group_payload = {
            "name": f"{aud_id} — {aud.get('name', 'unnamed')}",
            "targetings": audience_to_targeting(aud),
            "placements": ["vk_feed", "vk_clips", "ok_feed"],
            "budget_limit_day": budget_rub(aud.get("daily_budget_rub", 500)),
        }
        if aud.get("package_id") or package_id is not None:
            group_payload["package_id"] = aud.get("package_id") or package_id
        banners = []
        for cr in creatives:
            target_aud = (cr.get("ad_plan_id", "") or cr.get("audience", "")).split("+")[0].strip()
            if target_aud and target_aud != aud_id:
                continue
            media = media_id_for_creative.get(cr["name"], {})
            if media.get("video_id") is not None or media.get("image_ids"):
                _warn_once(plan, f"объявление {cr['name']}: видео/карусель уходят в старой форме — "
                                 "форма по ролям для них не реализована (роли видео — banner_patterns "
                                 "321–324), проверьте объявление в кабинете")
            if cr.get(ABOUT_ROLE):
                _warn_once(plan, f"объявление {cr['name']}: передаётся необязательная роль {ABOUT_ROLE} — "
                                 "по умолчанию её не шлём (ОРД берётся из кабинета); если VK отклонит "
                                 "группу, уберите поле из креатива")
            try:
                raw_url = (cr.get("url") or "").strip()
                url_id = client.urls.register(raw_url) if raw_url else None
                b_payload = build_banner_payload(cr, media, url_id=url_id, logo_id=logo_id)
            except OrdDataRequiredError:
                raise DeployPlanError(ORD_DATA_REQUIRED_MESSAGE + " В кабинете ещё ничего не создано.")
            except (BannerPayloadError, VKAdsError) as e:
                print(f"  ✗ объявление {cr['name']}: {e}", file=sys.stderr)
                plan["errors"].append(f"banner {cr['name']}: {e}")
                continue
            if b_payload is None:
                plan["errors"].append(f"banner {cr['name']}: нет медиа — skip")
                continue
            for role in UNVERIFIED_ROLES & set(b_payload.get("content", {})):
                _warn_once(plan, f"роль {role} (объявление {cr['name']}) есть в banner_patterns, но "
                                 "объявление с ней вживую не создавалось; проверьте объявление в кабинете")
            banners.append(b_payload)
        if not banners:
            print(f"  ⏭ группа {aud_id}: нет объявлений с медиа — пропускаю группу")
            plan["errors"].append(f"группа {aud_id}: нет объявлений")
            continue
        groups.append({"audience_id": aud_id, "payload": group_payload, "banners": banners})
        print(f"  • группа {aud_id}: {len(banners)} объявл., бюджет {aud.get('daily_budget_rub')} ₽/день")

    if not groups:
        raise DeployPlanError("Ни одной группы с объявлениями не собрано (нет медиа?). Прерываю.")

    # Шаг 3: создание дерева ad_plan → campaigns → banners
    print("\n--- Шаг 3/4: создание дерева (ad_plan → группы → объявления), всё в blocked ---")
    ad_plan_payload = dict(ad_plan_cfg)
    ad_plan_payload["status"] = "blocked"
    try:
        tree = client.create_campaign_tree(
            ad_plan_payload,
            [{"payload": g["payload"], "banners": g["banners"]} for g in groups],
        )
    except OrdDataRequiredError as e:
        created_id = getattr(e, "ad_plan_id", None)
        groups_done = getattr(e, "created_group_ids", []) or []
        if created_id:
            plan["ad_plan"] = {"id": created_id, "empty": not groups_done}
            plan["groups_created"] = [{"campaign_id": g} for g in groups_done]
            tail = (f" Кампания {created_id} создана пустой, на паузе — удалите её в кабинете вручную "
                    f"или заполните ОРД и повторите залив.")
            if groups_done:
                tail = (f" Кампания {created_id} создана на паузе, в ней группы {groups_done} — "
                        f"удалите кампанию в кабинете вручную или заполните ОРД и повторите залив.")
        else:
            tail = " В кабинете ничего не создано."
        print(f"  ✗ {ORD_DATA_REQUIRED_MESSAGE}{tail}", file=sys.stderr)
        raise DeployPlanError(ORD_DATA_REQUIRED_MESSAGE + tail)
    except VKAdsError as e:
        raise DeployPlanError(f"Не удалось создать дерево кампании: {e}")

    ad_plan_id = tree["ad_plan"]["id"] if tree.get("ad_plan") else 0
    plan["ad_plan"] = tree.get("ad_plan")
    for gi, grp in enumerate(tree.get("groups", [])):
        aud_id = groups[gi]["audience_id"] if gi < len(groups) else "?"
        plan["groups_created"].append({"audience_id": aud_id, "campaign_id": grp["id"], "name": grp["payload"].get("name")})
        print(f"  ✓ группа id={grp['id']} '{grp['payload'].get('name')}' ({len(grp['banners'])} объявл.)")
        for b in grp["banners"]:
            plan["banners_created"].append({"banner_id": b["id"], "campaign_id": grp["id"]})
    plan["errors"].extend(tree.get("errors", []))

    # Шаг 4: проверка объявлений + финал
    print("\n--- Шаг 4/4: проверка и финал ---")
    if not dry_run:
        problems = verify_banners(client, [g["campaign_id"] for g in plan["groups_created"]])
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        plan["errors"].extend(problems)
    if not dry_run and ad_plan_id:
        plan["kabinet_url"] = f"https://ads.vk.ru/hq/dashboard/ad_plans?id={ad_plan_id}"
        print(f"  Кампания создана (blocked): {plan['kabinet_url']}")
    elif dry_run:
        print("  [DRY-RUN] — ничего не создано в кабинете")


def _warn_once(plan: dict, msg: str) -> None:
    if msg not in plan["warnings"]:
        plan["warnings"].append(msg)


def _save_plan(workspace: Path, plan: dict, dry_run: bool) -> None:
    plan["finished_at"] = _utc_now().isoformat()
    plan_path = workspace / "assets" / "deploy_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"\n  План сохранён: {plan_path}")

    ad_plan_id = (plan.get("ad_plan") or {}).get("id")
    log_path = workspace / "operations_log.md"
    log_entry = (
        f"\n## {_utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC — deploy_campaign\n"
        f"**Mode:** {'DRY-RUN' if dry_run else 'REAL'}\n"
        f"**Ad plan (кампания) id:** {ad_plan_id if (ad_plan_id and not dry_run) else '—'}\n"
        f"**Группы:** {len(plan['groups_created'])}\n"
        f"**Объявления:** {len(plan['banners_created'])}\n"
        f"**Errors:** {len(plan['errors'])}\n"
        f"**Kabinet:** {plan.get('kabinet_url') or '—'}\n"
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(log_entry)
    print(f"  Лог обновлён: {log_path}")


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="M4: end-to-end залив кампании в VK Реклама")
    parser.add_argument("--workspace", required=True, help="Папка vk-campaign-<slug>/")
    parser.add_argument("--dry-run", action="store_true", help="Только план, ничего не создавать")
    parser.add_argument(
        "--skip-media-upload",
        action="store_true",
        help="Не загружать медиа, использовать vk_image_id/vk_video_id из creatives.json",
    )
    parser.add_argument(
        "--confirm-large-budget",
        action="store_true",
        help="Разрешить дневной бюджет выше 100 000 ₽ или общий выше 10 000 000 ₽ "
             "(защита от ошибки единиц)",
    )
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    try:
        plan = deploy(workspace, dry_run=args.dry_run, skip_media=args.skip_media_upload,
                      confirm_large_budget=args.confirm_large_budget)
    except DeployPlanError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    print("\n=== Сводка ===")
    print(f"  Загружено медиа: {len(plan['media_uploaded'])}")
    print(f"  Создано групп: {len(plan['groups_created'])}")
    print(f"  Создано объявлений: {len(plan['banners_created'])}")
    print(f"  Ошибок: {len(plan['errors'])}")

    if plan.get("warnings"):
        print("\n⚠ Предупреждения:")
        for w in plan["warnings"]:
            print(f"  - {w}")

    if plan["errors"]:
        print("\n⚠ Ошибки при заливе:")
        for err in plan["errors"]:
            print(f"  - {err}")

    if not args.dry_run and plan.get("kabinet_url"):
        print(f"\n✅ Открой в кабинете: {plan['kabinet_url']}")
        print("   Проверь чек-лист Сценария 10 + ОРД (company_info/ERID) → активируй только вручную по «Запустить».")

    return 0 if not plan["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
