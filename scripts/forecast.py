#!/usr/bin/env python3
"""
forecast.py — честный прогноз VK Рекламы по трём сценариям.

Решает проблему завышенных прогнозов по лидам/CPL: считает воронку
бюджет → показы → клики → заявки → лиды от ПРОВЕРЯЕМЫХ входных метрик,
помечает источник каждой метрики (api/client/benchmark/assumption) и
ставит статус согласования для лидов/CPL. См. references/forecasting.md.

Usage:
    # 1) создать шаблон входных данных (если его нет)
    python -m scripts.forecast --workspace <path> --init

    # 2) после заполнения forecast_inputs.json (цифры из MCP/кабинета и от клиента)
    python -m scripts.forecast --workspace <path>

Выход: <workspace>/_forecast.md
"""

import argparse
import json
import sys
from pathlib import Path

VERIFIED = {"api", "client"}  # источники, которым можно доверять для лидов/CPL

TEMPLATE = {
    "product": "<название продукта>",
    "currency": "₽",
    "period_days": 14,
    "category_cpl_floor": 100,
    "category_cpl_ceiling": 300,
    "learning_events_needed": 50,
    "sales_capacity_per_period": None,
    "budgets": {"minimal": 30000, "target": 40000, "expanded": 80000},
    "metrics": {
        "cpm": {"low": 150, "mid": 200, "high": 280, "source": "benchmark",
                "note": "ЗАМЕНИТЬ на CPM из get_statistics_summary похожей кампании"},
        "ctr": {"low": 0.004, "mid": 0.006, "high": 0.009, "source": "benchmark",
                "note": "холодная лента VK; api если есть статистика"},
        "cr_landing": {"low": 0.01, "mid": 0.02, "high": 0.04, "source": "assumption",
                       "note": "ЗАПРОСИТЬ у клиента реальный CR лендинга/лид-формы"},
        "qual_share": {"low": 0.5, "mid": 0.6, "high": 0.7, "source": "assumption",
                       "note": "доля качественных лидов из CRM клиента (опционально)"},
    },
}

# для каждого именованного сценария — какие границы метрик брать
SCENARIO_PICK = {
    "minimal":  {"cpm": "high", "ctr": "low",  "cr_landing": "low",  "qual_share": "low"},
    "target":   {"cpm": "mid",  "ctr": "mid",  "cr_landing": "mid",  "qual_share": "mid"},
    "expanded": {"cpm": "low",  "ctr": "high", "cr_landing": "high", "qual_share": "high"},
}
SCENARIO_RU = {"minimal": "Минимальный", "target": "Целевой", "expanded": "Расширенный"}


def m(metrics, key, bound):
    spec = metrics.get(key)
    if not spec:
        return None
    return spec.get(bound, spec.get("mid"))


def compute_scenario(name, budget, metrics):
    pick = SCENARIO_PICK[name]
    cpm = m(metrics, "cpm", pick["cpm"])
    ctr = m(metrics, "ctr", pick["ctr"])
    cr = m(metrics, "cr_landing", pick["cr_landing"])
    qual = m(metrics, "qual_share", pick["qual_share"])

    impressions = budget / cpm * 1000 if cpm else 0
    clicks = impressions * ctr if ctr else 0
    requests = clicks * cr if cr else None
    leads = requests * qual if (requests is not None and qual) else requests
    cpl = (budget / leads) if (leads and leads > 0) else None

    return {
        "name": name, "budget": budget,
        "impressions": impressions, "clicks": clicks, "ctr": ctr,
        "requests": requests, "leads": leads, "cpl": cpl,
    }


def leads_status(metrics):
    """Лиды/CPL подтверждены только если ctr+cr (+qual, если задан) из api/client."""
    gating_keys = ["ctr", "cr_landing"]
    if metrics.get("qual_share"):
        gating_keys.append("qual_share")
    weak = [k for k in gating_keys
            if metrics.get(k, {}).get("source") not in VERIFIED]
    return ("confirmed", []) if not weak else ("needs_approval", weak)


def reality_checks(scn, cfg):
    out = []
    need = cfg.get("learning_events_needed", 50)
    floor = cfg.get("category_cpl_floor")
    cap = cfg.get("sales_capacity_per_period")
    if scn["leads"] is not None and scn["leads"] < need:
        out.append(f"⚠️ {SCENARIO_RU[scn['name']]}: ~{scn['leads']:.0f} лидов за тест < {need} "
                   f"обучающих событий — алгоритм не выйдет из обучения, CPL ненадёжен.")
    if scn["cpl"] is not None and floor and scn["cpl"] < floor:
        out.append(f"⚠️ {SCENARIO_RU[scn['name']]}: CPL {scn['cpl']:.0f} ниже пола ниши "
                   f"({floor}) — вероятно завышен CTR или CR. Пересчитать консервативнее.")
    if scn["ctr"] and scn["ctr"] > 0.025:
        out.append(f"⚠️ {SCENARIO_RU[scn['name']]}: CTR {scn['ctr']*100:.1f}% оптимистичен для холода "
                   f"(реалистично 0.3–0.8%).")
    if cap and scn["leads"] is not None and scn["leads"] > cap:
        out.append(f"⚠️ {SCENARIO_RU[scn['name']]}: ~{scn['leads']:.0f} лидов > пропускной "
                   f"способности отдела продаж ({cap}).")
    return out


