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


def test_audience_to_targeting_uses_live_shape():
    """Форма как в живых группах: age.age_list по годам, sex строками."""
    aud = {"id": "H1", "config": {"geo": "MSK", "age": [25, 28], "sex": "female"}}
    t = dc.audience_to_targeting(aud)
    assert t["age"] == {"age_list": [25, 26, 27, 28], "expand": False}
    assert t["sex"] == ["female"]
    assert t["geo"] == {"regions": [1]}


def test_audience_to_targeting_defaults_both_sexes():
    t = dc.audience_to_targeting({"id": "H1", "config": {}})
    assert t["sex"] == ["female", "male"]
    assert t["age"]["age_list"][0] == 25 and t["age"]["age_list"][-1] == 45


@pytest.mark.parametrize("value, expected", [
    ([25, 45], list(range(25, 46))),
    ([30, 31, 35], [30, 31, 35]),
    (["18", "24"], list(range(18, 25))),
])
def test_age_list(value, expected):
    assert dc.age_list(value) == expected


@pytest.mark.parametrize("value, expected", [
    (None, ["female", "male"]),
    ("male", ["male"]),
    (["Ж"], ["female"]),
    (["male", "female"], ["female", "male"]),
    ([1, 2], ["female", "male"]),
])
def test_sex_list(value, expected):
    assert dc.sex_list(value) == expected


def test_audience_to_targeting_skips_unverified_keys(capsys):
    """Сообщества / доп. интересы / должности в API не проверены — не шлём, предупреждаем."""
    aud = {"id": "H1", "config": {"community_ids_or_urls": [111],
                                  "additional_interests": [5], "position_filter": ["CEO"]}}
    t = dc.audience_to_targeting(aud)
    assert not {"communities", "interest_categories", "positions"} & t.keys()
    out = capsys.readouterr().out
    assert "сообщества" in out and "должности" in out


# ---------- payload объявления ----------

def test_banner_without_media_is_skipped():
    """Объявление без картинки и видео не должно уезжать в API."""
    assert dc.build_banner_payload({"name": "cr1", "title": "Заголовок"}, {}) is None


CR = {"name": "cr1", "title": "Заголовок", "description": "Короткий текст", "url": "https://example.com"}


def test_banner_prefers_video_over_images():
    payload = dc.build_banner_payload({**CR, "format": "video"},
                                      {"video_id": 77, "image_id": 88}, url_id=5, logo_id=9)
    assert payload["videos"] == [{"id": 77}]
    assert "image_600x600" not in payload["content"]


def test_banner_title_not_silently_truncated():
    """Заголовок в пределах роли уходит целиком, без обрезки."""
    title = "Заголовок на тридцать с лишним знаков"
    assert 25 < len(title) <= 40
    payload = dc.build_banner_payload({**CR, "title": title}, {"image_id": 1}, url_id=5, logo_id=9)
    assert payload["textblocks"]["title_40_vkads"]["text"] == title


# ---------- форма объявления по ролям (0.7.2) ----------

def test_banner_role_form_matches_live_shape():
    """Форма, подтверждённая вживую через MCP: urls.primary.id, textblocks, content по ролям."""
    payload = dc.build_banner_payload(CR, {"image_id": 42, "width": 600, "height": 600},
                                      url_id=777, logo_id=9)
    assert payload["urls"] == {"primary": {"id": 777}}
    assert payload["textblocks"] == {
        "title_40_vkads": {"text": "Заголовок"},
        "text_90": {"text": "Короткий текст"},
        "cta_sites_full": {"text": "visitSite"},
    }
    assert payload["content"] == {"icon_256x256": {"id": 9}, "image_600x600": {"id": 42}}
    for old in ("format", "title", "description", "url", "images", "call_to_action"):
        assert old not in payload, f"VK отвергает старое поле {old}"


def test_banner_title_over_40_is_error_not_cut():
    with pytest.raises(dc.BannerPayloadError, match="title_40_vkads"):
        dc.build_banner_payload({**CR, "title": "З" * 41}, {"image_id": 1}, url_id=5, logo_id=9)


def test_banner_text_over_90_is_error_not_cut():
    with pytest.raises(dc.BannerPayloadError, match="text_90"):
        dc.build_banner_payload({**CR, "description": "т" * 91}, {"image_id": 1}, url_id=5, logo_id=9)


def test_banner_short_text_field_wins_over_long_description():
    payload = dc.build_banner_payload({**CR, "description": "д" * 500, "text_90": "Коротко"},
                                      {"image_id": 1}, url_id=5, logo_id=9)
    assert payload["textblocks"]["text_90"]["text"] == "Коротко"


def test_banner_without_logo_is_clear_error():
    with pytest.raises(dc.BannerPayloadError, match="icon_256x256"):
        dc.build_banner_payload(CR, {"image_id": 1}, url_id=5, logo_id=None)


