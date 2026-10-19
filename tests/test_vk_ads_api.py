"""REST-клиент VK Ads (`scripts/vk_ads_api.py`) — путь B залива.

Главное здесь — `create_campaign_tree`: он проставляет связи и статусы, от
которых зависит, увидит ли маркетолог кампанию в кабинете и не начнёт ли она
крутиться сама. Сеть закрыта (conftest + dry_run), наружу ничего не уходит.
"""
from __future__ import annotations

import pytest

from scripts.vk_ads_api import VKAdsClient, VKAdsError


@pytest.fixture
def client() -> VKAdsClient:
    return VKAdsClient(access_token="DRY_RUN_TOKEN", dry_run=True)


GROUPS = [
    {"payload": {"name": "Группа 1", "budget_limit_day": 50000},
     "banners": [{"title": "Объявление 1"}, {"title": "Объявление 2"}]},
    {"payload": {"name": "Группа 2", "budget_limit_day": 30000},
     "banners": [{"title": "Объявление 3"}]},
]


# ---------- инварианты дерева ----------

def test_tree_forces_blocked_everywhere(client):
    """Ни один объект не должен уйти в API активным — запускает только человек."""
    tree = client.create_campaign_tree({"name": "Кампания", "status": "active"}, GROUPS)

    assert tree["ad_plan"]["payload"]["status"] == "blocked", "явный active обязан быть перебит"
    for grp in tree["groups"]:
        assert grp["payload"]["status"] == "blocked"
        for ban in grp["banners"]:
            assert ban["payload"]["status"] == "blocked"


def test_every_group_gets_ad_plan_id(client):
    """Группа без ad_plan_id становится orphan и невидима в новом кабинете."""
    tree = client.create_campaign_tree({"name": "Кампания"}, GROUPS)
    ad_plan_id = tree["ad_plan"]["id"]

    assert ad_plan_id
    for grp in tree["groups"]:
        assert grp["payload"]["ad_plan_id"] == ad_plan_id


def test_banners_go_nested_into_group_payload(client):
    """Объявления уходят массивом внутри payload группы — отдельного POST нет."""
    tree = client.create_campaign_tree({"name": "Кампания"}, GROUPS)

    for grp in tree["groups"]:
        nested = grp["payload"]["banners"]
        assert len(nested) == len(grp["banners"])
        assert all(b["status"] == "blocked" for b in nested)
        # campaign_id внутри вложенного баннера не нужен: родитель и есть группа
        assert all("campaign_id" not in b for b in nested)

    # объявления не перепутаны между группами
    titles = {grp["payload"]["name"]: [b["payload"]["title"] for b in grp["banners"]]
              for grp in tree["groups"]}
    assert titles == {"Группа 1": ["Объявление 1", "Объявление 2"],
                      "Группа 2": ["Объявление 3"]}


def test_tree_does_not_mutate_caller_payloads(client):
    """Клиент не должен править переданные словари — они ещё нужны вызывающему коду."""
    ad_plan = {"name": "Кампания"}
    groups = [{"payload": {"name": "Г1"}, "banners": [{"title": "О1"}]}]

    client.create_campaign_tree(ad_plan, groups)

    assert ad_plan == {"name": "Кампания"}
    assert groups[0]["payload"] == {"name": "Г1"}
    assert groups[0]["banners"][0] == {"title": "О1"}


def test_tree_survives_group_without_banners(client):
    tree = client.create_campaign_tree({"name": "К"}, [{"payload": {"name": "Пустая"}}])
    assert tree["groups"][0]["banners"] == []


# ---------- защита от перепутанных связей ----------

def test_separate_banner_create_refuses_with_explanation(client):
    """POST /banners.json отвечает 405 — метод обязан объяснить это, а не лезть в сеть.

    conftest уронил бы тест на реальном connect: проверка заодно доказывает,
    что запроса не было.
    """
    with pytest.raises(VKAdsError, match="405") as exc:
        client.banners.create({"campaign_id": 123, "title": "О"})
    assert "banners" in str(exc.value), "в тексте должно быть, как создавать правильно"


