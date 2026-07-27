#!/usr/bin/env python3
"""
vk_ads_api.py — REST-клиент к VK Ads API (VK Реклама, наследник myTarget).

ИЕРАРХИЯ СУЩНОСТЕЙ (важно — API-имена не совпадают с UI):

    UI ads.vk.ru        API-ресурс      родитель
    -----------------   -------------   ----------------------
    Кампания (верх)     ad_plan         — (верхний уровень)
    Группа объявлений   campaign        ad_plan_id
    Объявление          banner          campaign_id

Правильная цепочка создания:  ad_plan → campaign(ad_plan_id) → banner(campaign_id).
- `campaign` без `ad_plan_id` создаётся, но становится ORPHAN — невидим в новом UI.
- `banner` привязывается к `campaign_id` (к ГРУППЕ), а НЕ к `ad_plan_id`.

Используй `client.create_campaign_tree(...)` для атомарного корректного создания —
он гарантирует правильные связи и статус `blocked` на всех уровнях.

⚠️ Имена полей/эндпоинтов наследованы от myTarget и могли измениться. ПЕРЕД массовым
созданием вызывай `client.probe_schema()` — он делает GET одного реального объекта
каждого уровня и показывает фактические имена полей (снимает риск 400 на догадках).

Поддерживает:
- OAuth2 (Client Credentials Grant) + refresh token
- Read-only операции (audit, statistics)
- Write-операции с dry-run и обязательным blocked при создании
- ОРД-preflight: активация запрещена, если у объявлений не заполнен company_info

Использование:
    python -m scripts.vk_ads_api audit                 # список кампаний (ad_plans)
    python -m scripts.vk_ads_api stats --days 7
    python -m scripts.vk_ads_api probe-schema           # сверить имена полей с кабинетом
    python -m scripts.vk_ads_api campaign-update --id 123 --budget 5000 --dry-run

    from scripts.vk_ads_api import VKAdsClient
    client = VKAdsClient.from_credentials()
    plans = client.ad_plans.list()

ENV / секреты:
    VK_ADS_ACCESS_TOKEN            (если токен уже есть)
    VK_ADS_CLIENT_ID / VK_ADS_CLIENT_SECRET
    Секрет держать в .env или в системе credentials скилла, НЕ в переписке.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Any

try:
    import requests
except ImportError:
    print("ERROR: модуль requests не установлен. pip install requests", file=sys.stderr)
    sys.exit(1)


BASE_URL = "https://ads.vk.com/api/v2"
TOKEN_URL = f"{BASE_URL}/oauth2/token.json"

# CTA дефолт и прочие константы можно расширять
ACTIVATE_CONFIRMATION = "YES_ACTIVATE"


class VKAdsError(Exception):
    pass


class OrdPreflightError(VKAdsError):
    """Активация заблокирована: не пройдена проверка ОРД-маркировки."""


class VKAdsClient:
    def __init__(self, access_token: str, dry_run: bool = False):
        self.access_token = access_token
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

        # Подресурсы. Имена соответствуют API-ресурсам (не UI):
        self.ad_plans = AdPlansAPI(self)      # = Кампания (верхний уровень)
        self.campaigns = CampaignsAPI(self)   # = Группа объявлений (child of ad_plan)
        self.banners = BannersAPI(self)       # = Объявление (child of campaign)
        self.audiences = AudiencesAPI(self)
        self.statistics = StatisticsAPI(self)
        self.media = MediaAPI(self)

    # ---------- Конструкторы ----------

    @classmethod
    def from_credentials(cls, dry_run: bool = False) -> "VKAdsClient":
        """Создаёт клиента через систему credentials скилла (env → ~/.vk-ads-manager/credentials.json)."""
        try:
            from scripts.credentials import load_api_key, CredentialNotFound
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from scripts.credentials import load_api_key, CredentialNotFound
        token = load_api_key("vk_ads")
        return cls(access_token=token, dry_run=dry_run)

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "VKAdsClient":
        """Создаёт клиента из env-переменных / .env. Секрет НЕ должен попадать в чат."""
        # Подхватим .env из cwd или из папки скилла, если есть (без внешних зависимостей)
        _load_dotenv_if_present()

        token = os.environ.get("VK_ADS_ACCESS_TOKEN")
        if token:
            return cls(access_token=token, dry_run=dry_run)

        client_id = os.environ.get("VK_ADS_CLIENT_ID")
        client_secret = os.environ.get("VK_ADS_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise VKAdsError(
                "Нужны env VK_ADS_ACCESS_TOKEN или (VK_ADS_CLIENT_ID + VK_ADS_CLIENT_SECRET). "
                "Положи их в .env (не в переписку) или сохрани через "
                "`python -m scripts.manage_credentials set vk_ads` и используй from_credentials()."
            )

        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": os.environ.get("VK_ADS_SCOPE", ""),  # scope=offline → бессрочный
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return cls(access_token=data["access_token"], dry_run=dry_run)

    # ---------- Низкоуровневые запросы ----------

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{BASE_URL}{path}"
        if self.dry_run and method.upper() in ("POST", "PUT", "DELETE"):
            print(f"[DRY-RUN] {method.upper()} {url}")
            if "json" in kwargs:
                print(f"  payload: {json.dumps(kwargs['json'], ensure_ascii=False, indent=2)}")
            return {"dry_run": True}

        for attempt in range(3):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code >= 500:
                    time.sleep(1)
                    continue
                if not resp.ok:
                    try:
                        err = resp.json()
                    except Exception:
                        err = {"raw": resp.text}
                    raise VKAdsError(f"HTTP {resp.status_code}: {err}")
                return resp.json() if resp.content else {}
            except requests.RequestException as e:
                if attempt == 2:
                    raise VKAdsError(f"Request failed: {e}")
                time.sleep(1)
        raise VKAdsError("Retries exhausted")

    def upload(self, path: str, files: dict, timeout: int = 300) -> Any:
        """Multipart upload — для загрузки картинок и видео."""
        url = f"{BASE_URL}{path}"

        if self.dry_run:
            file_info = []
            for k, (name, _, mime) in files.items():
                file_info.append(f"{k}={name} ({mime})")
            print(f"[DRY-RUN] POST (multipart) {url}")
            print(f"  files: {', '.join(file_info)}")
            return {"dry_run": True, "id": 0}

        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        for attempt in range(3):
            try:
                resp = requests.post(url, files=files, headers=headers, timeout=timeout)
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code >= 500:
                    time.sleep(2)
                    continue
                if not resp.ok:
                    try:
                        err = resp.json()
                    except Exception:
                        err = {"raw": resp.text}
                    raise VKAdsError(f"Upload HTTP {resp.status_code}: {err}")
                return resp.json() if resp.content else {}
            except requests.RequestException as e:
                if attempt == 2:
                    raise VKAdsError(f"Upload failed: {e}")
                time.sleep(2)
        raise VKAdsError("Upload retries exhausted")

    # ---------- Высокоуровневые операции ----------

    def probe_schema(self) -> dict:
        """Live-верификация схемы: GET одного реального объекта каждого уровня.

        Возвращает фактические имена полей, которые сейчас отдаёт кабинет —
        чтобы не строить payload на догадках и не ловить 400. Вызывать перед
        первым массовым созданием в незнакомом кабинете.
        """
        out = {}
        try:
            plans = self.ad_plans.list(limit=1)
            out["ad_plan_fields"] = sorted(plans[0].keys()) if plans else "нет ad_plans в кабинете"
        except VKAdsError as e:
            out["ad_plan_fields"] = f"ошибка: {e}"
        try:
            groups = self.campaigns.list(limit=1)
            out["campaign_fields"] = sorted(groups[0].keys()) if groups else "нет campaigns в кабинете"
        except VKAdsError as e:
            out["campaign_fields"] = f"ошибка: {e}"
        try:
            banners = self.banners.list(limit=1)
            out["banner_fields"] = sorted(banners[0].keys()) if banners else "нет banners в кабинете"
        except VKAdsError as e:
            out["banner_fields"] = f"ошибка: {e}"
        return out

    def create_campaign_tree(self, ad_plan_payload: dict, groups: list) -> dict:
        """Атомарно (по шагам) создаёт корректное дерево ad_plan → campaigns → banners.

        Все объекты создаются в status=blocked. Связи проставляются автоматически:
        каждая группа получает ad_plan_id, каждый баннер — campaign_id своей группы.
        Это устраняет orphan-группы.

        groups = [
            {
              "payload": {campaign(=группа) payload без ad_plan_id},
              "banners": [ {banner payload без campaign_id}, ... ]
            }, ...
        ]

        Возвращает структуру с созданными id и ошибками.
        """
        result = {"ad_plan": None, "groups": [], "errors": []}

        # 1) Кампания (ad_plan) — верхний уровень
        plan = dict(ad_plan_payload)
        plan["status"] = "blocked"
        ap_resp = self.ad_plans.create(plan)
        # В dry-run реальных id нет — подставляем плейсхолдеры, чтобы связи были валидны
        ad_plan_id = ap_resp.get("id") or ("DRY_AD_PLAN" if self.dry_run else 0)
        result["ad_plan"] = {"id": ad_plan_id, "payload": plan}

        # 2) Группы (campaigns) с ad_plan_id
        for gi, g in enumerate(groups):
            g_payload = dict(g.get("payload", {}))
            g_payload["ad_plan_id"] = ad_plan_id
            g_payload["status"] = "blocked"
            try:
                g_resp = self.campaigns.create(g_payload)
            except VKAdsError as e:
                result["errors"].append(f"группа '{g_payload.get('name')}': {e}")
                continue
            group_id = g_resp.get("id") or (f"DRY_GROUP_{gi}" if self.dry_run else 0)
            group_rec = {"id": group_id, "payload": g_payload, "banners": []}

            # 3) Объявления (banners) с campaign_id = id группы
            for b in g.get("banners", []):
                b_payload = dict(b)
                b_payload["campaign_id"] = group_id
                b_payload["status"] = "blocked"
                try:
                    b_resp = self.banners.create(b_payload)
                    group_rec["banners"].append({"id": b_resp.get("id", 0), "payload": b_payload})
                except VKAdsError as e:
                    result["errors"].append(f"объявление в группе {group_id}: {e}")
            result["groups"].append(group_rec)

        return result

    def preflight_ord(self, ad_plan_id: int) -> dict:
        """Проверяет ОРД-готовность перед активацией: у объявлений должен быть
        заполнен company_info (юр.данные для маркировки), иначе ОРД отклонит и ERID
        не будет присвоен. Возвращает {'ok': bool, 'banners_without_company_info': [...]}.
        """
        problems = []
        groups = self.campaigns.list(ad_plan_id=ad_plan_id)
        for g in groups:
            gid = g.get("id")
            for b in self.banners.list(campaign_id=gid):
                if not (b.get("company_info") or "").strip():
                    problems.append(b.get("id"))
        return {"ok": not problems, "banners_without_company_info": problems}

    def activate_ad_plan(
        self,
        ad_plan_id: int,
        confirmation: str = "",
        allow_missing_ord: bool = False,
    ) -> dict:
        """Активация кампании (ad_plan) с двумя защитами:
        1) явное confirmation='YES_ACTIVATE' (от случайного запуска);
        2) ОРД-preflight — блокирует активацию, если у объявлений нет company_info.
        """
        if confirmation != ACTIVATE_CONFIRMATION:
            raise VKAdsError(
                f"Для активации передай confirmation='{ACTIVATE_CONFIRMATION}'. "
                "Активация запускает трату бюджета — делается только по явному «запускай»."
            )
        if not allow_missing_ord and not self.dry_run:
            check = self.preflight_ord(ad_plan_id)
            if not check["ok"]:
                raise OrdPreflightError(
                    "Активация заблокирована ОРД-preflight: не заполнен company_info у объявлений "
                    f"{check['banners_without_company_info']}. Заполни юр.данные (или явно передай "
                    "allow_missing_ord=True, если реклама без онлайн-продажи товаров и поле должно быть пустым)."
                )
        return self.ad_plans.update(ad_plan_id, {"status": "active"})


class AdPlansAPI:
    """Кампания (верхний уровень). API-ресурс /ad_plans."""

    def __init__(self, client: VKAdsClient):
        self.client = client

    def list(self, limit: int = 100, offset: int = 0) -> list:
        data = self.client.request("GET", f"/ad_plans.json?limit={limit}&offset={offset}")
        return data.get("items", []) if isinstance(data, dict) else data

    def get(self, ad_plan_id: int) -> dict:
        return self.client.request("GET", f"/ad_plans/{ad_plan_id}.json")

    def create(self, payload: dict) -> dict:
        """Создаёт кампанию. Может содержать nested campaigns для атомарности.
        Форсирует blocked; запрещает создание сразу в active."""
        if payload.get("status") == "active":
            raise VKAdsError("Запрещено создавать ad_plan в status=active. Создавай blocked, активируй отдельно.")
        payload.setdefault("status", "blocked")
        return self.client.request("POST", "/ad_plans.json", json=payload)

    def update(self, ad_plan_id: int, payload: dict) -> dict:
        return self.client.request("PUT", f"/ad_plans/{ad_plan_id}.json", json=payload)

    def pause(self, ad_plan_id: int) -> dict:
        return self.update(ad_plan_id, {"status": "blocked"})


class CampaignsAPI:
    """Группа объявлений. API-ресурс /campaigns. Ребёнок ad_plan (нужен ad_plan_id)."""

    def __init__(self, client: VKAdsClient):
        self.client = client

    def list(self, ad_plan_id: Optional[int] = None, limit: int = 100, offset: int = 0) -> list:
        path = f"/campaigns.json?limit={limit}&offset={offset}"
        if ad_plan_id:
            path += f"&ad_plan_id={ad_plan_id}"
        data = self.client.request("GET", path)
        return data.get("items", []) if isinstance(data, dict) else data

    def get(self, campaign_id: int) -> dict:
        return self.client.request("GET", f"/campaigns/{campaign_id}.json")

    def create(self, payload: dict) -> dict:
        """Создаёт ГРУППУ. Требует ad_plan_id, иначе группа станет orphan (невидима в UI)."""
        if not payload.get("ad_plan_id"):
            raise VKAdsError(
                "campaign (группа) без ad_plan_id станет orphan и не будет видна в кабинете. "
                "Укажи ad_plan_id или используй client.create_campaign_tree(...)."
            )
        if payload.get("status") == "active":
            raise VKAdsError("Запрещено создавать группу в status=active. Создавай blocked, активируй отдельно.")
        payload.setdefault("status", "blocked")
        return self.client.request("POST", "/campaigns.json", json=payload)

    def update(self, campaign_id: int, payload: dict) -> dict:
        return self.client.request("PUT", f"/campaigns/{campaign_id}.json", json=payload)

    def pause(self, campaign_id: int) -> dict:
        return self.update(campaign_id, {"status": "blocked"})


class BannersAPI:
    """Объявление. API-ресурс /banners. Ребёнок campaign/группы (нужен campaign_id)."""

    def __init__(self, client: VKAdsClient):
        self.client = client

    def list(self, campaign_id: Optional[int] = None, limit: int = 100, offset: int = 0) -> list:
        path = f"/banners.json?limit={limit}&offset={offset}"
        if campaign_id:
            path += f"&campaign_id={campaign_id}"
        data = self.client.request("GET", path)
        return data.get("items", []) if isinstance(data, dict) else data

    def get(self, banner_id: int) -> dict:
        return self.client.request("GET", f"/banners/{banner_id}.json")

    def create(self, payload: dict) -> dict:
        """Создаёт ОБЪЯВЛЕНИЕ. Привязывается к campaign_id (к ГРУППЕ), НЕ к ad_plan_id."""
        if payload.get("ad_plan_id") and not payload.get("campaign_id"):
            raise VKAdsError(
                "banner привязывается к campaign_id (группе), а не к ad_plan_id. "
                "Передан ad_plan_id вместо campaign_id — исправь связь."
            )
        if not payload.get("campaign_id"):
            raise VKAdsError("banner требует campaign_id (id группы). Используй client.create_campaign_tree(...).")
        payload.setdefault("status", "blocked")
        return self.client.request("POST", "/banners.json", json=payload)

    def update(self, banner_id: int, payload: dict) -> dict:
        return self.client.request("PUT", f"/banners/{banner_id}.json", json=payload)


class AudiencesAPI:
    def __init__(self, client: VKAdsClient):
        self.client = client

    def list(self) -> list:
        data = self.client.request("GET", "/audiences.json")
        return data.get("items", []) if isinstance(data, dict) else data

    def get(self, audience_id: int) -> dict:
        return self.client.request("GET", f"/audiences/{audience_id}.json")

    def create(self, payload: dict) -> dict:
        return self.client.request("POST", "/audiences.json", json=payload)

    def upload_list(self, audience_id: int, csv_path: Path) -> dict:
        """Загружает CSV в custom_list аудиторию."""
        with Path(csv_path).open("rb") as f:
            return self.client.request(
                "POST",
                f"/audiences/{audience_id}/upload.json",
                files={"file": ("list.csv", f, "text/csv")},
            )


class MediaAPI:
    """Загрузка изображений и видео для использования в баннерах."""

    def __init__(self, client: VKAdsClient):
        self.client = client

    def upload_image(self, image_path: Path) -> dict:
        image_path = Path(image_path)
        if not image_path.exists():
            raise VKAdsError(f"Не найден файл изображения: {image_path}")
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        with image_path.open("rb") as f:
            return self.client.upload(
                path="/content/upload.json",
                files={"file": (image_path.name, f, mime)},
            )

    def upload_video(self, video_path: Path) -> dict:
        """Загружает видео (перформанс-объявление: до 90 МБ, mp4/mpeg/avi/mov)."""
        video_path = Path(video_path)
        if not video_path.exists():
            raise VKAdsError(f"Не найден видео-файл: {video_path}")
        with video_path.open("rb") as f:
            return self.client.upload(
                path="/content/upload.json",
                files={"file": (video_path.name, f, "video/mp4")},
            )

    def wait_for_video(self, video_id: int, max_attempts: int = 60, poll_interval: int = 5) -> dict:
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            info = self.client.request("GET", f"/content/{video_id}.json")
            status = info.get("status", "processing")
            if status == "ready":
                return info
            if status in ("failed", "error"):
                raise VKAdsError(f"VK не смог обработать видео {video_id}: {info}")
        raise VKAdsError(f"Видео {video_id} не обработалось за {max_attempts * poll_interval}s")


class StatisticsAPI:
    def __init__(self, client: VKAdsClient):
        self.client = client

    def ad_plans_day(self, ad_plan_ids: list, date_from: str, date_to: str) -> list:
        ids = ",".join(str(i) for i in ad_plan_ids)
        path = f"/statistics/ad_plans/{ids}/day.json?date_from={date_from}&date_to={date_to}"
        data = self.client.request("GET", path)
        return data.get("items", []) if isinstance(data, dict) else data

    def campaigns_day(self, campaign_ids: list, date_from: str, date_to: str) -> list:
        ids = ",".join(str(i) for i in campaign_ids)
        path = f"/statistics/campaigns/{ids}/day.json?date_from={date_from}&date_to={date_to}"
        data = self.client.request("GET", path)
        return data.get("items", []) if isinstance(data, dict) else data

    def summary(self, entity: str = "ad_plans", ids: list = None) -> dict:
        ids_str = ",".join(str(i) for i in ids) if ids else ""
        path = f"/statistics/{entity}/{ids_str}/summary.json"
        return self.client.request("GET", path)


# ---------- Вспомогательное ----------

def _load_dotenv_if_present() -> None:
    """Мини-загрузчик .env без внешних зависимостей. Ищет .env в cwd и в папке scripts/."""
    for candidate in (Path.cwd() / ".env", Path(__file__).parent / ".env"):
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        except Exception:
            pass


# ---------- CLI ----------


def cmd_audit(args):
    client = VKAdsClient.from_env(dry_run=args.dry_run)
    plans = client.ad_plans.list(limit=args.limit)
    active = [c for c in plans if c.get("status") == "active"]
    blocked = [c for c in plans if c.get("status") == "blocked"]
    print(f"Кампаний (ad_plans): {len(plans)}, активных: {len(active)}, на паузе: {len(blocked)}")
    for c in plans:
        budget = c.get("budget_limit_day", 0) / 100 if c.get("budget_limit_day") else 0
        print(f"  [{c.get('status')}] {c.get('id')} '{c.get('name')}' — {budget} ₽/день")


def cmd_probe(args):
    client = VKAdsClient.from_env(dry_run=False)
    schema = client.probe_schema()
    print("Фактические поля объектов в кабинете (live-сверка):")
    print(json.dumps(schema, ensure_ascii=False, indent=2))


def cmd_stats(args):
    from datetime import date, timedelta

    client = VKAdsClient.from_env(dry_run=args.dry_run)
    plans = client.ad_plans.list()
    if args.ad_plan_id:
        ids = [args.ad_plan_id]
    else:
        ids = [c["id"] for c in plans if c.get("status") == "active"]

    if not ids:
        print("Нет активных кампаний для статистики")
        return

    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=args.days)).isoformat()
    stats = client.statistics.ad_plans_day(ids, date_from, date_to)

    print(f"Статистика за {args.days} дней ({date_from} → {date_to}):")
    for s in stats:
        cost = s.get("cost", 0) / 100
        ctr = s.get("ctr", 0)
        clicks = s.get("clicks", 0)
        impressions = s.get("impressions", 0)
        print(
            f"  ad_plan {s.get('ad_plan_id') or s.get('id')}: "
            f"{cost:.0f} ₽, {impressions} показов, {clicks} кликов, CTR {ctr:.2f}%"
        )


def cmd_campaign_update(args):
    """Обновление бюджета/статуса кампании (ad_plan). Активация — с ОРД-preflight."""
    client = VKAdsClient.from_env(dry_run=args.dry_run)
    if args.status == "active":
        if not args.confirm_activate:
            raise SystemExit("Для активации добавь --confirm-activate.")
        result = client.activate_ad_plan(
            args.id,
            confirmation=ACTIVATE_CONFIRMATION,
            allow_missing_ord=args.allow_missing_ord,
        )
        print(f"OK (активировано): {result}")
        return

    payload = {}
    if args.budget is not None:
        payload["budget_limit_day"] = int(args.budget * 100)  # в копейках
    if args.status:
        payload["status"] = args.status
    if not payload:
        raise SystemExit("Нечего обновлять. Укажи --budget или --status")
    result = client.ad_plans.update(args.id, payload)
    print(f"OK: {result}")


def main():
    parser = argparse.ArgumentParser(description="VK Ads API client")
    parser.add_argument("--dry-run", action="store_true", help="Не выполнять write-операции")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="Список кампаний (ad_plans)")
    p_audit.add_argument("--limit", type=int, default=100)
    p_audit.set_defaults(func=cmd_audit)

    p_probe = sub.add_parser("probe-schema", help="Live-сверка имён полей с кабинетом")
    p_probe.set_defaults(func=cmd_probe)

    p_stats = sub.add_parser("stats", help="Статистика")
    p_stats.add_argument("--days", type=int, default=7)
    p_stats.add_argument("--ad-plan-id", type=int, default=None)
    p_stats.set_defaults(func=cmd_stats)

    p_upd = sub.add_parser("campaign-update", help="Обновить кампанию (ad_plan)")
    p_upd.add_argument("--id", type=int, required=True)
    p_upd.add_argument("--budget", type=float, help="Новый дневной бюджет в рублях")
    p_upd.add_argument("--status", choices=["blocked", "active", "deleted"], help="Новый статус")
    p_upd.add_argument("--confirm-activate", action="store_true")
    p_upd.add_argument(
        "--allow-missing-ord",
        action="store_true",
        help="Разрешить активацию без company_info (реклама без онлайн-продажи товаров)",
    )
    p_upd.set_defaults(func=cmd_campaign_update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
