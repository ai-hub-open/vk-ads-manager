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


def test_every_banner_gets_campaign_id_of_its_own_group(client):
    tree = client.create_campaign_tree({"name": "Кампания"}, GROUPS)

    for grp in tree["groups"]:
        for ban in grp["banners"]:
            assert ban["payload"]["campaign_id"] == grp["id"]

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

def test_banner_with_ad_plan_id_instead_of_campaign_id_rejected(client):
    """Частая ошибка: объявление вешают на «Кампанию» вместо «Группы»."""
    with pytest.raises(VKAdsError, match="campaign_id"):
        client.banners.create({"ad_plan_id": 123, "title": "О"})


def test_banner_without_campaign_id_rejected(client):
    with pytest.raises(VKAdsError, match="campaign_id"):
        client.banners.create({"title": "О"})


# ---------- dry-run действительно не пишет ----------

def test_dry_run_write_returns_stub_without_network(client):
    """conftest уронил бы тест на реальном connect — значит записи не было."""
    assert client.request("POST", "/ad_plans.json", json={"name": "x"}) == {"dry_run": True}


def test_dry_run_tree_has_no_errors(client):
    tree = client.create_campaign_tree({"name": "Кампания"}, GROUPS)
    assert tree["errors"] == []