def test_group_without_banners_key_when_no_banners(client):
    """Пустой массив banners в payload не шлём — только когда объявления есть."""
    tree = client.create_campaign_tree({"name": "К"}, [{"payload": {"name": "Пустая"}}])
    assert "banners" not in tree["groups"][0]["payload"]


# ---------- dry-run действительно не пишет ----------

def test_dry_run_write_returns_stub_without_network(client):
    """conftest уронил бы тест на реальном connect — значит записи не было."""
    assert client.request("POST", "/ad_plans.json", json={"name": "x"}) == {"dry_run": True}


def test_dry_run_tree_has_no_errors(client):
    tree = client.create_campaign_tree({"name": "Кампания"}, GROUPS)
    assert tree["errors"] == []


# ---------- загрузка медиа ----------

class FakeUploadResp:
    def __init__(self, status_code=200, content=b"\x89PNG", content_type="image/png"):
        self.status_code = status_code
        self.ok = status_code < 400
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self):
        return {"id": 777}


def test_image_endpoint_is_content_static(client, monkeypatch, tmp_path, capsys):
    """Общего /content/upload.json в API нет — тип задаётся путём."""
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    from scripts import vk_ads_api as api
    assert api.MediaAPI.IMAGE_ENDPOINT == "/content/static.json"
    assert api.MediaAPI.VIDEO_ENDPOINT == "/content/video.json"

    client.media.upload_image(img)
    assert "/content/static.json" in capsys.readouterr().out  # dry-run печатает URL


@pytest.mark.parametrize("source, expected", [
    ("https://example.com/a.png", True),
    ("http://example.com/a.png", True),
    (r"C:\creatives\a.png", False),          # диск Windows, не схема URL
    ("assets/images/a.png", False),
    ("/home/user/a.png", False),
])
def test_is_url(source, expected):
    from scripts.vk_ads_api import MediaAPI
    assert MediaAPI.is_url(source) is expected


def test_upload_from_public_url_fetches_bytes(monkeypatch, tmp_path):
    """VK по ссылке сам не ходит — скачиваем мы и шлём байты multipart."""
    from scripts.vk_ads_api import VKAdsClient, MediaAPI
    live = VKAdsClient(access_token="T", dry_run=False)

    monkeypatch.setattr(MediaAPI, "is_url", staticmethod(lambda s: str(s).startswith("http")))
    monkeypatch.setattr("scripts.vk_ads_api.requests.get",
                        lambda url, timeout=None: FakeUploadResp())

    sent = {}

    def fake_upload(path, files, timeout=300):
        sent["path"] = path
        sent["files"] = files
        return {"id": 777}

    monkeypatch.setattr(live, "upload", fake_upload)
    assert live.media.upload_image("https://example.com/pic.png")["id"] == 777
    assert sent["path"] == "/content/static.json"
    name, blob, mime = sent["files"]["file"]
    assert name == "pic.png"
    assert blob == b"\x89PNG"
    assert mime == "image/png"


def test_html_page_instead_of_image_is_a_link_error(monkeypatch):
    """Шаренная ссылка Google Drive отдаёт страницу — это проблема ссылки, не VK."""
    from scripts.vk_ads_api import VKAdsClient, VKAdsError
    live = VKAdsClient(access_token="T", dry_run=False)
    monkeypatch.setattr("scripts.vk_ads_api.requests.get",
                        lambda url, timeout=None: FakeUploadResp(content_type="text/html"))

    with pytest.raises(VKAdsError, match="страница"):
        live.media.upload_image("https://drive.google.com/file/d/xxx/view")


def test_missing_local_file_reported_clearly(client, tmp_path):
    from scripts.vk_ads_api import VKAdsError
    with pytest.raises(VKAdsError, match="Не найден файл креатива"):
        client.media.upload_image(tmp_path / "нет.png")


# ---------- бюджеты в рублях, защита от больших сумм, CTR в процентах ----------

import argparse
from scripts import vk_ads_api as api