def test_banner_without_url_is_clear_error():
    with pytest.raises(dc.BannerPayloadError, match="url"):
        dc.build_banner_payload(CR, {"image_id": 1}, url_id=None, logo_id=9)


def test_company_info_not_sent_by_default():
    """Роль about_company_115 не проверена вживую — по умолчанию не шлём (ОРД берётся из кабинета)."""
    payload = dc.build_banner_payload({**CR, "company_info": "ИП Иванов, ИНН 123456789012"},
                                      {"image_id": 1}, url_id=5, logo_id=9)
    assert "about_company_115" not in payload["textblocks"]
    assert "company_info" not in payload


def test_about_company_only_by_explicit_field():
    payload = dc.build_banner_payload({**CR, "about_company_115": "ИП Иванов, ИНН 123456789012"},
                                      {"image_id": 1}, url_id=5, logo_id=9)
    assert payload["textblocks"]["about_company_115"]["text"].startswith("ИП Иванов")


def test_about_company_explicit_field_warns(tmp_path, _media):
    creatives = {"campaign": DEFAULT_CREATIVES["campaign"],
                 "creatives": [{**DEFAULT_CREATIVES["creatives"][0], "about_company_115": "ИП И."}]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert any("about_company_115" in w for w in plan["warnings"])


@pytest.mark.parametrize("w, h, role", [
    (600, 600, "image_600x600"),
    (1080, 607, "image_1080x607"),
    (1080, 1350, "image_1080x1350"),
])
def test_banner_image_role_by_size(w, h, role):
    payload = dc.build_banner_payload(CR, {"image_id": 1, "width": w, "height": h}, url_id=5, logo_id=9)
    assert payload["content"][role] == {"id": 1}


def test_banner_square_not_600_is_error():
    """Квадрат 1080 в роль image_600x600 VK не принимает — должен был ужаться до загрузки."""
    with pytest.raises(dc.BannerPayloadError, match="ровно 600"):
        dc.build_banner_payload(CR, {"image_id": 1, "width": 1080, "height": 1080}, url_id=5, logo_id=9)


def test_banner_odd_ratio_is_error():
    with pytest.raises(dc.BannerPayloadError, match="ни под одну роль"):
        dc.build_banner_payload(CR, {"image_id": 1, "width": 1000, "height": 300}, url_id=5, logo_id=9)


# ---------- полный dry-run прогон ----------

def _workspace(tmp_path: Path, *, creatives=None, audiences=None, brand=None) -> Path:
    ws = tmp_path / "vk-campaign-test"
    ws.mkdir()
    (ws / "creatives.json").write_text(json.dumps(creatives, ensure_ascii=False), encoding="utf-8")
    (ws / "audiences.json").write_text(json.dumps(audiences, ensure_ascii=False), encoding="utf-8")
    if brand is None:
        brand = {"logo_url": "https://example.com/logo.png", "vk_logo_id": 9}
    (ws / "brand.json").write_text(json.dumps(brand, ensure_ascii=False), encoding="utf-8")
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

    def fake_upload_image(self, source, **kw):
        uploaded.append(str(source))
        return {"id": 4242}

    monkeypatch.setattr(MediaAPI, "upload_image", fake_upload_image)
    monkeypatch.setattr(MediaAPI, "upload_logo", fake_upload_image)

    plan = dc.deploy(ws, dry_run=True, skip_media=False)

    assert uploaded[0] == "https://example.com/a.png"
    assert uploaded[1] == "https://example.com/logo.png", "логотип грузится отдельно"
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


# ---------- 0.7.2: логотип, регистрация ссылок, package_id, ОРД, проверка объявлений ----------

def test_missing_logo_aborts_with_clear_message(tmp_path, _media):
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES, brand={})
    with pytest.raises(dc.DeployPlanError, match="icon_256x256"):
        dc.deploy(ws, dry_run=True, skip_media=True)


def test_logo_found_in_assets_brand(tmp_path):
    ws = tmp_path / "ws"
    (ws / "assets" / "brand").mkdir(parents=True)
    (ws / "assets" / "brand" / "logo.png").write_bytes(b"\x89PNG")
    assert dc.find_logo_source(ws, {}) == ws / "assets" / "brand" / "logo.png"
    assert dc.find_logo_source(ws, {"logo_url": "https://x/l.png"}) == "https://x/l.png"
    assert dc.find_logo_source(tmp_path / "пусто", {}) is None


