"""Образец payload `vk_ads_ad_plans_create` в Шаге 11 — тот, что VK принимает с первого вызова.

Добавлено в 0.7.12. Повод — 24 отказа VK на 4 созданные кампании на стенде AI Hub (10–11.2026): агент
собирал payload по образцам скилла, а они задавали кампании оба бюджета, держали `objective` только на
уровне кампании (с целями `leads|conversions`, которых у VK нет), советовали создать кампанию без групп,
ничего не говорили про `autobidding_mode` и не запрещали `patterns` в объявлении. Каждая проверка ниже —
отказ VK с живых заливов. Стратегия группы — `max_goals`: 18.11.2026 VK принял её при создании на CPC-пакете
3232, а без неё поставил фиксированную ставку 1 ₽ за клик (цена пакета).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "SKILL.md"
MCP_REF = REPO_ROOT / "references" / "vk-ads-mcp-integration.md"
API_REF = REPO_ROOT / "references" / "vk-ads-api.md"

# Цели, которые VK отдаёт в available_objectives (vk_ads_packages_list).
VK_OBJECTIVES = {"site_conversions", "leadads", "socialengagement", "appinstalls"}
# Коды кнопки cta_sites_full из allowed_values ответа VK (17.11.2026).
CTA_SITES = {
    "visitSite", "to_shop", "signUp", "choose", "book", "download", "order", "enroll", "register",
    "playGame", "buy", "buy_ticket", "see_menu", "begin", "open", "apply", "learnMore", "call", "get",
    "try", "contactUs", "listen", "watch", "create", "learn", "takeSurvey", "startTest",
}
BANNER_KEYS = {"name", "status", "urls", "textblocks", "content"}
NEVER_ON_CREATE = {"price", "max_price", "bid_strategy", "patterns", "settings", "content_settings"}


def _step11() -> str:
    text = SKILL.read_text(encoding="utf-8")
    i = text.index("# Шаг 11. Финальный залив в кабинет")
    j = text.index("# Шаг 11 (опциональный)", i)
    return text[i:j]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _sample() -> dict:
    """JSON-образец п. 3 пути A: единственный блок json в Шаге 11 с ключом campaigns."""
    blocks = re.findall(r"```json\n(.*?)```", _step11(), flags=re.S)
    samples = [b for b in blocks if '"campaigns"' in b]
    assert len(samples) == 1, "в Шаге 11 должен быть ровно один полный образец ad_plans_create"
    return json.loads(samples[0])


def _keys_anywhere(node) -> set:
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            found |= _keys_anywhere(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys_anywhere(item)
    return found


def test_sample_plan_has_name_objective_one_budget_and_groups():
    plan = _sample()
    assert plan["name"]
    assert plan["status"] == "blocked"
    assert plan["objective"] in VK_OBJECTIVES
    budgets = [k for k in ("budget_limit", "budget_limit_day") if k in plan]
    assert len(budgets) == 1, "Can't set both budget_limit and budget_limit_day"
    assert plan["campaigns"], "campaigns: Empty value — кампанию без групп VK не создаёт"
    assert "package_id" not in plan and "targetings" not in plan
    assert "autobidding_mode" not in plan, "у кампании он включает распределение бюджета кампанией"


def test_sample_groups_share_objective_and_carry_package_budget_targetings():
    plan = _sample()
    for group in plan["campaigns"]:
        assert group["status"] == "blocked"
        assert group["objective"] == plan["objective"], "Objective differ with ad_plan objective"
        assert isinstance(group["package_id"], int)
        assert group["budget_limit_day"] > 0
        assert group["autobidding_mode"] == "max_goals", "без стратегии VK ставит fixed по цене пакета"
        t = group["targetings"]
        ages = t["age"]["age_list"]
        assert ages == list(range(ages[0], ages[-1] + 1)), "возраст — каждый год отдельно"
        assert all(isinstance(s, str) for s in t["sex"])
        assert all(isinstance(r, int) for r in t["geo"]["regions"])
        assert all(isinstance(i, int) for i in t.get("interests", []))
        assert group["banners"]


def test_sample_banner_is_roles_only():
    for group in _sample()["campaigns"]:
        for banner in group["banners"]:
            assert set(banner) == BANNER_KEYS, "Unknown fields: patterns"
            assert banner["status"] == "blocked"
            assert isinstance(banner["urls"]["primary"], str)
            blocks = banner["textblocks"]
            assert len(blocks["title_40_vkads"]["text"]) <= 40
            assert len(blocks["text_90"]["text"]) <= 90
            assert blocks["cta_sites_full"]["text"] in CTA_SITES
            assert "icon_256x256" in banner["content"], "At least one pattern must be in package's settings"
            assert "image_600x600" in banner["content"]


def test_sample_has_no_strategy_or_template_fields():
    assert not (_keys_anywhere(_sample()) & NEVER_ON_CREATE)


def test_step11_names_each_vk_refusal():
    step11 = _flat(_step11())
    for phrase in (
        "Can't set both budget_limit and budget_limit_day",
        "Objective differ with ad_plan objective",
        "objective: Empty value",
        "autobidding_mode: Unallowed value",
        "Unknown fields: patterns",
        "At least one pattern must be in package's settings",
        "campaigns: Empty value",
        "Maximum width is 256",
    ):
        assert phrase in step11, phrase
    assert '`"autobidding_mode": "max_goals"` у каждой группы' in step11
    assert "1 ₽ за клик" in step11


def test_step11_does_not_create_plan_without_groups():
    step11 = _flat(_step11())
    assert "`vk_ads_ad_plans_create` → потом `vk_ads_campaigns_create" not in step11
    assert "создай кампанию с **первой** группой" in step11


def test_mcp_reference_has_refusal_table():
    ref = _flat(MCP_REF.read_text(encoding="utf-8"))
    assert "### Отказы VK при создании и что исправить" in ref
    for phrase in (
        "Can't set both budget_limit and budget_limit_day",
        "Objective differ with ad_plan objective",
        "autobidding_mode: Unallowed value",
        "Unknown fields: patterns",
        "At least one pattern must be in package's settings",
        "Maximum width is 256",
        "campaigns: Empty value",
    ):
        assert phrase in ref, phrase
    for code in CTA_SITES:
        assert f"`{code}`" in ref, code


def test_reference_samples_use_real_objectives_and_no_bid_strategy():
    for path in (API_REF, MCP_REF):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"```(?:json|http|python)\n(.*?)```", text, flags=re.S):
            assert '"bid_strategy"' not in block, path.name
            for wrong in ("leads", "conversions", "traffic", "reach"):
                assert f'"objective": "{wrong}"' not in block and f'"objective":"{wrong}"' not in block, (
                    path.name, wrong)
            plans = re.findall(r'\{"name":"[^"]*","(?:status|objective)"[^\n]*', block)
            for line in plans:
                assert not ("budget_limit\":" in line and "budget_limit_day\":" in line), (path.name, line)
