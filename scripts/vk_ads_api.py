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
import re
import sys
import time
from pathlib import Path
from typing import Optional, Any, Union
from urllib.parse import urlparse

try:
    from scripts._console import setup_console
    from scripts._image_prep import fit_square, prepare_image, LOGO_SIDE
except ImportError:  # запуск напрямую, не как модуль пакета
    from _console import setup_console
    from _image_prep import fit_square, prepare_image, LOGO_SIDE

try:
    import requests
except ImportError:
    print("ERROR: модуль requests не установлен. pip install requests", file=sys.stderr)
    sys.exit(1)


BASE_URL = "https://ads.vk.com/api/v2"
TOKEN_URL = f"{BASE_URL}/oauth2/token.json"

# CTA дефолт и прочие константы можно расширять
ACTIVATE_CONFIRMATION = "YES_ACTIVATE"


# ---------- Единицы денег ----------
# VK Ads API принимает и отдаёт budget_limit / budget_limit_day в РУБЛЯХ (не в копейках;
# подтверждено вживую 2026-10-16: запись 500 = «500 ₽» в кабинете). Статистика: spent/cpm/cpc/cpa —
# рубли (строки), ctr/cr — ПРОЦЕНТЫ (0.5176 = 0.52 %). Никаких ×100 / ÷100.

MAX_DAILY_BUDGET_RUB = 100_000
MAX_TOTAL_BUDGET_RUB = 10_000_000


class BudgetGuardError(ValueError):
    """Бюджет выше порога без явного подтверждения."""


