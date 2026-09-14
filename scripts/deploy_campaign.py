#!/usr/bin/env python3
"""
deploy_campaign.py — финальный модуль M4. Читает рабочую папку кампании и
заливает всё в кабинет VK Реклама через API в ПРАВИЛЬНОЙ иерархии:

    media → ad_plan (кампания) → campaigns (группы) → banners (объявления)

Связи проставляются автоматически (группа.ad_plan_id, объявление.campaign_id),
поэтому orphan-групп не возникает. Все объекты создаются в status=blocked;
активация НИКОГДА не выполняется здесь (только вручную после чек-листа + ОРД).

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
- M banners (Объявления, status=blocked) — привязаны к группе через campaign_id

Что НЕ делает:
- Не активирует (status=active никогда не ставится тут)
- Не создаёт CRM-list / lookalike (нужен source)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from scripts._console import setup_console
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console

try:
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.vk_ads_api import VKAdsClient, VKAdsError
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import load_api_key, CredentialNotFound
    from scripts.vk_ads_api import VKAdsClient, VKAdsError


# ============ Helpers ============

def rub_to_kopecks(rub: float) -> int:
    return int(round(rub * 100))


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


def load_ad_plan_config(workspace: Path) -> dict:
    """Конфиг КАМПАНИИ (ad_plan): цель + общий бюджет. Из creatives.json[campaign] или _state.json."""
    creatives_data = load_json(workspace / "creatives.json", default={})
    if isinstance(creatives_data, dict) and "campaign" in creatives_data:
        cfg = dict(creatives_data["campaign"])
        cfg.setdefault("status", "blocked")
        return cfg
    state = load_json(workspace / "_state.json", default={})
    return {
        "name": f"{state.get('brand_name_ru', 'Campaign')} — VK тест",
        "objective": state.get("objective", "conversions"),
        "status": "blocked",
        "budget_limit": rub_to_kopecks(state.get("budget_rub", 50000)),
        "budget_limit_day": rub_to_kopecks(state.get("daily_budget_rub", 3500)),
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
        targeting["interests"] = cfg["interests"]
    if "additional_interests" in cfg:
        targeting["interest_categories"] = cfg["additional_interests"]
    if "position_filter" in cfg:
        targeting["positions"] = cfg["position_filter"]
    return targeting


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


def _vk_format_for(creative: dict) -> str:
    fmt = (creative.get("format", "") or "").lower()
    if "video" in fmt or "video" in (creative.get("image_or_video", "") or ""):
        return "universal"
    if "carousel" in fmt:
        return "carousel"
    return "universal"


# ============ Media upload ============

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
    for cr in creatives:
        name = cr["name"]
        entry = {"creative": name, "image_id": None, "video_id": None}
        fmt = cr.get("format", "") or ""

        if "video" in fmt or "video" in (cr.get("image_or_video", "") or ""):
            video_path = videos_dir / f"{name}.mp4"
            if video_path.exists():
                try:
                    resp = client.media.upload_video(video_path)
                    entry["video_id"] = resp.get("id")
                    print(f"  ✓ video {name} → id={entry['video_id']}")
                except VKAdsError as e:
                    print(f"  ✗ video {name}: {e}", file=sys.stderr)
                    plan["errors"].append(f"video upload {name}: {e}")
            else:
                print(f"  ⏭ video {name}: файл {video_path} не найден — skip")
                plan["errors"].append(f"video file missing: {video_path}")
            media_id_for_creative[name] = entry
            continue

        if "carousel" in fmt:
            card_images = sorted(images_dir.glob(f"{name}_card*.png"))
            if not card_images:
                single = images_dir / f"{name}.png"
                if single.exists():
                    card_images = [single]
            if not card_images:
                print(f"  ⏭ carousel {name}: нет картинок в {images_dir} — skip")
                plan["errors"].append(f"carousel images missing: {name}")
                media_id_for_creative[name] = entry
                continue
            ids = []
            for ci in card_images:
                try:
                    resp = client.media.upload_image(ci)
                    ids.append(resp.get("id"))
                    print(f"  ✓ {ci.name} → id={resp.get('id')}")
                except VKAdsError as e:
                    print(f"  ✗ {ci.name}: {e}", file=sys.stderr)
                    plan["errors"].append(f"image upload {ci.name}: {e}")
            entry["image_ids"] = ids
            media_id_for_creative[name] = entry
            continue

        image_path = images_dir / f"{name}.png"
        if image_path.exists():
            try:
                resp = client.media.upload_image(image_path)
                entry["image_id"] = resp.get("id")
                print(f"  ✓ image {name} → id={entry['image_id']}")
            except VKAdsError as e:
                print(f"  ✗ image {name}: {e}", file=sys.stderr)
                plan["errors"].append(f"image upload {name}: {e}")
        else:
            print(f"  ⏭ image {name}: файл {image_path} не найден — skip")
            plan["errors"].append(f"image file missing: {image_path}")
        media_id_for_creative[name] = entry

    plan["media_uploaded"] = [{"creative": k, **v} for k, v in media_id_for_creative.items()]
    return media_id_for_creative


def build_banner_payload(cr: dict, media: dict) -> Optional[dict]:
    """Собирает payload объявления (без campaign_id — его проставит create_campaign_tree)."""
    payload = {
        "format": _vk_format_for(cr),
        "title": cr.get("title", ""),           # не режем молча: лимит зависит от объекта (сайт 25 / прочее 40)
        "description": cr.get("description", ""),
        "url": cr.get("url", ""),
        "call_to_action": cr.get("call_to_action", "learn_more"),
        "company_info": cr.get("company_info", ""),  # ОРД: юр.данные (пусто допустимо только для услуг/офлайн)
    }
    if media.get("video_id") is not None:
        payload["videos"] = [{"id": media["video_id"]}]
    elif media.get("image_ids"):
        payload["images"] = [{"id": i} for i in media["image_ids"]]
    elif media.get("image_id") is not None:
        payload["images"] = [{"id": media["image_id"]}]
    else:
        return None  # нет медиа — пропускаем
    return payload


# ============ Main deploy flow ============

class DeployPlanError(Exception):
    pass


def deploy(workspace: Path, dry_run: bool, skip_media: bool) -> dict:
    creatives = load_creatives(workspace)
    audiences = load_audiences(workspace)
    ad_plan_cfg = load_ad_plan_config(workspace)

    if not creatives:
        raise DeployPlanError(f"Не найден creatives.json или он пустой: {workspace}")
    if not audiences:
        raise DeployPlanError(f"Не найден audiences.json или он пустой: {workspace}")

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
        "started_at": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "media_uploaded": [],
        "ad_plan": None,
        "groups_created": [],
        "banners_created": [],
        "errors": [],
        "kabinet_url": None,
    }

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

    # Шаг 1: медиа
    media_id_for_creative = upload_media(client, creatives, workspace, skip_media, plan)

    # Шаг 2: собрать группы (campaigns) из аудиторий + объявления в каждую
    print("\n--- Шаг 2/4: сборка групп и объявлений ---")
    groups = []
    for aud in audiences:
        aud_id = aud["id"]
        group_payload = {
            "name": f"{aud_id} — {aud.get('name', 'unnamed')}",
            "targetings": audience_to_targeting(aud),
            "placements": ["vk_feed", "vk_clips", "ok_feed"],
            "budget_limit_day": rub_to_kopecks(aud.get("daily_budget_rub", 500)),
        }
        banners = []
        for cr in creatives:
            target_aud = (cr.get("ad_plan_id", "") or cr.get("audience", "")).split("+")[0].strip()
            if target_aud and target_aud != aud_id:
                continue
            media = media_id_for_creative.get(cr["name"], {})
            b_payload = build_banner_payload(cr, media)
            if b_payload is None:
                plan["errors"].append(f"banner {cr['name']}: нет медиа — skip")
                continue
            banners.append(b_payload)
        if not banners:
            print(f"  ⏭ группа {aud_id}: нет объявлений с медиа — пропускаю группу")
            plan["errors"].append(f"группа {aud_id}: нет объявлений")
            continue
        groups.append({"audience_id": aud_id, "payload": group_payload, "banners": banners})
        print(f"  • группа {aud_id}: {len(banners)} объявл., бюджет {aud.get('daily_budget_rub')} ₽/день")

    if not groups:
        raise DeployPlanError("Ни одной группы с объявлениями не собрано (нет медиа?). Прерываю.")

    # Шаг 3: атомарное создание дерева ad_plan → campaigns → banners
    print("\n--- Шаг 3/4: создание дерева (ad_plan → группы → объявления), всё в blocked ---")
    ad_plan_payload = dict(ad_plan_cfg)
    ad_plan_payload["status"] = "blocked"
    try:
        tree = client.create_campaign_tree(
            ad_plan_payload,
            [{"payload": g["payload"], "banners": g["banners"]} for g in groups],
        )
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

    # Шаг 4: финал
    print("\n--- Шаг 4/4: финал ---")
    if not dry_run and ad_plan_id:
        plan["kabinet_url"] = f"https://ads.vk.ru/hq/dashboard/ad_plans?id={ad_plan_id}"
        print(f"  Кампания создана (blocked): {plan['kabinet_url']}")
    elif dry_run:
        print("  [DRY-RUN] — ничего не создано в кабинете")

    plan["finished_at"] = datetime.utcnow().isoformat()

    plan_path = workspace / "assets" / "deploy_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"\n  План сохранён: {plan_path}")

    log_path = workspace / "operations_log.md"
    log_entry = (
        f"\n## {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC — deploy_campaign\n"
        f"**Mode:** {'DRY-RUN' if dry_run else 'REAL'}\n"
        f"**Ad plan (кампания) id:** {ad_plan_id if not dry_run else '—'}\n"
        f"**Группы:** {len(plan['groups_created'])}\n"
        f"**Объявления:** {len(plan['banners_created'])}\n"
        f"**Errors:** {len(plan['errors'])}\n"
        f"**Kabinet:** {plan.get('kabinet_url') or '—'}\n"
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(log_entry)
    print(f"  Лог обновлён: {log_path}")

    return plan


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
    args = parser.parse_args()

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    try:
        plan = deploy(workspace, dry_run=args.dry_run, skip_media=args.skip_media_upload)
    except DeployPlanError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    print("\n=== Сводка ===")
    print(f"  Загружено медиа: {len(plan['media_uploaded'])}")
    print(f"  Создано групп: {len(plan['groups_created'])}")
    print(f"  Создано объявлений: {len(plan['banners_created'])}")
    print(f"  Ошибок: {len(plan['errors'])}")

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
