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