def budget_rub(value):
    """Рубли для API: целое — как int, дробное — до 2 знаков (копейки).

    Мусор (не число, NaN, inf, отрицательное) — BudgetGuardError, а не traceback.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BudgetGuardError(f"Не могу прочитать бюджет {value!r}: нужно число в рублях.")
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        raise BudgetGuardError(
            f"Некорректный бюджет {value!r}: нужно конечное неотрицательное число в рублях.")
    rounded = round(number, 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def fmt_rub(value) -> str:
    """12345678 -> '12 345 678 ₽' (для сообщений пользователю)."""
    v = budget_rub(value)
    text = f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"
    return text.replace(",", " ") + " ₽"


def check_budget_guard(daily_rub=None, total_rub=None, confirmed: bool = False,
                       flag: str = "--confirm-large-budget") -> None:
    """Отказ записывать подозрительно большой бюджет без явного флага.

    Дневной > 100 000 ₽ или общий > 10 000 000 ₽ — почти всегда ошибка единиц
    (раньше скилл умножал рубли на 100). Сообщение называет сумму в рублях.
    """
    problems = []
    # сравниваем то, что реально уйдёт в API (округлённое), а не сырой ввод
    daily_rub = None if daily_rub is None else budget_rub(daily_rub)
    total_rub = None if total_rub is None else budget_rub(total_rub)
    if daily_rub is not None and daily_rub > MAX_DAILY_BUDGET_RUB:
        problems.append(f"дневной бюджет {fmt_rub(daily_rub)} (порог {fmt_rub(MAX_DAILY_BUDGET_RUB)})")
    if total_rub is not None and total_rub > MAX_TOTAL_BUDGET_RUB:
        problems.append(f"общий бюджет {fmt_rub(total_rub)} (порог {fmt_rub(MAX_TOTAL_BUDGET_RUB)})")
    if problems and not confirmed:
        raise BudgetGuardError(
            "Слишком большой бюджет: " + "; ".join(problems) + ". "
            "API принимает рубли, не копейки — проверь, что сумма в рублях. "
            f"Назови пользователю сумму в рублях; флаг {flag} — только после его явного «да, сумма верна»."
        )


def check_kopeck_units(campaign: dict, state: Optional[dict] = None, group_daily=None) -> None:
    """Ловит бюджеты в копейках из 0.7.0 (рубли ×100), не опираясь на единственный источник.

    * `campaign.budget_limit`/`budget_limit_day` ≥ 100× `_state.json` `budget_rub`/`daily_budget_rub`;
    * дневной бюджет кампании ≥ 100× суммы дневных бюджетов групп (они всегда в рублях).
    """
    msg = ("{what}: похоже, бюджет в копейках из версии 0.7.0 — укажите в рублях "
           "(API принимает рубли; сейчас {val} против {ref}).")
    state = state or {}
    for key, state_key in (("budget_limit", "budget_rub"), ("budget_limit_day", "daily_budget_rub")):
        val, ref = campaign.get(key), state.get(state_key)
        if val and ref and budget_rub(val) >= 100 * budget_rub(ref):
            raise BudgetGuardError(msg.format(what=f"{key} в creatives.json", val=fmt_rub(val),
                                              ref=f"{state_key}={fmt_rub(ref)} в _state.json"))
    groups = [budget_rub(g) for g in (group_daily or []) if g]
    day = campaign.get("budget_limit_day")
    if day and groups and sum(groups) > 0 and budget_rub(day) >= 100 * sum(groups):
        raise BudgetGuardError(msg.format(what="дневной бюджет кампании", val=fmt_rub(day),
                                          ref=f"сумма дневных бюджетов групп {fmt_rub(sum(groups))}"))


class VKAdsError(Exception):
    pass


class OrdPreflightError(VKAdsError):
    """Активация заблокирована: не пройдена проверка ОРД-маркировки."""


ORD_DATA_REQUIRED_MESSAGE = (
    "VK отказал в создании объявления: в кабинете не заполнены данные для ОРД "
    "(ord_data_required). Нужны ИНН и название рекламодателя в настройках кабинета "
    "ads.vk.ru (раздел про маркировку рекламы / ОРД; для агентства — данные клиента и договора, "
    "см. references/ord-agency.md). Заполните их в кабинете и запустите залив заново — "
    "повторять запрос без этого бесполезно."
)


class OrdDataRequiredError(VKAdsError):
    """VK отклонил создание: в кабинете нет данных ОРД (ИНН, название рекламодателя)."""


def is_ord_data_required(error) -> bool:
    """Ответ VK с кодом ord_data_required (в тексте ошибки или в теле ответа)."""
    return "ord_data_required" in str(error)


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
        self.urls = UrlsAPI(self)
        self.packages = PackagesAPI(self)

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
                    if is_ord_data_required(err):
                        # Без повторов: пока в кабинете не заполнят ОРД, ответ не изменится.
                        raise OrdDataRequiredError(f"{ORD_DATA_REQUIRED_MESSAGE} Ответ VK: HTTP {resp.status_code}: {err}")
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

        # 2) Группы (campaigns) с ad_plan_id — объявления уходят ВЛОЖЕННЫМ массивом
        #    в том же вызове: отдельного POST /banners.json в API нет (405), а группа,
        #    созданная без баннеров, навсегда останется пустой.
        for gi, g in enumerate(groups):
            g_payload = dict(g.get("payload", {}))
            g_payload["ad_plan_id"] = ad_plan_id
            g_payload["status"] = "blocked"

            banner_payloads = [{**dict(b), "status": "blocked"} for b in g.get("banners", [])]
            if banner_payloads:
                g_payload["banners"] = banner_payloads

            try:
                g_resp = self.campaigns.create(g_payload)
            except OrdDataRequiredError as e:
                # Все группы упадут одинаково — не долбим API, отдаём наверх одну понятную ошибку
                # с точными id того, что уже создано (кампания пустая, на паузе).
                e.ad_plan_id = None if self.dry_run else ad_plan_id
                e.created_group_ids = [g["id"] for g in result["groups"]]
                raise
            except VKAdsError as e:
                result["errors"].append(f"группа '{g_payload.get('name')}': {e}")
                continue

            group_id = g_resp.get("id") or (f"DRY_GROUP_{gi}" if self.dry_run else 0)
            group_rec = {"id": group_id, "payload": g_payload, "banners": []}

            # id объявлений приходят в ответе на создание группы, по порядку payload.
            created = g_resp.get("banners") or []
            for bi, b_payload in enumerate(banner_payloads):
                b_resp = created[bi] if bi < len(created) else {}
                group_rec["banners"].append({
                    "id": b_resp.get("id", f"DRY_BANNER_{gi}_{bi}" if self.dry_run else 0),
                    "payload": b_payload,
                })

            if banner_payloads and not created and not self.dry_run:
                result["errors"].append(
                    f"группа {group_id}: в ответе нет созданных объявлений — "
                    f"проверь список объявлений группы: GET /banners.json?_campaign_id={group_id} "
                    f"(MCP: vk_ads_banners_list(campaign_id={group_id})). Поле banners у группы "
                    f"на паузе всегда пустое — по нему не суди"
                )
            result["groups"].append(group_rec)

        return result

    def preflight_ord(self, ad_plan_id: int) -> dict:
        """Проверяет ОРД-готовность перед активацией: у объявлений должен быть
        заполнен company_info (юр.данные для маркировки), иначе ОРД отклонит и ERID
        не будет присвоен. Возвращает {'ok': bool, 'banners_without_company_info': [...]}.
        """
        problems = []
        checked = 0
        groups = only_children(self.campaigns.list(ad_plan_id=ad_plan_id, fields="id,name,ad_plan_id"),
                               "ad_plan_id", ad_plan_id)
        for g in groups:
            gid = g.get("id")
            # campaign_id запрашиваем явно: без него only_children отбросит всё и проверять будет нечего.
            # company_info в списке допустимых полей banners нет — VK его не отдаёт (см. «Известные ограничения»)
            for b in only_children(self.banners.list(campaign_id=gid, fields="id,campaign_id"),
                                   "campaign_id", gid):
                checked += 1
                if not (b.get("company_info") or "").strip():
                    problems.append(b.get("id"))
        if not checked:
            # Ничего не проверено — это не «всё в порядке»: барьер не должен молча пропускать кампанию
            return {"ok": False, "banners_without_company_info": [],
                    "reason": f"не удалось проверить: у кампании {ad_plan_id} не найдено ни одной группы "
                              "с объявлениями"}
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
                if check.get("reason"):
                    raise OrdPreflightError(
                        f"Активация заблокирована ОРД-preflight: {check['reason']}. Проверь кампанию в "
                        "кабинете (или явно передай allow_missing_ord=True после ручной проверки ОРД)."
                    )
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

    def list(self, ad_plan_id: Optional[int] = None, limit: int = 100, offset: int = 0,
             fields: Optional[str] = None) -> list:
        """Список групп. Фильтр — myTarget-синтаксис `_ad_plan_id` (так фильтрует и
        хостовый MCP). Вживую в REST-клиенте не проверено: если VK фильтр проигнорирует,
        вернутся группы всего кабинета — отсеивай `only_children(..., "ad_plan_id", id)`
        (для этого запрашивай поле ad_plan_id в `fields`)."""
        path = f"/campaigns.json?limit={limit}&offset={offset}"
        if ad_plan_id:
            path += f"&_ad_plan_id={ad_plan_id}"
        if fields:
            path += f"&fields={fields}"
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

    def list(self, campaign_id: Optional[int] = None, limit: int = 100, offset: int = 0,
             fields: Optional[str] = None) -> list:
        """Список объявлений. Фильтр по группе — `_campaign_id` (myTarget-синтаксис, так
        же шлёт хостовый MCP). Вживую в REST-клиенте не проверено: если VK фильтр
        проигнорирует, придут объявления всего кабинета — отсеивай
        `only_children(..., "campaign_id", id)`."""
        path = f"/banners.json?limit={limit}&offset={offset}"
        if campaign_id:
            path += f"&_campaign_id={campaign_id}"
        if fields:
            path += f"&fields={fields}"
        data = self.client.request("GET", path)
        return data.get("items", []) if isinstance(data, dict) else data

    def get(self, banner_id: int) -> dict:
        return self.client.request("GET", f"/banners/{banner_id}.json")

    def create(self, payload: dict) -> dict:
        """Отдельного создания объявления в API НЕТ. Метод оставлен как заглушка.

        `POST /api/v2/banners.json` отвечает `405 unsupported_http_method`
        с `supported_methods: ["GET"]` — сверено с реализацией хостового сервера
        vk-ads-mcp, которая проверялась против ads.vk.com. Раньше этот метод
        уходил в сеть и падал 405 уже на заливе; теперь объясняет, что делать.

        Объявления создаются **вложенным массивом** внутри группы:
        `campaigns.create({..., "banners": [...]})` — или деревом целиком через
        `client.create_campaign_tree(...)`. «Долить» объявления в существующую
        группу нельзя: группа, созданная без баннеров, остаётся пустой навсегда — её
        пересоздают. Проверка залива — по списку объявлений группы
        (`banners.list(campaign_id=...)`): провал = пустой список. Поле `banners` и issue
        `NO_BANNERS_WITH_ACTIVE_STATUS` у группы на паузе — не признак провала.
        """
        raise VKAdsError(
            "Отдельного создания объявления в VK Ads API нет: POST /banners.json "
            "отвечает 405 unsupported_http_method. Передавай объявления массивом "
            "`banners` внутри payload группы — campaigns.create({..., 'banners': [...]}) "
            "или client.create_campaign_tree(...)."
        )

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
    """Загрузка изображений и видео для использования в баннерах.

    Источником может быть локальный файл или публичный http(s)-URL. VK сам по
    ссылке не ходит — эндпоинт принимает только multipart, — поэтому ссылку
    скачиваем мы и отправляем байты. Это тот же мост, что у пути A через
    KeepImage: `assets/images/*` → публичная ссылка → загрузка; здесь роль
    «скачивателя» берёт на себя клиент, и для локальных файлов KeepImage не
    нужен вовсе.
    """

    # Эндпоинты контента (сверено с реализацией хостового сервера vk-ads-mcp).
    # ⚠️ Общего /content/upload.json в API нет — тип задаётся путём.
    IMAGE_ENDPOINT = "/content/static.json"
    VIDEO_ENDPOINT = "/content/video.json"

    IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".gif": "image/gif", ".webp": "image/webp"}
    VIDEO_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime",
                  ".avi": "video/x-msvideo", ".mpeg": "video/mpeg", ".mpg": "video/mpeg"}

    def __init__(self, client: VKAdsClient):
        self.client = client

    def upload_image(self, source: Union[str, Path]) -> dict:
        """Загружает картинку объявления из локального файла или по публичной ссылке.

        Перед загрузкой картинка приводится к точному размеру роли
        (`_image_prep.prepare_image`): квадрат > 600 → 600×600 (роль `image_600x600`
        отклоняет 1080), 16:9 → 1080×607, 4:5 → 1080×1350. Исходник не меняется —
        уходит копия из памяти. К ответу добавляются `width`/`height` загруженного,
        `role` (нет — соотношение не подходит ни под одну роль), `too_small`
        и `resized_from`, если ужимали.
        """
        return self._upload_prepared(source, prepare_image)

    def upload_logo(self, source: Union[str, Path]) -> dict:
        """Логотип для роли icon_256x256: квадрат больше 256 ужимается до 256×256."""
        return self._upload_prepared(source, lambda blob: fit_square(blob, side=LOGO_SIDE))

    def _upload_prepared(self, source, prep_fn) -> dict:
        name, blob, mime = self._read_source(source, self.IMAGE_MIME, "image/png")
        blob, prep = prep_fn(blob)
        resp = self.client.upload(self.IMAGE_ENDPOINT, files={"file": (name, blob, prep["mime"] or mime)})
        if isinstance(resp, dict):
            resp = dict(resp)
            if prep["width"]:
                resp.setdefault("width", prep["width"])
                resp.setdefault("height", prep["height"])
            if prep.get("role"):
                resp["role"] = prep["role"]
            if prep.get("too_small"):
                resp["too_small"] = True
            if prep["resized"]:
                resp["resized_from"] = prep["original"]
        return resp

    def upload_video(self, source: Union[str, Path]) -> dict:
        """Загружает видео (до 90 МБ, mp4/mpeg/avi/mov) из файла или по ссылке."""
        name, blob, mime = self._read_source(source, self.VIDEO_MIME, "video/mp4")
        return self.client.upload(self.VIDEO_ENDPOINT, files={"file": (name, blob, mime)})

    # ---------- источник: файл или ссылка ----------

    @staticmethod
    def is_url(source: Union[str, Path]) -> bool:
        """http(s)-ссылка? Одна буква со схемой — это диск Windows (C:\\...), не URL."""
        return bool(re.match(r"^https?://", str(source), re.IGNORECASE))

    def _read_source(self, source, mime_map: dict, default_mime: str):
        """Возвращает (имя файла, байты или файловый объект, mime-тип)."""
        if self.is_url(source):
            return self._fetch_url(str(source), mime_map, default_mime)

        path = Path(source)
        if not path.exists():
            raise VKAdsError(f"Не найден файл креатива: {path}")
        mime = mime_map.get(path.suffix.lower(), default_mime)
        return path.name, path.read_bytes(), mime

    def _fetch_url(self, url: str, mime_map: dict, default_mime: str):
        """Скачивает креатив по публичной ссылке.

        Ошибку ссылки отделяем от ошибки VK: «страница вместо картинки» (типовой
        случай — шаренная ссылка Google Drive) должна читаться как проблема
        ссылки, а не как отказ рекламной системы по контенту.
        """
        if self.client.dry_run:
            return Path(urlparse(url).path).name or "creative", b"", default_mime
        try:
            resp = requests.get(url, timeout=60)
        except requests.RequestException as e:
            raise VKAdsError(f"Не смог скачать креатив по ссылке {url}: {e}")
        if not resp.ok:
            raise VKAdsError(f"Ссылка {url} вернула HTTP {resp.status_code}")

        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type.startswith("text/"):
            raise VKAdsError(
                f"По ссылке {url} лежит не файл, а страница ({content_type}). "
                "Типовой случай — шаренная ссылка Google Drive или Яндекс.Диска: "
                "нужна прямая ссылка на файл."
            )

        name = Path(urlparse(url).path).name or "creative"
        mime = content_type or mime_map.get(Path(name).suffix.lower(), default_mime)
        return name, resp.content, mime

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


def only_children(items, parent_key: str, parent_id) -> list:
    """Оставляет записи, у которых `parent_key` совпадает с `parent_id`.

    Защита на случай, если VK проигнорировал фильтр `_campaign_id` / `_ad_plan_id`
    и отдал объекты всего кабинета: без этого «у группы есть объявления» читалось бы
    по чужим объявлениям. Запись без поля родителя тоже отбрасывается — подтвердить
    её принадлежность нечем.
    """
    return [it for it in (items or []) if isinstance(it, dict)
            and it.get(parent_key) is not None and str(it.get(parent_key)) == str(parent_id)]


class PackagesAPI:
    """Пакеты размещения: GET /packages.json. ⚠️ В REST-клиенте вживую не проверено.

    Логика как у MCP `vk_ads_packages_list`: весь список одним ответом, фильтр по
    `objective` на клиенте. `package_id` обязателен при создании группы.
    Без пикселя с целью — пакет с оплатой за клики (`priced_event_type: 1`);
    oCPM (`30`) требует `priced_goal` от пикселя.
    """

    KEYS = ("id", "name", "objective", "status", "price", "priced_event_type", "banner_format_id",
            "can_create")

    def __init__(self, client: VKAdsClient):
        self.client = client

    def list(self, objective: Optional[str] = None, only_active: bool = True) -> list:
        data = self.client.request("GET", "/packages.json")
        items = data.get("items", []) if isinstance(data, dict) else (data or [])
        if objective:
            want = objective.lower()
            def objectives(p):
                o = p.get("objective") or []
                return [o] if isinstance(o, str) else list(o)
            items = [p for p in items if want in [str(o).lower() for o in objectives(p)]]
        if only_active:
            # can_create: false — VK отклонит группу как invalid_package, даже если status active
            items = [p for p in items if p.get("status", "active") == "active"
                     and p.get("can_create", True) is not False]
        return [{k: p.get(k) for k in self.KEYS} for p in items]


class UrlsAPI:
    """Регистрация адреса сайта для объявления: POST /urls.json {"url": ...} → id.

    В объявлении ссылка задаётся как `urls: {"primary": {"id": <id>}}` (сверено
    вживую через MCP 10.2026). GET /urls.json отвечает 405 — списка нет, поэтому
    одинаковые адреса кэшируем в рамках одного клиента (одного залива).
    """

    def __init__(self, client: VKAdsClient):
        self.client = client
        self._cache: dict = {}

    def register(self, url: str):
        url = (url or "").strip()
        if not url:
            raise VKAdsError("Пустой адрес сайта: у объявления должен быть url посадочной.")
        if url in self._cache:
            return self._cache[url]
        resp = self.client.request("POST", "/urls.json", json={"url": url})
        url_id = resp.get("id") if isinstance(resp, dict) else None
        if url_id is None:
            if not self.client.dry_run:
                raise VKAdsError(f"VK не вернул id для адреса {url}: {resp}")
            url_id = f"DRY_URL_{len(self._cache)}"
        self._cache[url] = url_id
        return url_id


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
        budget = c.get("budget_limit_day") or 0  # API отдаёт рубли
        print(f"  [{c.get('status')}] {c.get('id')} '{c.get('name')}' — {budget} ₽/день")


def cmd_packages(args):
    """Пакеты размещения для package_id (путь B без MCP). Не проверено вживую."""
    try:
        client = VKAdsClient.from_credentials(dry_run=False)
    except Exception:
        client = VKAdsClient.from_env(dry_run=False)
    items = client.packages.list(objective=args.objective)
    goal = f", цель {args.objective}" if args.objective else ""
    print(f"Пакетов (активных{goal}): {len(items)}")
    print("priced_event_type: 0 = показы (CPM), 1 = клики (CPC), 30 = oCPM (нужен пиксель с целью), "
          "51 = лиды. Без пикселя — бери 1.")
    for p in items:
        print(f"  id={p['id']}  {p['name']}  priced_event_type={p['priced_event_type']}  "
              f"banner_format_id={p['banner_format_id']}")


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
        cost = float(s.get("cost") or 0)  # рубли (в статистике может быть строкой)
        ctr = float(s.get("ctr") or 0)  # уже ПРОЦЕНТЫ: 0.5176 = 0.52 %
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
        try:
            check_budget_guard(daily_rub=args.budget,
                               confirmed=getattr(args, "confirm_large_budget", False))
            payload["budget_limit_day"] = budget_rub(args.budget)  # рубли, не копейки
        except BudgetGuardError as e:
            raise SystemExit(str(e))
    if args.status:
        payload["status"] = args.status
    if not payload:
        raise SystemExit("Нечего обновлять. Укажи --budget или --status")
    result = client.ad_plans.update(args.id, payload)
    print(f"OK: {result}")


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="VK Ads API client")
    parser.add_argument("--dry-run", action="store_true", help="Не выполнять write-операции")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="Список кампаний (ad_plans)")
    p_audit.add_argument("--limit", type=int, default=100)
    p_audit.set_defaults(func=cmd_audit)

    p_pack = sub.add_parser("packages", help="Пакеты размещения (package_id) — не проверено вживую")
    p_pack.add_argument("--objective", default=None, help="Цель: site_conversions, leadads, …")
    p_pack.set_defaults(func=cmd_packages)

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
        "--confirm-large-budget",
        action="store_true",
        help="Разрешить дневной бюджет выше 100 000 ₽ (защита от ошибки единиц)",
    )
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
