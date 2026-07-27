#!/usr/bin/env python3
"""
generate_launch_guide.py — M5 UI fallback. Генератор пошаговой инструкции
для ручного залива кампании в кабинете ads.vk.ru.

Используется когда:
- У клиента нет access_token VK Ads API (или не готов выдавать)
- API недоступен / Replicate отказал в авторизации / VK Ads API меняет схему
- Хочется чтобы за ввод отвечал ответственный человек со стороны клиента

Что генерируем:
- LAUNCH_GUIDE.md в рабочей папке кампании — структурированный пошаговый
  гайд со всеми точными значениями (тексты, бюджеты, аудитории, плашки ОРД)
- Опционально LAUNCH_GUIDE.docx через python-docx для удобной печати

Использование (Claude сам запускает в Cowork):
    python -m scripts.generate_launch_guide --workspace <path>

    # С docx-версией
    python -m scripts.generate_launch_guide --workspace <path> --docx
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_creatives(workspace: Path) -> list:
    data = load_json(workspace / "creatives.json", default=[])
    if isinstance(data, dict):
        return data.get("creatives", [])
    return data


def load_campaign_config(workspace: Path) -> dict:
    data = load_json(workspace / "creatives.json", default={})
    if isinstance(data, dict) and "campaign" in data:
        return data["campaign"]
    state = load_json(workspace / "_state.json", default={})
    return {
        "name": f"{state.get('brand_name_ru', 'Campaign')} — VK тест",
        "budget_limit": state.get("budget_rub", 50000) * 100,
        "budget_limit_day": state.get("daily_budget_rub", 3500) * 100,
        "objective": "conversions",
        "bid_strategy": "minimal_price",
    }


def load_audiences(workspace: Path) -> list:
    data = load_json(workspace / "audiences.json", default=[])
    if isinstance(data, dict):
        return data.get("audiences", [])
    return data


def kop_to_rub(k: int) -> int:
    return int(k / 100)


def _ru_format_objective(obj: str) -> str:
    return {
        "conversions": "Сайт → Конверсии",
        "traffic": "Сайт → Трафик",
        "leads": "Лиды (через лид-форму VK)",
        "reach": "Охват и вовлечение",
        "video_views": "Видео-просмотры",
    }.get(obj, obj)


def _ru_format_strategy(s: str) -> str:
    return {
        "minimal_price": "Минимальная цена (рекомендуется на старте)",
        "max_price": "Предельная цена (после накопления 50+ конверсий)",
    }.get(s, s)


def _ru_format_cta(cta: str) -> str:
    return {
        "download": "Скачать",
        "learn_more": "Узнать больше / Подробнее",
        "sign_up": "Зарегистрироваться",
        "buy": "Купить",
        "subscribe": "Подписаться",
        "register": "Регистрация",
        "contact_us": "Связаться",
    }.get(cta, cta)


def _audience_to_human(audience: dict) -> str:
    """Текстовое описание аудитории для оператора."""
    cfg = audience.get("config", {})
    lines = []

    if "age" in cfg:
        age = cfg["age"]
        lines.append(f"  - Возраст: {age[0]}–{age[1]}")

    sex = cfg.get("sex", [1, 2])
    if sex == [1]:
        lines.append("  - Пол: мужчины")
    elif sex == [2]:
        lines.append("  - Пол: женщины")
    else:
        lines.append("  - Пол: оба")

    geo = cfg.get("geo", "")
    if geo == "RF_million_cities":
        lines.append("  - География: Россия — города-миллионники "
                     "(Москва, СПб, Екатеринбург, Новосибирск, Казань, Нижний Новгород, "
                     "Челябинск, Самара, Уфа, Ростов-на-Дону, Краснодар, Воронеж, Пермь, Волгоград)")
    elif geo == "MSK":
        lines.append("  - География: Москва + Московская область")
    elif geo == "SPB":
        lines.append("  - География: Санкт-Петербург + Ленинградская область")
    else:
        lines.append(f"  - География: {geo}")

    if "community_ids_or_urls" in cfg:
        comms = cfg["community_ids_or_urls"]
        lines.append(f"  - Подписчики сообществ ({len(comms)}):")
        for c in comms:
            lines.append(f"    • {c}")

    if "interests" in cfg:
        lines.append(f"  - Интересы: {', '.join(cfg['interests'])}")
    if "additional_interests" in cfg:
        lines.append(f"  - Доп. интересы: {', '.join(cfg['additional_interests'])}")

    if "position_filter" in cfg:
        lines.append(f"  - Должность в профиле VK: {', '.join(cfg['position_filter'])}")

    if "exclude_audiences" in cfg and cfg["exclude_audiences"]:
        excl = cfg["exclude_audiences"]
        lines.append(f"  - **Исключить** пересечения с: {', '.join(excl)}")

    return "\n".join(lines)


def _vk_format_to_ru(creative: dict) -> str:
    """Маппинг нашего format в VK-формат для оператора."""
    fmt = (creative.get("format", "") or "").lower()
    image_desc = (creative.get("image_or_video", "") or "").lower()
    if "video" in fmt or "video" in image_desc:
        return "Видео в ленте VK (универсальная запись с видео)"
    if "carousel" in fmt and "lead_form" in fmt:
        return "Карусель + Лид-форма VK (внутри VK без перехода на сайт)"
    if "carousel" in fmt:
        return "Карусель (3-10 карточек со свайпом)"
    if "lead_form" in fmt:
        return "Лид-форма VK"
    return "Универсальная запись"


def build_guide(workspace: Path) -> str:
    """Главный билдер LAUNCH_GUIDE.md."""
    creatives = load_creatives(workspace)
    audiences = load_audiences(workspace)
    campaign = load_campaign_config(workspace)
    state = load_json(workspace / "_state.json", default={})
    brand = load_json(workspace / "brand.json", default={})

    # ОРД реквизиты из state или brand
    agent = state.get("agent_relationship", {})
    company_info_sample = creatives[0].get("company_info", "") if creatives else ""
    if not company_info_sample and agent:
        company_info_sample = f"{agent.get('agent_name', '')}, ИНН {agent.get('agent_inn', '')}"

    product = brand.get("product_name") or state.get("brand_name_ru", "Продукт")

    lines = []
    lines.append(f"# LAUNCH GUIDE: ручной залив кампании в ads.vk.ru")
    lines.append("")
    lines.append(f"**Продукт:** {product}")
    lines.append(f"**Сайт:** {state.get('site', '')}")
    lines.append(f"**Дата подготовки:** {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(
        "Эта инструкция собрана автоматически из артефактов кампании "
        f"(`{workspace.name}/creatives.json` + `audiences.json` + `_state.json`). "
        "Используйте её если нет токена VK Ads API или хотите всё проконтролировать руками."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ============ ШАГ 0 ============
    lines.append("## Шаг 0. Подготовка — что должно быть готово ДО входа в кабинет")
    lines.append("")
    lines.append("**Доступы:**")
    lines.append("- [ ] Учётная запись в VK Реклама (https://ads.vk.ru) — личная или агентская")
    lines.append("- [ ] Деньги на балансе (минимум на тестовый период)")
    lines.append("- [ ] Юр.лицо для ОРД — реквизиты на руках")
    lines.append("")
    if company_info_sample:
        lines.append("**Юр.реквизиты для ОРД (пойдут в плашку «Реклама. Рекламодатель: ...»):**")
        lines.append("```")
        lines.append(company_info_sample)
        lines.append("```")
        lines.append("")
    lines.append("**Файлы которые понадобятся (готовы):**")
    lines.append(f"- Тексты креативов: `{workspace}\\creatives.json` и `10_creatives.md`")
    lines.append(f"- Картинки: `{workspace}\\assets\\images\\*.png`")
    lines.append(f"- Видео: `{workspace}\\assets\\videos\\*.mp4`")
    lines.append(f"- PDF lead magnet: `{workspace}\\assets\\pdfs\\*.pdf` (для eBook-кампаний)")
    lines.append("")
    lines.append("**Пиксель VK Ads:**")
    lines.append(f"- [ ] Установлен на сайте {state.get('site', '<сайт продукта>')} "
                 "(см. отдельный файл `07a_pixel_setup.md` с кодом)")
    lines.append("- [ ] Активность пикселя «зелёная» (заходит ≥100 событий/сутки)")
    lines.append("- [ ] Цели в кабинете VK Реклама → Цели → созданы и замаплены на события пикселя")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ============ ШАГ 1: ОРД ============
    lines.append("## Шаг 1. Настройка ОРД-маркировки")
    lines.append("")
    lines.append("**В кабинете:** Настройки → ОРД → Заполнить реквизиты рекламодателя.")
    lines.append("")
    lines.append("**Что вводить:**")
    if agent:
        lines.append(f"- ФИО / название: **{agent.get('agent_name', '<заполни>')}**")
        lines.append(f"- ИНН: **{agent.get('agent_inn', '<заполни>')}**")
        lines.append(f"- ОГРНИП / ОГРН: **{agent.get('agent_ogrnip', '<заполни>')}**")
        lines.append(f"- Email: **{agent.get('agent_email', '<заполни>')}**")
    else:
        lines.append("- Заполнить из ваших юр.документов (ИП / ООО)")
    lines.append("")
    lines.append("**Активировать:** галочка «Передавать данные в ЕРИР через ОРД VK» (бесплатно для пользователей VK Реклама на 2026).")
    lines.append("")
    lines.append("**Что произойдёт:** при создании каждого креатива система автоматически получит токен ERID и проставит плашку «Реклама. Рекламодатель: ...» — вам ничего отдельно делать не нужно.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ============ ШАГ 2: КАМПАНИЯ ============
    lines.append("## Шаг 2. Создание кампании")
    lines.append("")
    lines.append("**В кабинете:** Кампании → «Создать кампанию».")
    lines.append("")
    lines.append("**Настройки:**")
    lines.append(f"- Название: `{campaign.get('name', '')}`")
    lines.append(f"- Цель: **{_ru_format_objective(campaign.get('objective', 'conversions'))}**")
    lines.append(f"- Стратегия ставок: **{_ru_format_strategy(campaign.get('bid_strategy', 'minimal_price'))}**")
    lines.append(f"- Общий бюджет: **{kop_to_rub(campaign.get('budget_limit', 5000000))} ₽**")
    lines.append(f"- Дневной бюджет: **{kop_to_rub(campaign.get('budget_limit_day', 350000))} ₽**")
    lines.append(f"- Срок: {state.get('test_period_days', 14)} дней")
    lines.append(f"- **Статус при создании:** «На паузе» — НЕ запускайте сразу, сначала добавьте группы и баннеры!")
    lines.append("")

    # Микро-цель
    decided = state.get("decided", {}) if isinstance(state.get("decided"), dict) else {}
    micro_goal = decided.get("micro_goal") or campaign.get("main_micro_goal")
    if micro_goal:
        lines.append(f"**Оптимизация на микро-цель:** `{micro_goal}` (это событие пикселя из `07a_pixel_setup.md`).")
        lines.append("")
        lines.append("В разделе «Цель оптимизации» при создании кампании выберите соответствующую "
                     "цель из настроенных в Шаге 0. Если её нет в списке — вернитесь в Цели и создайте.")
        lines.append("")

    lines.append("**После сохранения:** кампания появится в списке со статусом «Активна» или «На паузе». "
                 "Поставьте на паузу через переключатель — пока не зальёте группы.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ============ ШАГ 3: ГРУППЫ ОБЪЯВЛЕНИЙ ============
    lines.append(f"## Шаг 3. Создание {len(audiences)} групп объявлений (по числу аудиторий)")
    lines.append("")
    lines.append("В каждой кампании VK группы объявлений = разные таргетинги. Создаём по одной на каждую аудиторию из стратегии.")
    lines.append("")
    lines.append("**Общий шаблон:** Внутри кампании → «Добавить группу объявлений» → заполнить настройки → Сохранить (статус «На паузе»).")
    lines.append("")

    for i, aud in enumerate(audiences, 1):
        aud_id = aud.get("id", f"A{i}")
        lines.append(f"### Группа {i}/{len(audiences)}: {aud_id} — {aud.get('name', '')}")
        lines.append("")
        lines.append(f"**Название группы:** `{aud_id} — {aud.get('name', '')}`")
        lines.append("")
        lines.append("**Таргетинг:**")
        lines.append(_audience_to_human(aud))
        lines.append("")
        lines.append("**Бюджет и плейсменты:**")
        daily = aud.get("daily_budget_rub", 0)
        lines.append(f"- Дневной бюджет группы: **{daily} ₽**")
        lines.append("- Плейсменты (где показывать): **ВКонтакте лента + VK Клипы + ОК лента** "
                     "(остальные можно выключить на старте — сфокусируемся на основных)")
        lines.append("- Время показа: круглосуточно (можно сузить если знаете когда ЦА онлайн)")
        lines.append("")
        lines.append("**Статус группы при создании:** «На паузе».")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ============ ШАГ 4: БАННЕРЫ ============
    lines.append(f"## Шаг 4. Создание {len(creatives)} баннеров (креативов)")
    lines.append("")
    lines.append("**Общий шаблон:** Внутри группы объявлений → «Добавить объявление» → выбрать формат → заполнить тексты → загрузить медиа → Сохранить (статус «На модерации»).")
    lines.append("")
    lines.append("**После сохранения** каждый баннер уходит на модерацию VK (1-4 часа). После принятия статус сменится на «Принят».")
    lines.append("")

    # Маппинг аудитория → группа уже создана в Шаге 3, теперь баннер → группа
    for i, cr in enumerate(creatives, 1):
        name = cr["name"]
        cr_audiences = cr.get("ad_plan_id", "")
        # creative.ad_plan_id может быть "W1" или "W1+W2"
        target_groups = [a.strip() for a in cr_audiences.split("+")]

        lines.append(f"### Баннер {i}/{len(creatives)}: `{name}`")
        lines.append("")
        lines.append(f"**Группа(-ы) для добавления:** {', '.join(target_groups)} "
                     f"(если несколько — продублируйте баннер в каждую группу руками)")
        lines.append("")
        lines.append(f"**Формат:** {_vk_format_to_ru(cr)}")
        lines.append("")
        lines.append("**Поля для заполнения:**")
        lines.append("")
        title = cr.get("title", "")
        lines.append(f"1. **Заголовок** ({len(title)} знаков, лимит 40):")
        lines.append(f"   ```")
        lines.append(f"   {title}")
        lines.append(f"   ```")
        lines.append("")
        desc = cr.get("description", "")
        lines.append(f"2. **Текст объявления** ({len(desc)} знаков):")
        lines.append(f"   ```")
        # Многострочный текст с отступом
        for ln in desc.split("\n"):
            lines.append(f"   {ln}")
        lines.append(f"   ```")
        lines.append("")
        lines.append(f"3. **CTA-кнопка:** **{_ru_format_cta(cr.get('call_to_action', 'learn_more'))}**")
        lines.append("")
        url = cr.get("url", "")
        lines.append(f"4. **Ссылка перехода (с UTM):**")
        lines.append(f"   ```")
        lines.append(f"   {url}")
        lines.append(f"   ```")
        lines.append("")
        company = cr.get("company_info", company_info_sample)
        lines.append(f"5. **Поле «О компании»** (юр.инфо, ≤115 знаков):")
        lines.append(f"   ```")
        lines.append(f"   {company}")
        lines.append(f"   ```")
        lines.append("")
        lines.append("6. **Передавать данные в ОРД:** галочка ВКЛ (обязательно)")
        lines.append("")

        # Медиа
        fmt = (cr.get("format", "") or "").lower()
        if "video" in fmt or "video" in (cr.get("image_or_video", "") or ""):
            lines.append(f"7. **Видео:** загрузить файл `assets/videos/{name}.mp4`")
            lines.append(f"   - Если нет файла — снять реальное видео или сгенерить через "
                         f"`python -m scripts.generate_creative_videos --workspace <path> --only {name}`")
        elif "carousel" in fmt:
            lines.append(f"7. **Карусель (3-5 карточек):** загрузить файлы")
            lines.append(f"   - `assets/images/{name}_card1.png` — обложка")
            lines.append(f"   - `assets/images/{name}_card2.png` — превью контента")
            lines.append(f"   - `assets/images/{name}_card3.png` — CTA-карточка")
        else:
            lines.append(f"7. **Изображение:** загрузить `assets/images/{name}.png`")
        lines.append("")

        # Лид-форма
        if cr.get("lead_form"):
            lf = cr["lead_form"]
            lines.append("8. **Лид-форма VK** (для eBook):")
            lines.append(f"   - Поля: {', '.join(lf.get('fields', ['name', 'email']))}")
            lines.append(f"   - Согласие на ПДн: **обязательно ВКЛ**")
            lines.append(f"   - Спасибо-экран: «{lf.get('thanks_screen', 'Спасибо! PDF на почте.')}»")
            lines.append("")
            # PDF для отправки
            pdf_path = workspace / "assets" / "pdfs" / f"{name}.pdf"
            lines.append(f"9. **PDF для автоотправки на email** (после заполнения формы):")
            lines.append(f"   - Файл: `assets/pdfs/{name}.pdf`")
            lines.append(f"   - В кабинете VK Реклама нет встроенной автоотправки PDF. Варианты:")
            lines.append(f"     a) В спасибо-экране дать прямую ссылку на скачивание PDF "
                         f"(залить PDF на сайт продукта или Google Drive с публичной ссылкой)")
            lines.append(f"     b) Интегрировать email-сервис (Mindbox / SendPulse / Unisender / "
                         f"AmoCRM) через webhook VK Реклама → отправлять PDF с него")
            lines.append("")
        lines.append("---")
        lines.append("")

    # ============ ШАГ 5: ЧЕК-ЛИСТ ============
    lines.append("## Шаг 5. Финальный чек-лист перед активацией")
    lines.append("")
    lines.append("**Перед тем как нажать «Запустить»:**")
    lines.append("")
    lines.append("- [ ] Все баннеры прошли модерацию VK (статус **«Принят»**, не «На проверке» или «Отклонён»)")
    lines.append("- [ ] Если что-то отклонено — прочитайте причину, переформулируйте текст, перезалейте")
    lines.append("- [ ] Пиксель установлен на сайте, активность зелёная за последние 24 часа")
    lines.append("- [ ] События пикселя ловятся (проверьте: откройте сайт в инкогнито, пройдите регистрацию, "
                 "посмотрите что событие появилось в кабинете → Сайты → Активность)")
    lines.append("- [ ] Цели созданы в кабинете и замаплены на события пикселя")
    lines.append("- [ ] Кампания оптимизируется на правильную цель (микро-цель `signup_started`, не основную `signup_completed` — иначе данных будет мало)")
    lines.append("- [ ] UTM-метки на всех URL баннеров (проверьте в превью что они работают)")
    lines.append("- [ ] Лендинг открывается с мобильного, форма работает (тест в инкогнито)")
    lines.append("- [ ] ОРД-данные заполнены в Настройках, плашка появляется в превью баннеров")
    lines.append("- [ ] Бюджет на счёте кабинета достаточен (минимум на 14 дней)")
    lines.append("- [ ] Есть план мониторинга на первые 3 дня (см. `lifecycle-runbook.md` Сценарий 11 — daily check)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ============ ШАГ 6: АКТИВАЦИЯ ============
    lines.append("## Шаг 6. Активация и первые часы")
    lines.append("")
    lines.append("**Когда чек-лист пройден:**")
    lines.append("")
    lines.append("1. **Активируйте группы объявлений:** в каждой группе переключатель «На паузе» → «Активна»")
    lines.append("2. **Активируйте кампанию:** переключатель в шапке кампании")
    lines.append("3. **Первые 2-4 часа:** алгоритм VK начинает откручивать. Не пугайтесь если показов мало — это «обучение»")
    lines.append("4. **Через 24 часа:** Claude → «Daily check VK» → даст сводку за вчера")
    lines.append("")
    lines.append("**Если CPL > потолка через первые 12 часов:** не паникуйте, дайте алгоритму 3-5 дней")
    lines.append("обучиться. Если через 5 дней CPL ≥ 2× от плана — Claude → «Прошла неделя, оптимизация».")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Что делать если что-то не получается")
    lines.append("")
    lines.append("**Модерация отклонила баннер**")
    lines.append("- Прочитайте причину в кабинете")
    lines.append("- Скиньте Claude в чат «модерация отклонила X с причиной Y» → переформулируем")
    lines.append("")
    lines.append("**Пиксель не стреляет**")
    lines.append("- Откройте DevTools на сайте → Network → ищите запросы к `top-fwz1.mail.ru/counter`")
    lines.append("- Если их нет — код пикселя не установлен или установлен не на ВСЕХ страницах")
    lines.append("- См. `07a_pixel_setup.md` для чек-листа отладки")
    lines.append("")
    lines.append("**Кабинет ругается на ОРД**")
    lines.append("- Заполните Настройки → ОРД до конца (обязательно ИНН + email)")
    lines.append("- Если ИНН иностранный — нужен ИП-агент в РФ")
    lines.append("")
    lines.append("**Нужна помощь Claude по любому шагу**")
    lines.append("- Просто опиши проблему в чате — Claude разберёт по runbook")
    lines.append("")

    return "\n".join(lines)


def write_docx(md_text: str, output_path: Path) -> bool:
    """Конвертирует MD в простой DOCX через python-docx."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return False

    doc = Document()
    for line in md_text.split("\n"):
        if not line.strip():
            doc.add_paragraph()
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=0)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=2)
        elif line.startswith("- ") or line.startswith("  - "):
            doc.add_paragraph(line.lstrip("- ").strip(), style="List Bullet")
        elif line.startswith("---"):
            doc.add_paragraph("─" * 50)
        else:
            doc.add_paragraph(line)

    doc.save(output_path)
    return True


def main():
    parser = argparse.ArgumentParser(description="M5: генератор LAUNCH_GUIDE для ручного залива в VK")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--docx", action="store_true", help="Также сохранить как .docx")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"ERROR: {workspace} не существует", file=sys.stderr)
        return 1

    if not (workspace / "creatives.json").exists():
        print(f"ERROR: не найден creatives.json в {workspace}", file=sys.stderr)
        return 1

    guide = build_guide(workspace)

    md_path = workspace / "LAUNCH_GUIDE.md"
    md_path.write_text(guide, encoding="utf-8")
    print(f"✓ {md_path} ({len(guide)} знаков)")

    if args.docx:
        docx_path = workspace / "LAUNCH_GUIDE.docx"
        if write_docx(guide, docx_path):
            print(f"✓ {docx_path}")
        else:
            print("WARN: python-docx не установлен, .docx не создан", file=sys.stderr)

    creatives = load_creatives(workspace)
    audiences = load_audiences(workspace)
    print(f"\nГайд покрывает:")
    print(f"  - {len(audiences)} аудиторий (групп объявлений)")
    print(f"  - {len(creatives)} креативов (баннеров)")
    print(f"\nГотов отдать оператору / клиенту для ручного залива в ads.vk.ru.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