def test_same_url_registered_once(tmp_path, monkeypatch, capsys):
    """POST /urls.json по одному разу на адрес: одинаковые ссылки кэшируются в рамках залива."""
    creatives = {"campaign": DEFAULT_CREATIVES["campaign"], "creatives": [
        {**DEFAULT_CREATIVES["creatives"][0], "name": "cr1"},
        {**DEFAULT_CREATIVES["creatives"][0], "name": "cr2"},
    ]}
    monkeypatch.setattr(dc, "upload_media", lambda client, crs, ws, skip, plan:
                        {"cr1": {"image_id": 1}, "cr2": {"image_id": 2}})
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    out = capsys.readouterr().out
    assert out.count("/urls.json") == 1
    assert len(plan["banners_created"]) == 2


def test_dry_run_banner_uses_registered_url_id(tmp_path, _media, monkeypatch):
    captured = {}
    real_tree = dc.VKAdsClient.create_campaign_tree

    def spy(self, ad_plan_payload, groups):
        captured["groups"] = groups
        return real_tree(self, ad_plan_payload, groups)

    monkeypatch.setattr(dc.VKAdsClient, "create_campaign_tree", spy)
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    dc.deploy(ws, dry_run=True, skip_media=True)
    banner = captured["groups"][0]["banners"][0]
    assert banner["urls"]["primary"]["id"] == "DRY_URL_0"
    assert banner["content"]["icon_256x256"] == {"id": 9}


def test_package_id_goes_to_group_and_not_ad_plan(tmp_path, _media):
    creatives = {"campaign": {**DEFAULT_CREATIVES["campaign"], "package_id": 3232},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert "package_id" not in plan["ad_plan"]["payload"]
    assert not any("package_id" in w for w in plan["warnings"])


def test_missing_package_id_is_warned(tmp_path, _media):
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert any("package_id" in w for w in plan["warnings"])


def test_ord_data_required_becomes_clear_error(tmp_path, _media, monkeypatch):
    def boom(self, ad_plan_payload, groups):
        raise dc.OrdDataRequiredError("ord_data_required")

    monkeypatch.setattr(dc.VKAdsClient, "create_campaign_tree", boom)
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError) as e:
        dc.deploy(ws, dry_run=True, skip_media=True)
    assert "ИНН" in str(e.value) and "ОРД" in str(e.value)


class _BannersStub:
    def __init__(self, by_group):
        self.by_group = by_group

    def list(self, campaign_id=None, limit=100, offset=0, fields=None):
        self.fields = fields
        return self.by_group.get(campaign_id, [])


class _ClientStub:
    def __init__(self, by_group):
        self.banners = _BannersStub(by_group)


def test_verify_banners_uses_banner_list_not_group_field():
    """Группа на паузе отдаёт пустое поле banners — смотрим список объявлений группы."""
    client = _ClientStub({101: [{"id": 1, "campaign_id": 101}], 102: []})
    problems = dc.verify_banners(client, [101, 102])
    assert len(problems) == 1 and "102" in problems[0]
    assert "campaign_id" in client.banners.fields
    assert dc.verify_banners(_ClientStub({101: [{"id": 1, "campaign_id": 101}]}), [101]) == []


def test_verify_banners_ignores_foreign_banners_if_filter_ignored():
    """VK проигнорировал _campaign_id и отдал объявления кабинета — это не «успех»."""
    client = _ClientStub({102: [{"id": 7, "campaign_id": 999}, {"id": 8}]})
    problems = dc.verify_banners(client, [102])
    assert len(problems) == 1 and "чужих" in problems[0]


# ---------- 0.7.2 ревью: порядок проверок, package_id, сохранение плана, ОРД с id ----------

class _LiveStub:
    """Боевой клиент без сети: запоминает вызовы, ничего не создаёт."""

    def __init__(self, calls):
        self.calls = calls


def _fake_live_client(monkeypatch, calls, *, ord_on_group=False):
    """Подменяет VKAdsClient.from_credentials клиентом без сети (session.request замокан)."""
    from scripts.vk_ads_api import VKAdsClient

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self.ok = status < 400
            self._body = body
            self.content = b"x"
            self.text = str(body)

        def json(self):
            return self._body

    def fake_request(method, url, **kw):
        calls.append((method, url))
        if method == "POST" and url.endswith("/ad_plans.json"):
            return _Resp(200, {"id": 555})
        if method == "POST" and url.endswith("/campaigns.json"):
            if ord_on_group:
                return _Resp(400, {"error": {"code": "ord_data_required"}})
            return _Resp(200, {"id": 777, "banners": [{"id": 1}]})
        if method == "POST" and url.endswith("/urls.json"):
            return _Resp(200, {"id": 31})
        if "/banners.json" in url:
            return _Resp(200, {"items": [{"id": 1, "campaign_id": 777}]})
        return _Resp(200, {"items": []})

    def from_credentials(cls=None, dry_run=False):
        client = VKAdsClient(access_token="T", dry_run=False)
        monkeypatch.setattr(client.session, "request", fake_request)
        client.upload = lambda path, files, timeout=300: calls.append(("UPLOAD", path)) or {"id": 42}
        return client

    monkeypatch.setattr(dc.VKAdsClient, "from_credentials", classmethod(lambda cls, dry_run=False: from_credentials()))


