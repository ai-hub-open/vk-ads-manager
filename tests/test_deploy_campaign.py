"""Залив кампании (`scripts/deploy_campaign.py`) — самый опасный путь скилла.

Он единственный создаёт сущности в живом кабинете, поэтому инварианты здесь
не косметические:

* всё создаётся в `status: "blocked"` — активирует только маркетолог руками;
* каждая группа получает `ad_plan_id`, иначе становится orphan и невидима в
  новом кабинете ads.vk.ru;
* бюджеты уходят в API в рублях (НЕ в копейках — 0.7.0 завышал в 100 раз);
* бюджет выше порога не уходит без `--confirm-large-budget`;
* объявления создаются вложенными в группу — отдельного `POST /banners` в API нет.

Сеть закрыта: conftest запрещает `socket.connect`, а клиент поднимается с
`dry_run=True`, то есть write-вызовы наружу не уходят вовсе.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import deploy_campaign as dc
from scripts.vk_ads_api import MediaAPI


# ---------- рубли: API принимает budget_limit* в рублях ----------

@pytest.mark.parametrize("rub, expected", [
    (60000, 60000),
    (500, 500),
    (0, 0),
    (500.0, 500),          # целые рубли остаются int
    (1234.56, 1234.56),    # дробные — до 2 знаков
    (99.999, 100),         # округление до копеек, затем в целое
])
def test_budget_rub(rub, expected):
    assert dc.budget_rub(rub) == expected


def test_no_kopecks_helper_left():
    assert not hasattr(dc, "rub_to_kopecks"), "рубли в копейки больше не переводятся"


# ---------- гео и сообщества ----------

@pytest.mark.parametrize("geo, expected", [
    ("MSK", {"regions": [1]}),
    ("SPB", {"regions": [2, 10174]}),
    ("RF", {"countries": ["ru"]}),
    ("", {"countries": ["ru"]}),
    ("неизвестно", {"countries": ["ru"]}),
])
def test_geo_to_regions(geo, expected):
    assert dc._geo_to_regions(geo) == expected


@pytest.mark.parametrize("value, expected", [
    (12345, 12345),
    ("12345", 12345),
    ("https://vk.com/clubname", None),
    ("", None),
])
def test_extract_community_id(value, expected):
    assert dc._extract_community_id(value) == expected


def test_audience_to_targeting_drops_unparsable_communities():
    aud = {"id": "H1", "config": {"geo": "MSK", "age": [25, 40],
                                  "community_ids_or_urls": [111, "222", "https://vk.com/x"]}}
    t = dc.audience_to_targeting(aud)
    assert t["communities"] == [111, 222]
    assert t["geo"] == {"regions": [1]}
    assert t["age"] == [25, 40]


# ---------- payload объявления ----------

def test_banner_without_media_is_skipped():
    """Объявление без картинки и видео не должно уезжать в API."""
    assert dc.build_banner_payload({"name": "cr1", "title": "Заголовок"}, {}) is None


def test_banner_prefers_video_over_images():
    payload = dc.build_banner_payload(
        {"name": "cr1", "title": "T", "format": "video"},
        {"video_id": 77, "image_id": 88},
    )
    assert payload["videos"] == [{"id": 77}]
    assert "images" not in payload


def test_banner_title_not_silently_truncated():
    """Лимит зависит от объекта (сайт 25 / прочее 40) — резать молча нельзя."""
    long_title = "Очень длинный заголовок далеко за двадцать пять знаков"
    payload = dc.build_banner_payload({"name": "c", "title": long_title}, {"image_id": 1})
    assert payload["title"] == long_title


# ---------- полный dry-run прогон ----------

def _workspace(tmp_path: Path, *, creatives=None, audiences=None) -> Path:
    ws = tmp_path / "vk-campaign-test"
    ws.mkdir()
    (ws / "creatives.json").write_text(json.dumps(creatives, ensure_ascii=False), encoding="utf-8")
    (ws / "audiences.json").write_text(json.dumps(audiences, ensure_ascii=False), encoding="utf-8")
    return ws


DEFAULT_CREATIVES = {
    "campaign": {"name": "Тест", "objective": "leadads", "budget_limit": 60000},
    "creatives": [
        {"name": "cr1", "title": "Заголовок", "description": "Описание",
         "url": "https://example.com", "audience": "H1", "image_url": "https://example.com/a.png"},
    ],
}
DEFAULT_AUDIENCES = [{"id": "H1", "name": "Тёплые", "daily_budget_rub": 500,
                      "config": {"geo": "MSK", "age": [25, 45]}}]


@pytest.fixture
def _media(monkeypatch):
    """Медиа уже загружены: сеть закрыта, а без media объявление не собирается."""
    monkeypatch.setattr(dc, "upload_media",
                        lambda client, creatives, ws, skip, plan: {"cr1": {"image_id": 42}})


def test_dry_run_creates_ad_plan_blocked(tmp_path, _media):
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)

    ad_plan = plan["ad_plan"]
    assert ad_plan is not None
    assert ad_plan["payload"]["status"] == "blocked", "ad_plan не должен создаваться активным"
    assert plan["groups_created"], "группа должна быть создана"
    assert plan["groups_created"][0]["audience_id"] == "H1"


def test_dry_run_never_sets_active(tmp_path, _media):
    """Во всём плане не должно встретиться status=active — активация только руками."""
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert '"active"' not in json.dumps(plan, ensure_ascii=False)


def test_group_daily_budget_in_rubles(tmp_path, _media, monkeypatch):
    """Дневной бюджет группы уходит в API в рублях: 500 ₽ → 500."""
    captured = {}
    real_tree = dc.VKAdsClient.create_campaign_tree

    def spy(self, ad_plan_payload, groups):
        captured["groups"] = groups
        captured["ad_plan"] = ad_plan_payload
        return real_tree(self, ad_plan_payload, groups)

    monkeypatch.setattr(dc.VKAdsClient, "create_campaign_tree", spy)
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    dc.deploy(ws, dry_run=True, skip_media=True)

    assert captured["groups"][0]["payload"]["budget_limit_day"] == 500


def test_banners_nested_in_group(tmp_path, _media):
    """Объявления уходят вложенными в группу: отдельного POST /banners в API нет."""
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert plan["banners_created"], "объявления должны создаваться вместе с группой"


def test_no_media_aborts_instead_of_creating_empty_group(tmp_path):
    """Группа без объявлений уходит в blocked с NO_BANNERS_WITH_ACTIVE_STATUS и мертва.

    Поэтому при отсутствии медиа залив должен остановиться, а не создать пустышку.
    """
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError, match="[Нн]и одной группы"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_missing_creatives_raises(tmp_path):
    ws = _workspace(tmp_path, creatives=[], audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError, match="creatives.json"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_missing_audiences_raises(tmp_path):
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=[])
    with pytest.raises(dc.DeployPlanError, match="audiences.json"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_ad_plan_config_defaults_to_blocked(tmp_path):
    """Даже если маркетолог не указал статус в creatives.json — ставим blocked."""
    ws = _workspace(
        tmp_path,
        creatives={"campaign": {"name": "Без статуса", "objective": "leadads"}, "creatives": []},
        audiences=DEFAULT_AUDIENCES,
    )
    assert dc.load_ad_plan_config(ws)["status"] == "blocked"


def test_ad_plan_config_from_state_keeps_rubles(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "_state.json").write_text(
        json.dumps({"brand_name_ru": "Бренд", "budget_rub": 40000, "daily_budget_rub": 2000}),
        encoding="utf-8",
    )
    cfg = dc.load_ad_plan_config(ws)
    assert cfg["status"] == "blocked"
    assert cfg["budget_limit"] == 40000
    assert cfg["budget_limit_day"] == 2000


# ---------- источник медиа: ссылка или локальный файл ----------

def test_public_url_creative_no_longer_skipped(tmp_path, monkeypatch):
    """Креатив, отданный публичной ссылкой, раньше пропускался как «файл не найден».

    В итоге у группы не оставалось объявлений и залив падал целиком.
    """
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    uploaded = []

    def fake_upload_image(self, source):
        uploaded.append(str(source))
        return {"id": 4242}

    monkeypatch.setattr(MediaAPI, "upload_image", fake_upload_image)

    plan = dc.deploy(ws, dry_run=True, skip_media=False)

    assert uploaded == ["https://example.com/a.png"]
    assert plan["banners_created"], "объявление должно собраться на картинке из ссылки"


def test_image_url_wins_over_local_file(tmp_path):
    """Ссылка маркетолога приоритетнее нашей генерации с тем же именем."""
    local = tmp_path / "cr1.png"
    local.write_bytes(b"\x89PNG")
    assert dc._image_source({"image_url": "https://example.com/x.png"}, local) == "https://example.com/x.png"


def test_local_file_used_when_no_url(tmp_path):
    local = tmp_path / "cr1.png"
    local.write_bytes(b"\x89PNG")
    assert dc._image_source({}, local) == local


def test_no_source_at_all(tmp_path):
    assert dc._image_source({}, tmp_path / "нет.png") is None


def test_carousel_prefers_url_list(tmp_path):
    urls = ["https://example.com/1.png", "https://example.com/2.png"]
    assert dc._carousel_sources({"image_urls": urls}, tmp_path, "cr1") == urls


def test_carousel_falls_back_to_card_files(tmp_path):
    for i in (1, 2):
        (tmp_path / f"cr1_card{i}.png").write_bytes(b"\x89PNG")
    sources = dc._carousel_sources({}, tmp_path, "cr1")
    assert [p.name for p in sources] == ["cr1_card1.png", "cr1_card2.png"]


def test_video_url_source():
    assert dc._video_source({"video_url": "https://example.com/v.mp4"}, Path("нет.mp4")) \
        == "https://example.com/v.mp4"


@pytest.mark.parametrize("source, shown", [
    ("https://example.com/pic.png", "https://example.com/pic.png"),
    (Path("assets/images/cr1.png"), "cr1.png"),
])
def test_describe_source(source, shown):
    assert dc._describe(source) == shown


# ---------- отметки времени ----------

def test_utc_now_is_timezone_aware():
    """`datetime.utcnow()` отдаёт naive-время и намечен к удалению в Python."""
    now = dc._utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


def test_timestamps_carry_offset(tmp_path, _media):
    """По строке в deploy_plan.json должно быть видно, что это UTC, а не локальное."""
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert plan["started_at"].endswith("+00:00")
    assert plan["finished_at"].endswith("+00:00")


def test_no_deprecated_utcnow_left():
    src = (Path(dc.__file__)).read_text(encoding="utf-8")
    assert "utcnow()" not in src.replace("`datetime.utcnow()`", "")


# ---------- старые бюджеты в копейках (0.7.0) ----------

@pytest.mark.parametrize("camp_key, state_key", [("budget_limit", "budget_rub"),
                                                 ("budget_limit_day", "daily_budget_rub")])
def test_refuses_kopeck_budget_vs_state(tmp_path, _media, camp_key, state_key):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", camp_key: 5_000_000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    (ws / "_state.json").write_text(json.dumps({state_key: 50000 if camp_key == "budget_limit" else 3500}),
                                    encoding="utf-8")
    if camp_key == "budget_limit_day":
        (ws / "_state.json").write_text(json.dumps({state_key: 50}), encoding="utf-8")
    with pytest.raises(dc.DeployPlanError, match="в копейках из версии 0.7.0"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_equal_or_close_budget_vs_state_passes(tmp_path, _media):
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    (ws / "_state.json").write_text(json.dumps({"budget_rub": 60000}), encoding="utf-8")
    assert dc.deploy(ws, dry_run=True, skip_media=True)["ad_plan"]


def test_kopeck_check_skipped_without_state(tmp_path, _media):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", "budget_limit": 6_000_000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    assert dc.deploy(ws, dry_run=True, skip_media=True)["ad_plan"]


# ---------- защита от больших бюджетов ----------

def _big(tmp_path, *, daily=500, total=60000):
    creatives = {
        "campaign": {"name": "Тест", "objective": "leadads", "budget_limit": total},
        "creatives": DEFAULT_CREATIVES["creatives"],
    }
    auds = [{**DEFAULT_AUDIENCES[0], "daily_budget_rub": daily}]
    return _workspace(tmp_path, creatives=creatives, audiences=auds)


def test_guard_refuses_large_daily_budget(tmp_path, _media):
    ws = _big(tmp_path, daily=100_001)
    with pytest.raises(dc.DeployPlanError) as e:
        dc.deploy(ws, dry_run=True, skip_media=True)
    msg = str(e.value)
    assert "100 001" in msg and "--confirm-large-budget" in msg


def test_guard_refuses_large_total_budget(tmp_path, _media):
    ws = _big(tmp_path, total=10_000_001)
    with pytest.raises(dc.DeployPlanError) as e:
        dc.deploy(ws, dry_run=True, skip_media=True)
    assert "10 000 001" in str(e.value) and "--confirm-large-budget" in str(e.value)


def test_old_kopeck_style_total_is_now_millions_but_under_limit(tmp_path, _media):
    """6000000 в creatives.json теперь честные 6 млн ₽ (а не 60 тыс.) — ниже порога, проходит."""
    ws = _big(tmp_path, total=6_000_000)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert plan["ad_plan"]["payload"]["budget_limit"] == 6_000_000


def test_guard_boundary_values_pass(tmp_path, _media):
    ws = _big(tmp_path, daily=100_000, total=10_000_000)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert plan["ad_plan"]["payload"]["budget_limit"] == 10_000_000


def test_guard_override_with_flag(tmp_path, _media):
    ws = _big(tmp_path, daily=250_000, total=20_000_000)
    plan = dc.deploy(ws, dry_run=True, skip_media=True, confirm_large_budget=True)
    assert plan["ad_plan"]["payload"]["budget_limit"] == 20_000_000


def test_guard_checks_ad_plan_daily_budget(tmp_path, _media):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", "budget_limit_day": 150_000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    auds = [{**DEFAULT_AUDIENCES[0], "daily_budget_rub": 100_000}]
    ws = _workspace(tmp_path, creatives=creatives, audiences=auds)
    with pytest.raises(dc.DeployPlanError, match="150 000"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_cli_has_confirm_large_budget_flag(tmp_path, _media, monkeypatch):
    ws = _big(tmp_path, daily=250_000)
    monkeypatch.setattr("sys.argv", ["deploy_campaign.py", "--workspace", str(ws), "--dry-run"])
    assert dc.main() == 1
    monkeypatch.setattr("sys.argv", ["deploy_campaign.py", "--workspace", str(ws), "--dry-run",
                                     "--confirm-large-budget"])
    assert dc.main() in (0, 2)


# ---------- ревью: сверка с группами без _state.json, вывод бюджетов ----------

def test_kopeck_ad_plan_daily_vs_groups_without_state(tmp_path, _media):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", "budget_limit_day": 350_000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)  # группа 500 ₽
    assert not (ws / "_state.json").exists()
    with pytest.raises(dc.DeployPlanError, match="в копейках из версии 0.7.0"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_ad_plan_daily_consistent_with_groups_passes(tmp_path, _media):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", "budget_limit_day": 5_000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    assert dc.deploy(ws, dry_run=True, skip_media=True)["ad_plan"]


def test_deploy_prints_budgets_in_rubles(tmp_path, _media, capsys):
    creatives = {"campaign": {"name": "Т", "objective": "leadads", "budget_limit": 60000,
                              "budget_limit_day": 4000},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    dc.deploy(ws, dry_run=True, skip_media=True)
    out = capsys.readouterr().out
    assert "Бюджет кампании" in out
    assert "60 000 ₽" in out and "4 000 ₽" in out