class _FakePlans:
    def __init__(self):
        self.updated = None

    def update(self, ad_plan_id, payload):
        self.updated = (ad_plan_id, payload)
        return {"id": ad_plan_id}

    def list(self, limit=100):
        return [{"id": 1, "name": "Тест", "status": "active", "budget_limit_day": 500}]


class _FakeClient:
    def __init__(self):
        self.ad_plans = _FakePlans()


def _ns(**kw):
    base = dict(dry_run=True, id=7, budget=None, status=None, confirm_activate=False,
                allow_missing_ord=False, confirm_large_budget=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _patch_client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(api.VKAdsClient, "from_env", classmethod(lambda cls, dry_run=False: fake))
    return fake


def test_campaign_update_writes_rubles_not_kopecks(monkeypatch):
    fake = _patch_client(monkeypatch)
    api.cmd_campaign_update(_ns(budget=500.0))
    assert fake.ad_plans.updated == (7, {"budget_limit_day": 500})


def test_campaign_update_keeps_fractional_ruble(monkeypatch):
    fake = _patch_client(monkeypatch)
    api.cmd_campaign_update(_ns(budget=500.5))
    assert fake.ad_plans.updated[1]["budget_limit_day"] == 500.5


def test_campaign_update_refuses_large_daily_budget(monkeypatch):
    fake = _patch_client(monkeypatch)
    with pytest.raises(SystemExit) as e:
        api.cmd_campaign_update(_ns(budget=150000))
    assert "150 000" in str(e.value) and "--confirm-large-budget" in str(e.value)
    assert fake.ad_plans.updated is None


def test_campaign_update_large_budget_with_flag(monkeypatch):
    fake = _patch_client(monkeypatch)
    api.cmd_campaign_update(_ns(budget=150000, confirm_large_budget=True))
    assert fake.ad_plans.updated[1]["budget_limit_day"] == 150000


def test_campaign_update_cli_flag_exists(monkeypatch):
    fake = _patch_client(monkeypatch)
    monkeypatch.setattr("sys.argv", ["vk_ads_api.py", "--dry-run", "campaign-update",
                                     "--id", "7", "--budget", "150000", "--confirm-large-budget"])
    api.main()
    assert fake.ad_plans.updated[1]["budget_limit_day"] == 150000


def test_check_budget_guard_totals():
    api.check_budget_guard(total_rub=10_000_000)
    with pytest.raises(api.BudgetGuardError, match="10 000 001"):
        api.check_budget_guard(total_rub=10_000_001)
    api.check_budget_guard(total_rub=10_000_001, confirmed=True)


def test_audit_prints_rubles_as_is(monkeypatch, capsys):
    _patch_client(monkeypatch)
    api.cmd_audit(argparse.Namespace(dry_run=True, limit=10))
    assert "500 ₽/день" in capsys.readouterr().out


def test_stats_cost_in_rubles_and_ctr_in_percent(monkeypatch, capsys):
    """cost и ctr из API — рубли и проценты: ни ÷100, ни ×100."""
    fake = _patch_client(monkeypatch)
    fake.statistics = type("S", (), {"ad_plans_day": lambda self, ids, a, b: [
        {"ad_plan_id": 1, "cost": 1234.5, "ctr": 0.5176, "clicks": 608, "impressions": 117469}]})()
    api.cmd_stats(argparse.Namespace(dry_run=True, ad_plan_id=1, days=7))
    out = capsys.readouterr().out
    assert "1234 ₽" in out or "1235 ₽" in out
    assert "CTR 0.52%" in out


# ---------- ревью: согласие пользователя, мусорные суммы, единицы ----------

def test_guard_message_requires_user_consent():
    with pytest.raises(api.BudgetGuardError) as e:
        api.check_budget_guard(daily_rub=150000)
    msg = str(e.value)
    assert "Назови пользователю сумму в рублях" in msg
    assert "только после его явного" in msg and "да, сумма верна" in msg
    assert "повтори с флагом" not in msg


def test_guard_compares_rounded_value():
    api.check_budget_guard(daily_rub=100000.004)          # пишется как 100000 — проходит
    with pytest.raises(api.BudgetGuardError):
        api.check_budget_guard(daily_rub=100000.006)      # пишется как 100000.01


@pytest.mark.parametrize("bad", ["abc", float("inf"), float("nan"), None, "", -5])
def test_budget_rub_rejects_garbage(bad):
    with pytest.raises(api.BudgetGuardError, match="бюджет"):
        api.budget_rub(bad)


def test_guard_rejects_garbage():
    with pytest.raises(api.BudgetGuardError):
        api.check_budget_guard(daily_rub=float("nan"))


def test_campaign_update_nan_budget_is_clear_refusal(monkeypatch):
    fake = _patch_client(monkeypatch)
    with pytest.raises(SystemExit) as e:
        api.cmd_campaign_update(_ns(budget=float("inf")))
    assert "бюджет" in str(e.value)
    assert fake.ad_plans.updated is None


def test_kopeck_units_vs_groups():
    with pytest.raises(api.BudgetGuardError, match="в копейках из версии 0.7.0"):
        api.check_kopeck_units({"budget_limit_day": 350000}, group_daily=[1000, 2500])
    api.check_kopeck_units({"budget_limit_day": 3500}, group_daily=[1000, 2500])
    api.check_kopeck_units({"budget_limit_day": 350000}, group_daily=[])      # нечем сверять
    api.check_kopeck_units({"budget_limit_day": 350000}, group_daily=[0])


def test_kopeck_units_vs_state():
    with pytest.raises(api.BudgetGuardError, match="в копейках из версии 0.7.0"):
        api.check_kopeck_units({"budget_limit": 5_000_000}, state={"budget_rub": 50000})
    api.check_kopeck_units({"budget_limit": 50000}, state={"budget_rub": 50000})


# ---------- 0.7.2: ссылки, ord_data_required ----------

class _JsonResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.ok = status_code < 400
        self._body = body
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


def test_urls_register_posts_and_caches(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw.get("json")))
        return _JsonResp(200, {"id": 555})

    monkeypatch.setattr(live.session, "request", fake_request)
    assert live.urls.register("https://example.com/a") == 555
    assert live.urls.register("https://example.com/a") == 555
    assert len(calls) == 1
    assert calls[0][0] == "POST" and calls[0][1].endswith("/urls.json")
    assert calls[0][2] == {"url": "https://example.com/a"}


