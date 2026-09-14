"""Прогноз (`scripts/forecast.py`) — гейт маркетолога и проверки на реальность.

Смысл гейта: лиды и CPL можно показывать как подтверждённые только если CTR и CR
взяты из факта (`api` — статистика кабинета, `client` — данные клиента). На
бенчмарках и допущениях цифры остаются ориентиром и требуют согласования.
Ошибка здесь стоит слитого бюджета, поэтому проверяем обе стороны гейта.
"""
from __future__ import annotations

import json

import pytest

from scripts import forecast as fc


def _metrics(**sources) -> dict:
    """Метрики с заданными источниками; значения не важны для гейта."""
    base = {"cpm": 200, "ctr": 0.006, "cr_landing": 0.02, "qual_share": 0.6}
    return {k: {"low": v, "mid": v, "high": v, "source": sources.get(k, "benchmark")}
            for k, v in base.items()}


# ---------- гейт ----------

def test_verified_sources_are_api_and_client():
    assert fc.VERIFIED == {"api", "client"}


def test_all_verified_confirms():
    status, weak = fc.leads_status(_metrics(ctr="api", cr_landing="client", qual_share="client"))
    assert status == "confirmed"
    assert weak == []


@pytest.mark.parametrize("weak_key", ["ctr", "cr_landing", "qual_share"])
def test_single_weak_metric_blocks(weak_key):
    sources = {"ctr": "api", "cr_landing": "client", "qual_share": "client"}
    sources[weak_key] = "benchmark"
    status, weak = fc.leads_status(_metrics(**sources))
    assert status == "needs_approval"
    assert weak == [weak_key]


def test_assumption_is_not_verified():
    """`assumption` — не факт: гейт обязан считать такой источник слабым."""
    status, weak = fc.leads_status(
        _metrics(ctr="assumption", cr_landing="assumption", qual_share="assumption")
    )
    assert status == "needs_approval"
    assert set(weak) == {"ctr", "cr_landing", "qual_share"}


def test_cpm_source_does_not_gate():
    """CPM влияет на охват, но не на достоверность лидов/CPL — гейт его не смотрит."""
    status, _ = fc.leads_status(_metrics(cpm="benchmark", ctr="api", cr_landing="client",
                                         qual_share="api"))
    assert status == "confirmed"


def test_qual_share_gates_only_when_present():
    """qual_share необязателен: без него гейт не должен придираться."""
    metrics = _metrics(ctr="api", cr_landing="client")
    del metrics["qual_share"]
    status, weak = fc.leads_status(metrics)
    assert status == "confirmed"
    assert weak == []


def test_missing_metric_counts_as_weak():
    metrics = _metrics(cr_landing="client", qual_share="client")
    del metrics["ctr"]
    status, weak = fc.leads_status(metrics)
    assert status == "needs_approval"
    assert "ctr" in weak


def test_template_ships_unverified_so_gate_is_closed():
    """Шаблон forecast_inputs.json не должен по умолчанию выглядеть подтверждённым."""
    status, weak = fc.leads_status(fc.TEMPLATE["metrics"])
    assert status == "needs_approval"
    assert weak, "в шаблоне обязаны быть слабые источники — иначе гейт бесполезен"


# ---------- арифметика сценария ----------

def test_scenario_math():
    metrics = {k: {"low": v, "mid": v, "high": v, "source": "api"} for k, v in
               {"cpm": 200, "ctr": 0.01, "cr_landing": 0.05, "qual_share": 0.5}.items()}
    scn = fc.compute_scenario("target", 40000, metrics)

    assert scn["impressions"] == pytest.approx(200_000)   # 40000 / 200 * 1000
    assert scn["clicks"] == pytest.approx(2_000)          # 200000 * 0.01
    assert scn["requests"] == pytest.approx(100)          # 2000 * 0.05
    assert scn["leads"] == pytest.approx(50)              # 100 * 0.5
    assert scn["cpl"] == pytest.approx(800)               # 40000 / 50


def test_scenario_without_cr_gives_no_leads():
    """Нет CR — лидов не выдумываем, CPL остаётся пустым."""
    metrics = {"cpm": {"mid": 200, "source": "api"}, "ctr": {"mid": 0.01, "source": "api"}}
    scn = fc.compute_scenario("target", 40000, metrics)
    assert scn["requests"] is None
    assert scn["leads"] is None
    assert scn["cpl"] is None


# ---------- проверки на реальность ----------

def test_warns_when_leads_below_learning_threshold():
    scn = {"name": "target", "leads": 20, "cpl": 500, "ctr": 0.006}
    warnings = fc.reality_checks(scn, {"learning_events_needed": 50})
    assert any("обучени" in w for w in warnings)


def test_warns_when_cpl_below_category_floor():
    scn = {"name": "target", "leads": 200, "cpl": 50, "ctr": 0.006}
    warnings = fc.reality_checks(scn, {"category_cpl_floor": 100, "learning_events_needed": 50})
    assert any("ниже пола" in w for w in warnings)


def test_warns_on_optimistic_ctr():
    scn = {"name": "target", "leads": 200, "cpl": 500, "ctr": 0.05}
    assert any("CTR" in w for w in fc.reality_checks(scn, {"learning_events_needed": 50}))


def test_warns_when_leads_exceed_sales_capacity():
    scn = {"name": "expanded", "leads": 500, "cpl": 500, "ctr": 0.006}
    warnings = fc.reality_checks(scn, {"sales_capacity_per_period": 100,
                                       "learning_events_needed": 50})
    assert any("пропускной" in w for w in warnings)


def test_healthy_scenario_has_no_warnings():
    scn = {"name": "target", "leads": 120, "cpl": 350, "ctr": 0.006}
    assert fc.reality_checks(scn, {"learning_events_needed": 50, "category_cpl_floor": 100,
                                   "sales_capacity_per_period": 300}) == []


# ---------- прогон CLI ----------

def test_init_then_run_writes_forecast(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()

    assert fc.main.__name__ == "main"
    import sys
    sys.argv = ["forecast.py", "--workspace", str(ws), "--init"]
    fc.main()
    template = json.loads((ws / "forecast_inputs.json").read_text(encoding="utf-8"))
    assert template["metrics"]["ctr"]["source"] == "benchmark"

    sys.argv = ["forecast.py", "--workspace", str(ws)]
    fc.main()
    report = (ws / "_forecast.md").read_text(encoding="utf-8")
    assert "ТРЕБУЕТ СОГЛАСОВАНИЯ" in capsys.readouterr().out
    assert report.strip()
