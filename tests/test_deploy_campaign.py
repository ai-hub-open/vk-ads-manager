"""Залив кампании (`scripts/deploy_campaign.py`) — самый опасный путь скилла.

Он единственный создаёт сущности в живом кабинете, поэтому инварианты здесь
не косметические:

* всё создаётся в `status: "blocked"` — активирует только маркетолог руками;
* каждая группа получает `ad_plan_id`, иначе становится orphan и невидима в
  новом кабинете ads.vk.ru;
* бюджеты уходят в API в копейках;
* объявления создаются вложенными в группу — отдельного `POST /banners` в API нет.

Сеть закрыта: conftest запрещает `socket.connect`, а клиент поднимается с
`dry_run=True`, то есть write-вызовы наружу не уходят вовсе.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import deploy_campaign as dc


# ---------- рубли → копейки ----------

@pytest.mark.parametrize("rub, kopecks", [
    (60000, 6000000),
    (500, 50000),
    (0, 0),
    (1234.56, 123456),
    (0.01, 1),
    (99.995, 10000),   # округление, а не усечение
])
def test_rub_to_kopecks(rub, kopecks):
    assert dc.rub_to_kopecks(rub) == kopecks
    assert isinstance(dc.rub_to_kopecks(rub), int)


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
    "campaign": {"name": "Тест", "objective": "leadads", "budget_limit": 6000000},
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


def test_group_daily_budget_in_kopecks(tmp_path, _media, monkeypatch):
    """Дневной бюджет группы уходит в API в копейках: 500 ₽ → 50000."""
    captured = {}
    real_tree = dc.VKAdsClient.create_campaign_tree

    def spy(self, ad_plan_payload, groups):
        captured["groups"] = groups
        captured["ad_plan"] = ad_plan_payload
        return real_tree(self, ad_plan_payload, groups)

    monkeypatch.setattr(dc.VKAdsClient, "create_campaign_tree", spy)
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    dc.deploy(ws, dry_run=True, skip_media=True)

    assert captured["groups"][0]["payload"]["budget_limit_day"] == 50000


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


def test_ad_plan_config_from_state_converts_budget(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "_state.json").write_text(
        json.dumps({"brand_name_ru": "Бренд", "budget_rub": 40000, "daily_budget_rub": 2000}),
        encoding="utf-8",
    )
    cfg = dc.load_ad_plan_config(ws)
    assert cfg["status"] == "blocked"
    assert cfg["budget_limit"] == 4000000
    assert cfg["budget_limit_day"] == 200000