def test_urls_register_empty_is_error():
    live = api.VKAdsClient(access_token="T", dry_run=True)
    with pytest.raises(api.VKAdsError, match="url"):
        live.urls.register("  ")


def test_ord_data_required_is_recognized_without_retries(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        return _JsonResp(400, {"error": {"code": "ord_data_required", "message": "..."}})

    monkeypatch.setattr(live.session, "request", fake_request)
    with pytest.raises(api.OrdDataRequiredError) as e:
        live.request("POST", "/campaigns.json", json={})
    assert len(calls) == 1
    assert "ИНН" in str(e.value)


def test_tree_stops_on_ord_error(monkeypatch):
    """Все группы упадут одинаково — дерево не долбит API по каждой."""
    client = api.VKAdsClient(access_token="T", dry_run=True)
    calls = []

    def create(payload):
        calls.append(payload)
        raise api.OrdDataRequiredError("ord_data_required")

    monkeypatch.setattr(client.campaigns, "create", create)
    with pytest.raises(api.OrdDataRequiredError):
        client.create_campaign_tree({"name": "К"}, GROUPS)
    assert len(calls) == 1


# ---------- фильтры списков: myTarget-синтаксис _campaign_id / _ad_plan_id ----------

def _capture_get(monkeypatch, client, body):
    seen = []

    def fake_request(method, url, **kw):
        seen.append(url)
        return _JsonResp(200, body)

    monkeypatch.setattr(client.session, "request", fake_request)
    return seen


def test_banners_list_uses_underscore_campaign_filter(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    seen = _capture_get(monkeypatch, live, {"items": []})
    live.banners.list(campaign_id=777, fields="id,campaign_id")
    assert "_campaign_id=777" in seen[0]
    assert "&campaign_id=" not in seen[0]
    assert "fields=id,campaign_id" in seen[0]


def test_campaigns_list_uses_underscore_ad_plan_filter(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    seen = _capture_get(monkeypatch, live, {"items": []})
    live.campaigns.list(ad_plan_id=555, fields="id,ad_plan_id")
    assert "_ad_plan_id=555" in seen[0]
    assert "&ad_plan_id=" not in seen[0]


def test_only_children_drops_foreign_and_unknown():
    items = [{"id": 1, "campaign_id": 7}, {"id": 2, "campaign_id": 8}, {"id": 3}]
    assert api.only_children(items, "campaign_id", 7) == [{"id": 1, "campaign_id": 7}]


def test_preflight_ord_filters_foreign_groups(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    monkeypatch.setattr(live.campaigns, "list", lambda **kw: [{"id": 1, "ad_plan_id": 5},
                                                             {"id": 2, "ad_plan_id": 6}])
    asked = []
    monkeypatch.setattr(live.banners, "list",
                        lambda campaign_id=None, **kw: asked.append(campaign_id) or [])
    live.preflight_ord(5)
    assert asked == [1]


def test_packages_list_filters_objective_and_active(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    seen = _capture_get(monkeypatch, live, {"items": [
        {"id": 3232, "name": "cpc", "objective": ["site_conversions"], "status": "active", "priced_event_type": 1},
        {"id": 3229, "name": "ocpm", "objective": ["site_conversions"], "status": "blocked", "priced_event_type": 30},
        {"id": 3215, "name": "lead", "objective": ["leadads"], "status": "active", "priced_event_type": 51},
    ]})
    items = live.packages.list(objective="site_conversions")
    assert seen[0].endswith("/packages.json")
    assert [p["id"] for p in items] == [3232]
    assert items[0]["priced_event_type"] == 1


def test_ord_error_carries_created_ad_plan_id(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)

    def fake_request(method, url, **kw):
        if url.endswith("/ad_plans.json"):
            return _JsonResp(200, {"id": 555})
        return _JsonResp(400, {"error": {"code": "ord_data_required"}})

    monkeypatch.setattr(live.session, "request", fake_request)
    with pytest.raises(api.OrdDataRequiredError) as e:
        live.create_campaign_tree({"name": "К"}, GROUPS)
    assert e.value.ad_plan_id == 555
    assert e.value.created_group_ids == []


def test_preflight_ord_nothing_checked_is_not_ok(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    monkeypatch.setattr(live.campaigns, "list", lambda **kw: [{"id": 1, "ad_plan_id": 5}])
    monkeypatch.setattr(live.banners, "list", lambda **kw: [{"id": 9}])  # без campaign_id — не наш
    res = live.preflight_ord(5)
    assert res["ok"] is False
    assert "не удалось проверить" in res["reason"]


def test_preflight_ord_asks_campaign_id_field(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    monkeypatch.setattr(live.campaigns, "list", lambda **kw: [{"id": 1, "ad_plan_id": 5}])
    seen = {}
    monkeypatch.setattr(live.banners, "list",
                        lambda campaign_id=None, fields=None, **kw: seen.update(fields=fields) or [])
    live.preflight_ord(5)
    assert "campaign_id" in seen["fields"].split(",")


def test_activate_blocked_when_nothing_checked(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    monkeypatch.setattr(live.campaigns, "list", lambda **kw: [])
    with pytest.raises(api.OrdPreflightError, match="не удалось проверить"):
        live.activate_ad_plan(5, confirmation=api.ACTIVATE_CONFIRMATION)


def test_packages_list_skips_cannot_create_and_string_objective(monkeypatch):
    live = api.VKAdsClient(access_token="T", dry_run=False)
    _capture_get(monkeypatch, live, {"items": [
        {"id": 3217, "objective": ["site_conversions"], "status": "active", "can_create": False},
        {"id": 3232, "objective": "site_conversions", "status": "active", "priced_event_type": 1},
    ]})
    assert [p["id"] for p in live.packages.list(objective="site_conversions")] == [3232]