def fmt(x, nd=0):
    if x is None:
        return "—"
    return f"{x:,.{nd}f}".replace(",", " ")


def render(cfg):
    cur = cfg.get("currency", "₽")
    metrics = cfg["metrics"]
    status, weak = leads_status(metrics)
    scns = [compute_scenario(n, cfg["budgets"].get(n, 0), metrics)
            for n in ["minimal", "target", "expanded"] if n in cfg["budgets"]]

    badge = "✅ подтверждено" if status == "confirmed" else "⚠️ требует согласования"

    L = [f"# Прогноз VK Реклама — {cfg.get('product','')}", "",
         f"Период теста: {cfg.get('period_days','?')} дн. Все числа лидов/CPL: **{badge}**.", "",
         "## Источники входных метрик", "",
         "| Метрика | low | mid | high | Источник | Тег | Комментарий |",
         "|---|---|---|---|---|---|---|"]
    labels = {"cpm": "CPM", "ctr": "CTR", "cr_landing": "CR заявки",
              "qual_share": "Доля качеств."}
    for k, lab in labels.items():
        s = metrics.get(k)
        if not s:
            continue
        pct = k in ("ctr", "cr_landing", "qual_share")
        def v(b):
            x = s.get(b)
            if x is None:
                return "—"
            return f"{x*100:.2f}%" if pct else fmt(x)
        L.append(f"| {lab} | {v('low')} | {v('mid')} | {v('high')} | "
                 f"{s.get('note','')} | `{s.get('source','—')}` | |")
    L += ["",
          "## Сценарии",
          "",
          f"| Сценарий | Бюджет, {cur} | Показы | Клики | CTR | Заявки | Лиды | CPL, {cur} | Статус |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s in scns:
        L.append(
            f"| {SCENARIO_RU[s['name']]} | {fmt(s['budget'])} | {fmt(s['impressions'])} | "
            f"{fmt(s['clicks'])} | {s['ctr']*100:.2f}% | {fmt(s['requests'])} | "
            f"{fmt(s['leads'])} | {fmt(s['cpl'])} | {badge} |")
    L += [""]
    L.append("> Показы и клики рассчитаны от CPM/CTR из таблицы источников. "
             + ("**Лиды и CPL производны от непроверенных метрик ("
                + ", ".join(weak) + ") — до согласования с маркетологом считать ориентиром, "
                "не обязательством.**" if status == "needs_approval"
                else "Все звенья воронки подтверждены источниками `api`/`client`."))
    L += ["", "## Reality-check", ""]
    checks = []
    for s in scns:
        checks += reality_checks(s, cfg)
    if checks:
        L += [f"- {c}" for c in checks]
    else:
        L.append("- ✅ Грубых нестыковок не найдено.")
    L += ["", "## Гейт согласования", "",
          "1. Показать маркетологу/клиенту таблицу источников и сценарии.",
          "2. Подтвердить CR заявки и долю качественных лидов (заменить `assumption` на `client`).",
          "3. После «да» — статус меняется на «подтверждено маркетологом», цифры идут в стратегию.",
          "4. Если «нереалистично» — взять поправку как `client`-вход, перезапустить forecast.py.", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Прогноз VK по сценариям с тегами источников")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--init", action="store_true", help="создать шаблон forecast_inputs.json")
    args = ap.parse_args()

    ws = Path(args.workspace)
    if not ws.exists():
        print(f"ERROR: workspace {ws} не существует", file=sys.stderr)
        return 1
    inp = ws / "forecast_inputs.json"

    if args.init or not inp.exists():
        if inp.exists():
            print(f"forecast_inputs.json уже есть: {inp}")
        else:
            inp.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Создан шаблон: {inp}\n"
                  "Заполни CPM/CTR из MCP (get_statistics_summary), CR — от клиента, "
                  "затем запусти без --init.")
        return 0

    cfg = json.loads(inp.read_text(encoding="utf-8"))
    out = ws / "_forecast.md"
    out.write_text(render(cfg), encoding="utf-8")
    st, weak = leads_status(cfg["metrics"])
    print(f"OK: {out}")
    print("Статус лидов/CPL:", "подтверждено" if st == "confirmed"
          else f"ТРЕБУЕТ СОГЛАСОВАНИЯ (слабые: {', '.join(weak)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