def test_real_run_without_package_id_stops_before_any_call(tmp_path, monkeypatch):
    calls = []
    _fake_live_client(monkeypatch, calls)
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError, match="package_id"):
        dc.deploy(ws, dry_run=False, skip_media=True)
    assert calls == [], "без package_id в VK не должно уйти ни одного запроса"


def test_logo_checked_before_media_upload(tmp_path, monkeypatch):
    uploads = []
    monkeypatch.setattr(dc, "upload_media", lambda *a, **k: uploads.append("media") or {})
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES, brand={})
    with pytest.raises(dc.DeployPlanError, match="icon_256x256"):
        dc.deploy(ws, dry_run=True, skip_media=False)
    assert uploads == [], "без логотипа медиа не грузим"


def test_deploy_plan_saved_even_on_error(tmp_path, monkeypatch):
    """Ошибка после загрузки медиа — id контента всё равно в deploy_plan.json."""
    monkeypatch.setattr(dc, "upload_media", lambda client, crs, ws, skip, plan:
                        plan.update(media_uploaded=[{"creative": "cr1", "image_id": 42}]) or {})
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError):
        dc.deploy(ws, dry_run=True, skip_media=True)
    saved = json.loads((ws / "assets" / "deploy_plan.json").read_text(encoding="utf-8"))
    assert saved["media_uploaded"] == [{"creative": "cr1", "image_id": 42}]
    assert saved["logo_id"] == 9
    assert any("ОСТАНОВЛЕНО" in e for e in saved["errors"])


def test_ord_on_group_reports_exact_ad_plan_id(tmp_path, monkeypatch, _media):
    calls = []
    _fake_live_client(monkeypatch, calls, ord_on_group=True)
    creatives = {"campaign": {**DEFAULT_CREATIVES["campaign"], "package_id": 3232},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    with pytest.raises(dc.DeployPlanError) as e:
        dc.deploy(ws, dry_run=False, skip_media=True)
    assert "Кампания 555 создана пустой, на паузе" in str(e.value)
    assert sum(1 for m, u in calls if u.endswith("/campaigns.json")) == 1, "без повторов"
    saved = json.loads((ws / "assets" / "deploy_plan.json").read_text(encoding="utf-8"))
    assert saved["ad_plan"]["id"] == 555


def test_real_run_success_with_fake_client(tmp_path, monkeypatch, _media):
    calls = []
    _fake_live_client(monkeypatch, calls)
    creatives = {"campaign": {**DEFAULT_CREATIVES["campaign"], "package_id": 3232},
                 "creatives": DEFAULT_CREATIVES["creatives"]}
    ws = _workspace(tmp_path, creatives=creatives, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=False, skip_media=True)
    assert plan["errors"] == []
    assert any("_campaign_id=777" in u for m, u in calls), "проверка объявлений — с фильтром _campaign_id"


def test_non_square_roles_warned_as_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "upload_media", lambda client, crs, ws, skip, plan:
                        {"cr1": {"image_id": 42, "width": 1080, "height": 607}})
    ws = _workspace(tmp_path, creatives=DEFAULT_CREATIVES, audiences=DEFAULT_AUDIENCES)
    plan = dc.deploy(ws, dry_run=True, skip_media=True)
    assert any("image_1080x607" in w and "вживую не создавалось" in w for w in plan["warnings"])


def test_non_square_wrong_exact_size_is_error():
    with pytest.raises(dc.BannerPayloadError, match="ровно 1080×607"):
        dc.build_banner_payload(CR, {"image_id": 1, "width": 900, "height": 506}, url_id=5, logo_id=9)


# ---------- интересы с ID из targetings_tree (Шаг 6.5) ----------

def test_interest_ids_from_dicts_and_numbers(capsys):
    ids = dc.interest_ids([{"id": 101, "name": "Маркетинг"}, 202, "303", "SMM без id"])
    assert ids == [101, 202, 303]
    assert "SMM без id" in capsys.readouterr().out


def test_audience_targeting_sends_interest_ids():
    aud = {"id": "W1", "config": {"interests": [{"id": 7, "name": "Бизнес"}]}}
    assert dc.audience_to_targeting(aud)["interests"] == [7]


def test_launch_guide_prints_interest_names_with_ids():
    from scripts import generate_launch_guide as lg
    assert lg._interests_text([{"id": 7, "name": "Бизнес"}, "SMM"]) == "Бизнес (id 7), SMM"
